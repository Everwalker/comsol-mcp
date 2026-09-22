"""Public-MCP C11/C14 dataset and probe cases for an already bound Chain-A model.

The caller owns the MCP host and has already bound a freshly solved Chain-A model.  This
module never starts COMSOL, creates a model, imports a worker, or calls a private model API.
It sends only the published ``dataset.*``, ``result.table_manage`` and ``probe.*`` actions.

The cases are deliberately evidence driven:

* every request has a unique idempotency key, a body hash, and ``reconcile=False``;
* successful mutations require the product's ``APPLIED`` envelope and readable post-write
  data, followed by an independent get/inspect where the operation has one;
* an error code by itself is not a PASS.  A refusal is accepted only when the response is a
  non-success envelope with a bounded validation/no-dispatch witness;
* ``NO_HISTORY`` and unavailable 3-D fixtures remain visible as NOT_RUN/BLOCKED; this helper
  never creates an empty or echoed table to manufacture history evidence.

``run_node_probe_cases`` accepts an optional ``execute_java`` callback solely for a root-owned
3-D fixture.  The default Chain-A fixture is two-dimensional, so the CutPlane case is recorded
as NOT_RUN until the caller supplies a real solved 3-D descriptor.  The callback contract is
documented by :data:`CUTPLANE_FIXTURE_REQUEST`; no Java is executed by this module itself.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))
import phase4_run_mcp as protocol  # noqa: E402


STATUSES = {"PASS", "FAIL", "BLOCKED", "NOT_RUN", "UNVERIFIED"}
_KEY_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")
_TERMINAL_JOB_STATUSES = frozenset({
    "SUCCEEDED", "SUCCESS", "FAILED", "FAILURE", "ERROR", "CANCELLED", "CANCELED",
    "TERMINAL", "COMPLETE", "COMPLETED", "DONE", "ABORTED", "RELEASED",
})
_ENVIRONMENT_ERROR_CODES = frozenset({
    "API_UNSUPPORTED", "UNSUPPORTED_OPERATION", "ENGINE_UNAVAILABLE", "LICENSE_UNAVAILABLE",
    "COMSOL_UNAVAILABLE", "WORKER_UNAVAILABLE", "MODEL_NOT_BOUND", "NO_MODEL",
})


class _TransientProbePrecondition(Exception):
    """Stop the transient case before owned mutations after a bounded precondition failure."""


# This is a request descriptor, rather than an engine claim.  A root driver may use it to
# select a reviewed native 3-D fixture and return ``fixture_ready=True`` with a solved dataset.
# The helper intentionally does not guess a COMSOL geometry/physics/solver recipe.
CUTPLANE_FIXTURE_REQUEST: dict[str, Any] = {
    "kind": "temporary_solved_3d_cutplane_fixture",
    "required_return": {
        "fixture_ready": True,
        "dataset": "a Solution dataset tag readable by dataset.list",
        "component": "a component tag containing the dataset geometry",
        "geometry": "optional geometry tag; if supplied it must be readable",
    },
    "execute_java_signature": "await execute_java(name, source, arguments=None)",
    "safety": "root-owned fixture only; no existing Chain-A node may be relabelled",
}


# Read-only javap/API review list for the native implementation behind the public routes.  The
# helper never invokes these names directly; they are the allowlist root should compare with the
# Worker Java catalogue before a live run.  ``genResult`` is intentionally limited to explicit
# probe create/update preparation and is absent from ``probe.history``.
NATIVE_METHOD_ALLOWLIST: dict[str, tuple[str, ...]] = {
    "dataset": (
        "DatasetFeatureList.create(String,String)", "DatasetFeature.set(String,Object)",
        "PropFeature.getString(String)", "PropFeature.getDouble(String)",
        "PropFeature.getDoubleArray(String)", "PropFeature.getDoubleMatrix(String)",
        "PropFeature.getStringArray(String)", "PropFeature.getStringMatrix(String)",
        "PropFeature.getType()", "PropFeature.properties()",
    ),
    "probe": (
        "ProbeFeatureList.create(String,String)", "ProbeFeature.genResult(String)",
        "ModelEntity.model(String)", "ModelEntity.model()",
        "PropFeature.set(String,Object)", "PropFeature.getString(String)",
        "PropFeature.getDouble(String)", "PropFeature.getStringArray(String)",
        "PropFeature.properties()",
    ),
    "table": (
        "TableBaseFeature.setTableData(double[][])",
        "TableBaseFeature.setTableData(double[][],double[][])",
        "TableBaseFeature.setColumnHeaders(String[])", "TableBaseFeature.getReal()",
        "TableBaseFeature.getImag()", "TableBaseFeature.getColumnHeaders()",
        "TableBaseFeature.getRowHeaders()", "TableBaseFeature.getTableData(boolean)",
        "TableBaseFeature.isComplex()",
    ),
}


def _safe(value: Any) -> Any:
    converter = getattr(protocol, "_json_safe", None)
    if callable(converter):
        try:
            return converter(value)
        except Exception:
            pass
    if isinstance(value, Mapping):
        return {str(key): _safe(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(child) for child in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _data(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        return {}
    value = payload.get("data")
    return dict(value) if isinstance(value, Mapping) else {}


def _success(payload: Mapping[str, Any] | None) -> bool:
    helper = getattr(protocol, "_success", None)
    if callable(helper):
        try:
            return bool(helper(payload))
        except Exception:
            pass
    return isinstance(payload, Mapping) and payload.get("success") is True


def _error_code(payload: Mapping[str, Any] | None) -> str | None:
    helper = getattr(protocol, "_error_code", None)
    if callable(helper):
        try:
            return helper(payload)
        except Exception:
            pass
    error = payload.get("error") if isinstance(payload, Mapping) else None
    if isinstance(error, Mapping) and error.get("code"):
        return str(error["code"])
    return None


def _unknown(payload: Mapping[str, Any] | None) -> bool:
    helper = getattr(protocol, "_unknown_outcome", None)
    if callable(helper):
        try:
            return helper(payload) is not None
        except Exception:
            pass
    data = _data(payload)
    error = payload.get("error") if isinstance(payload, Mapping) else None
    code = error.get("code") if isinstance(error, Mapping) else None
    return bool(data.get("execution_state_unknown") or code == "EXECUTION_STATE_UNKNOWN")


_ACTIVE_JOB_STATUSES = frozenset({"QUEUED", "STARTING", "RUNNING", "ACTIVE", "IN_PROGRESS"})
_SUCCESS_JOB_STATUSES = frozenset({"SUCCEEDED", "SUCCESS", "COMPLETE", "COMPLETED", "DONE"})


def _job_id(payload: Mapping[str, Any] | None) -> str | None:
    if not isinstance(payload, Mapping):
        return None
    execution = payload.get("execution")
    if not isinstance(execution, Mapping):
        execution = _data(payload).get("execution")
    if isinstance(execution, Mapping) and isinstance(execution.get("job_id"), str):
        return execution["job_id"]
    data = _data(payload)
    return data.get("job_id") if isinstance(data.get("job_id"), str) else None


def _job_status(payload: Mapping[str, Any] | None) -> str | None:
    if not isinstance(payload, Mapping):
        return None
    data = _data(payload)
    execution = payload.get("execution")
    if not isinstance(execution, Mapping):
        execution = data.get("execution")
    for source in (data, execution if isinstance(execution, Mapping) else {}, payload):
        value = source.get("status") or source.get("state")
        if isinstance(value, str) and value:
            return value.upper()
    return None


def _slug(value: str) -> str:
    return _KEY_SAFE.sub("-", value).strip(".-")[:64] or "case"


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    partial = path.with_name(f".{path.name}.partial")
    partial.write_text(json.dumps(_safe(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                       encoding="utf-8")
    os.replace(partial, path)


def _body_hash(body: Mapping[str, Any]) -> str:
    helper = getattr(protocol, "_body_sha256", None)
    if callable(helper):
        try:
            return str(helper(body))
        except Exception:
            pass
    return hashlib.sha256(json.dumps(_safe(body), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _path(collection: str, tag: str) -> dict[str, Any]:
    return {"segments": [{"accessor": "result"}, {"collection": collection, "tag": tag}]}


def _probe_path(tag: str, component: str | None = None) -> dict[str, Any]:
    segments: list[dict[str, str]] = []
    if component is not None:
        segments.append({"collection": "component", "tag": component})
    segments.append({"collection": "probe", "tag": tag})
    return {"segments": segments}


def _finite(value: Any) -> bool:
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, Mapping):
        return all(_finite(child) for child in value.values())
    if isinstance(value, (list, tuple)):
        return all(_finite(child) for child in value)
    return True


def _summary(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    data = _data(payload)
    summary: dict[str, Any] = {
        "success": _success(payload),
        "error_code": _error_code(payload),
        "unknown": _unknown(payload),
        "data_keys": sorted(str(key) for key in data),
    }
    for key in ("status", "tag", "type_id", "created", "updated", "removed", "table",
                "headers", "rows", "total_rows", "has_more", "next_cursor", "source",
                "readback", "applied", "failed", "not_executed", "domain_outcome"):
        if key in data:
            summary[key] = _safe(data[key])
    if isinstance(payload, Mapping) and isinstance(payload.get("execution"), Mapping):
        execution = payload["execution"]
        summary["execution"] = {key: _safe(execution.get(key)) for key in (
            "operation_id", "job_id", "request_id", "idempotency_key", "revision", "dispatch_stage"
        ) if key in execution}
    return summary


def _dispatch_witness(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    observed = "unknown"
    product: Any = None
    observed_helper = getattr(protocol, "observed_dispatch_stage", None)
    if callable(observed_helper):
        try:
            observed = observed_helper(payload)
        except Exception:
            observed = "unknown"
    product_helper = getattr(protocol, "product_dispatch_stage", None)
    if callable(product_helper):
        try:
            product = product_helper(payload)
        except Exception:
            product = None
    stages = getattr(protocol, "NOT_EXECUTED_STAGES", frozenset())
    proves = bool(isinstance(product, Mapping) and product.get("proves_not_executed")) or observed in stages
    # Pre-validation responses normally have no data and no successful execution envelope.  This
    # is a bounded contract witness; an UNKNOWN response is never treated as validation.
    if not _unknown(payload) and not _success(payload) and not _data(payload):
        proves = True
    return {"observed_stage": observed, "product": _safe(product), "proves_not_executed": proves}


def _bad_status(payload: Mapping[str, Any] | None, mode: str = "response") -> str:
    """Classify a non-PASS without downgrading an UNKNOWN engine state to BLOCKED."""
    if mode in {"exception", "halted"} and not _unknown(payload):
        return "BLOCKED" if mode == "exception" else "FAIL"
    if _unknown(payload):
        return "FAIL"
    return "BLOCKED" if _error_code(payload) in _ENVIRONMENT_ERROR_CODES else "FAIL"


def _mutation_verified(payload: Mapping[str, Any] | None, *, type_id: str | None = None) -> tuple[bool, dict[str, Any]]:
    data = _data(payload)
    readback = data.get("readback") if isinstance(data.get("readback"), Mapping) else {}
    status = data.get("status")
    status_ok = status == "APPLIED" or (isinstance(status, Mapping) and status.get("ok") is True)
    readable = bool(readback.get("readable"))
    match = readback.get("match", readback.get("readback_match", True)) is not False
    type_ok = type_id is None or data.get("type_id") in (None, type_id) or readback.get("type_id") in (None, type_id)
    assertions = {"success": _success(payload), "status": status, "status_ok": status_ok,
                  "readback_readable": readable, "readback_match": match, "type_match": type_ok,
                  "unknown": _unknown(payload)}
    return bool(_success(payload) and not _unknown(payload) and status_ok and readable and match and type_ok), assertions


def _readable_list(data: Mapping[str, Any], key: str) -> tuple[bool, list[Any]]:
    value = data.get(key)
    if not isinstance(value, list):
        return False, []
    return True, value


def _numeric_values(value: Any) -> list[float]:
    """Flatten only numeric result values; metadata and echoed requests are excluded."""
    if isinstance(value, bool):
        return []
    if isinstance(value, (int, float)):
        number = float(value)
        return [number] if math.isfinite(number) else []
    if isinstance(value, Mapping):
        # ``complex_mode=real`` should return scalars, but accepting a native
        # real wrapper keeps this checker explicit if the public route wraps a
        # scalar in ``{real: ...}``.  Never use an echoed request field here.
        if "real" in value and isinstance(value.get("real"), (int, float)) and not isinstance(value.get("real"), bool):
            return _numeric_values(value["real"])
        values: list[float] = []
        for child in value.values():
            values.extend(_numeric_values(child))
        return values
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        values = []
        for child in value:
            values.extend(_numeric_values(child))
        return values
    return []


async def _dataset_solution_indices(ledger: _NodeLedger, tag: str, label: str,
                                    *, expected_component: str | None = None,
                                    expected_geometry: str | None = None) -> bool:
    """Require a real dataset -> sol1 binding and published solution-axis metadata."""
    key, payload, mode = await ledger.call(
        "dataset.solution_indices", {"path": _path("dataset", tag)}, label,
    )
    data = _data(payload)
    pairs = data.get("solnum_pairs")
    has_axis = bool(data.get("inner_indices") or data.get("outer_indices") or pairs)
    binding = data.get("dataset_binding") if isinstance(data.get("dataset_binding"), Mapping) else {}
    binding_component = binding.get("component")
    binding_geometry = binding.get("geometry")
    binding_match = (
        (expected_component is None or binding_component == expected_component)
        and (expected_geometry is None or binding_geometry == expected_geometry)
    )
    passed = (mode == "response" and _success(payload)
              and data.get("binding_complete") is True
              and data.get("solution") == "sol1" and has_axis and binding_match)
    ledger.add(label, "PASS" if passed else (
        "FAIL" if mode == "response" and _error_code(payload) in {"API_UNSUPPORTED", "METHOD_REJECTED"}
        else _bad_status(payload, mode)),
               operation="dataset.solution_indices", key=key, response=payload,
               assertions={"binding_complete": data.get("binding_complete"),
                           "solution": data.get("solution"),
                           "axis_metadata": has_axis,
                           "dataset_binding": binding,
                           "binding_match": binding_match,
                           "expected_component": expected_component,
                           "expected_geometry": expected_geometry,
                           "inner_indices": data.get("inner_indices"),
                           "outer_indices": data.get("outer_indices"),
                           "solnum_pairs": pairs},
               reason=None if passed else "dataset.solution_indices did not prove a real binding to sol1")
    return passed


async def _evaluate_dataset(ledger: _NodeLedger, tag: str, *, expression: str,
                            aggregate: str, expected: float | None, label: str,
                            tolerance: float = 1e-6) -> tuple[bool, dict[str, Any] | None, str]:
    """Evaluate one real dataset and verify the returned value against an expectation."""
    body = {"spec": {"expressions": [expression],
                      "solution": {"dataset": tag, "solution": "sol1"},
                      "aggregate": aggregate, "complex_mode": "real"}}
    key, payload, mode = await ledger.call("result.evaluate", body, label)
    data = _data(payload)
    values = data.get("values")
    numbers = _numeric_values(values)
    dataset_echo = data.get("dataset") == tag
    finite = bool(numbers) and all(math.isfinite(number) for number in numbers)
    if expected is None:
        value_ok = finite
    elif aggregate == "none" and abs(expected) > 0:
        value_ok = finite and all(math.isclose(number, expected, rel_tol=1e-6, abs_tol=tolerance)
                                  for number in numbers)
    else:
        value_ok = finite and all(math.isclose(number, expected, rel_tol=1e-6, abs_tol=tolerance)
                                  for number in numbers)
    passed = (mode == "response" and _success(payload) and dataset_echo and value_ok)
    if passed:
        status = "PASS"
    elif mode == "response" and _error_code(payload) in {"API_UNSUPPORTED", "METHOD_REJECTED"}:
        # An unavailable core route is an implementation failure for this live
        # acceptance, not an environment BLOCKED result.
        status = "FAIL"
    else:
        status = _bad_status(payload, mode)
    ledger.add(label, status, operation="result.evaluate", key=key, response=payload,
               assertions={"dataset_echo": dataset_echo, "expression": expression,
                           "aggregate": aggregate, "values": values,
                           "numeric_values": numbers, "finite": finite,
                           "expected": expected, "value_match": value_ok},
               reason=None if passed else (
                   "result.evaluate did not return a real value for the requested dataset"
                   if not _success(payload) else
                   "result.evaluate value/dataset readback did not match the registered expectation"))
    return passed, payload, mode


class _NodeLedger:
    def __init__(self, client: Any, run_dir: Path) -> None:
        self.client = client
        self.run_dir = Path(run_dir).expanduser().resolve()
        self.run_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        (self.run_dir / "responses").mkdir(mode=0o700, exist_ok=True)
        self.calls: list[dict[str, Any]] = []
        self.rows: list[dict[str, Any]] = []
        self.counter = 0
        self.halted = False
        self.unknown_observations: list[dict[str, Any]] = []

    def key(self, label: str) -> str:
        self.counter += 1
        return f"g33-node-{self.counter:03d}-{_slug(label)}"

    def flush(self) -> None:
        _write_json(self.run_dir / "g33_node_probe_cases.json", {
            "schema_version": 1,
            "scope": "public_mcp_chain_a_c11_c14",
            "run_dir": str(self.run_dir),
            "calls": self.calls,
            "rows": self.rows,
            "unknown_observations": self.unknown_observations,
            "summary": {status: sum(row["status"] == status for row in self.rows) for status in sorted(STATUSES)},
        })

    def add(self, case: str, status: str, *, operation: str | None = None,
            key: str | None = None, assertions: Mapping[str, Any] | None = None,
            response: Mapping[str, Any] | None = None, reason: str | None = None,
            **extra: Any) -> dict[str, Any]:
        if status not in STATUSES:
            raise ValueError(f"invalid node/probe case status: {status}")
        row: dict[str, Any] = {
            "case": case,
            "status": status,
            "operation": operation,
            "idempotency_key": key,
            "assertions": _safe(dict(assertions or {})),
            "response": _summary(response) if isinstance(response, Mapping) else None,
        }
        if reason:
            row["reason"] = reason
        row.update(_safe(extra))
        self.rows.append(row)
        self.flush()
        return row

    async def call(self, operation: str, body: Mapping[str, Any], label: str,
                   *, require_model: bool = True) -> tuple[str, dict[str, Any] | None, str]:
        key = self.key(label)
        request = f"g33-node-{_slug(label)}"
        record = {
            "operation": operation,
            "idempotency_key": key,
            "request": request,
            "body_sha256": _body_hash(body),
            "require_model": require_model,
            "reconcile": False,
        }
        self.calls.append(record)
        if self.halted:
            record["mode"] = "halted"
            record["skip_reason"] = "a previous UNKNOWN job was not proven quiescent by its published reads"
            self.flush()
            return key, None, "halted"
        self.flush()
        try:
            payload = await self.client.action(operation, dict(body), require_model=require_model,
                                               key=key, request=request, reconcile=False)
        except Exception as exc:  # the absence of a public route is bounded evidence
            record["mode"] = "exception"
            record["exception"] = {"type": type(exc).__name__, "message": str(exc)}
            _write_json(self.run_dir / "responses" / f"{key}.json", {
                "mode": "exception", "operation": operation, "idempotency_key": key,
                "exception": record["exception"],
            })
            self.flush()
            return key, None, "exception"
        safe_payload = _safe(payload)
        _write_json(self.run_dir / "responses" / f"{key}.json", safe_payload)
        record["response"] = _summary(payload)
        record["mode"] = "response"
        mode = "response"
        if _unknown(payload):
            observation = await self._observe_unknown(payload, key, operation)
            record["unknown_observation"] = observation
            self.unknown_observations.append(_safe({"source_key": key, **observation}))
            # A first-hand UNKNOWN is an implementation/runtime defect.  Do not allow another
            # mutation until the original job has a terminal/quiescent published readback.
            self.halted = not (bool(observation.get("quiescent"))
                               and bool(observation.get("model_reconciled")))
            record["continue_allowed"] = not self.halted
            mode = "unknown"
        self.flush()
        return key, dict(payload) if isinstance(payload, Mapping) else None, mode

    async def _observe_unknown(self, payload: Mapping[str, Any], source_key: str,
                               source_operation: str) -> dict[str, Any]:
        """Immediately observe the original UNKNOWN job through published host operations."""
        execution = payload.get("execution") if isinstance(payload, Mapping) else None
        if not isinstance(execution, Mapping):
            execution = _data(payload).get("execution")
        execution = execution if isinstance(execution, Mapping) else {}
        job_id = execution.get("job_id")
        observation: dict[str, Any] = {
            "source_operation": source_operation, "source_key": source_key,
            "job_id": job_id, "reads": [], "quiescent": False,
        }
        host = getattr(self.client, "host", None)
        host_call = getattr(host, "call", None)
        if not isinstance(job_id, str) or not job_id:
            observation["reason"] = "UNKNOWN envelope did not carry a job_id"
            return observation
        if not callable(host_call):
            observation["reason"] = "client.host.call is unavailable for published job observation"
            return observation
        for tool in ("job_status", "job_result", "job_reconcile"):
            follow_key = self.key(f"unknown-{tool}-{job_id}")
            execution_builder = getattr(protocol, "_execution", None)
            execution_body = execution_builder(key=follow_key, request=tool) if callable(execution_builder) else {
                "idempotency_key": follow_key, "request_id": tool,
            }
            request = {"job_id": job_id, "execution": execution_body}
            read: dict[str, Any] = {"tool": tool, "key": follow_key, "body_sha256": _body_hash(request)}
            try:
                observed = await host_call(tool, request, reconcile=False)
                read.update({"success": _success(observed), "error_code": _error_code(observed),
                             "summary": _summary(observed)})
                data = _data(observed)
                status_value = data.get("status") or data.get("state")
                if isinstance(status_value, str) and status_value:
                    read["native_status"] = status_value
                    # Keep the native job record's own status: the caller needs it to
                    # distinguish a completed solve from an applied action token.
                    observation.setdefault("native_status", status_value.upper())
                if isinstance(status_value, str) and status_value.upper() in _TERMINAL_JOB_STATUSES:
                    observation["quiescent"] = True
                if bool(data.get("reconciled_quiescent")):
                    observation["quiescent"] = True
                # The published host reconciliation envelope nests its proof under
                # ``data.metadata``.  Reading only data.reconciled_quiescent falsely
                # halted a live run after a validation refusal, even though the host had
                # already confirmed the original job was quiescent.
                metadata = data.get("metadata")
                if isinstance(metadata, Mapping) and bool(metadata.get("reconciled_quiescent")):
                    observation["quiescent"] = True
                _write_json(self.run_dir / "responses" / f"{follow_key}.json", _safe(observed))
            except Exception as exc:
                read["exception"] = {"type": type(exc).__name__, "message": str(exc)}
                _write_json(self.run_dir / "responses" / f"{follow_key}.json", read)
            self.calls.append({"operation": tool, "kind": "unknown_observation", "job_id": job_id,
                               "idempotency_key": follow_key, "request": tool,
                               "body_sha256": read["body_sha256"], "reconcile": False,
                               "require_model": False, "mode": "response" if "summary" in read else "exception"})
            observation["reads"].append(read)
            self.flush()
        if not observation["quiescent"]:
            observation["reason"] = "job_status/job_result/job_reconcile did not prove a terminal/quiescent state"
            observation["model_reconciled"] = False
            return observation

        # Job quiescence is not the same as the managed model ledger accepting the external
        # change.  Before allowing another mutation, ask the published model_inspect refresh
        # route with the current ModelRef/revision.  This is deliberately a separate public read
        # with a fresh identity and reconcile=False; it must not silently replay the UNKNOWN job.
        current_state = getattr(self.client, "state", {})
        current_ref = current_state.get("ref") if isinstance(current_state, Mapping) else None
        current_revision = current_state.get("revision") if isinstance(current_state, Mapping) else None
        refresh_key = self.key(f"unknown-model-inspect-{job_id}")
        if isinstance(current_ref, Mapping) and callable(host_call):
            execution_builder = getattr(protocol, "_execution", None)
            refresh_execution = (execution_builder(key=refresh_key, request="model_inspect",
                                                   ref=current_ref, revision=current_revision)
                                if callable(execution_builder) else {
                                    "idempotency_key": refresh_key, "request_id": "model_inspect",
                                    "model_ref": dict(current_ref), "expected_revision": current_revision,
                                })
            refresh_request = {"refresh": True, "execution": refresh_execution}
            refresh_read: dict[str, Any] = {"tool": "model_inspect", "key": refresh_key,
                                            "body_sha256": _body_hash(refresh_request)}
            try:
                refreshed = await host_call("model_inspect", refresh_request, reconcile=False)
                refresh_read.update({"success": _success(refreshed), "error_code": _error_code(refreshed),
                                     "summary": _summary(refreshed)})
                refreshed_execution = refreshed.get("execution") if isinstance(refreshed, Mapping) else None
                if not isinstance(refreshed_execution, Mapping):
                    refreshed_data = _data(refreshed)
                    refreshed_execution = refreshed_data.get("execution")
                refreshed_execution = refreshed_execution if isinstance(refreshed_execution, Mapping) else {}
                refresh_read["revision"] = refreshed_execution.get("revision")
                refresh_read["dirty"] = refreshed_execution.get("dirty")
                observation["model_reconciled"] = bool(
                    _success(refreshed)
                    and refreshed_execution.get("dirty") is False
                    and isinstance(refreshed_execution.get("revision"), int)
                    and not isinstance(refreshed_execution.get("revision"), bool)
                )
                observation["model_reconcile"] = refresh_read
                _write_json(self.run_dir / "responses" / f"{refresh_key}.json", _safe(refreshed))
                adopt = getattr(self.client, "_adopt_readback", None)
                if callable(adopt):
                    adopt(refreshed)
            except Exception as exc:
                refresh_read["exception"] = {"type": type(exc).__name__, "message": str(exc)}
                observation["model_reconciled"] = False
                observation["model_reconcile"] = refresh_read
                _write_json(self.run_dir / "responses" / f"{refresh_key}.json", refresh_read)
            self.calls.append({"operation": "model_inspect", "kind": "model_reconciliation",
                               "job_id": job_id, "idempotency_key": refresh_key,
                               "request": "model_inspect", "body_sha256": refresh_read["body_sha256"],
                               "reconcile": False, "require_model": True,
                               "mode": "response" if "summary" in refresh_read else "exception"})
            self.flush()
        else:
            observation["model_reconciled"] = False
            observation["model_reconcile"] = {"available": False,
                                               "reason": "current model_ref or public host.call unavailable"}
        if not observation.get("model_reconciled"):
            observation["reason"] = "job was quiescent but model_inspect(refresh=true) did not prove clean managed state"
        return observation

    def finish(self, *, metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
        summary = {status: sum(row["status"] == status for row in self.rows) for status in sorted(STATUSES)}
        if summary["FAIL"] or summary["UNVERIFIED"]:
            overall = "FAIL"
        elif summary["BLOCKED"]:
            overall = "BLOCKED"
        else:
            overall = "PASS"
        result = {"schema_version": 1, "scope": "public_mcp_chain_a_c11_c14",
                  "run_dir": str(self.run_dir), "calls": self.calls, "rows": self.rows,
                  "unknown_observations": self.unknown_observations,
                  "summary": summary, "overall": overall, "metadata": _safe(dict(metadata or {}))}
        _write_json(self.run_dir / "g33_node_probe_cases.final.json", result)
        return result


async def _wait_for_study_job(ledger: _NodeLedger, payload: Mapping[str, Any],
                              key: str, *, max_polls: int = 12,
                              poll_interval_s: float = 0.5) -> dict[str, Any]:
    """Observe an ACTIVE/UNKNOWN study job until native quiescence is proven.

    ``study.run`` is allowed to return before the solve finishes.  The helper
    therefore uses the same published job_status/job_result/job_reconcile and
    model_inspect(refresh=true) evidence required for an UNKNOWN mutation.  A
    caller never proceeds to history or cleanup while the original job remains
    active or the managed model remains dirty.
    """
    observed = next((dict(row) for row in reversed(ledger.unknown_observations)
                     if row.get("source_key") == key), None)
    if observed is None:
        raw = await ledger._observe_unknown(payload, key, "study.run")
        observed = {"source_key": key, **raw}
        ledger.unknown_observations.append(_safe(observed))
        ledger.flush()
    for _ in range(max(0, int(max_polls))):
        safe = bool(observed.get("quiescent")) and bool(observed.get("model_reconciled"))
        if safe:
            break
        await asyncio.sleep(max(0.0, float(poll_interval_s)))
        raw = await ledger._observe_unknown(payload, key, "study.run")
        observed = {"source_key": key, **raw}
        ledger.unknown_observations.append(_safe(observed))
        ledger.flush()
    ledger.halted = not (bool(observed.get("quiescent"))
                         and bool(observed.get("model_reconciled")))
    observed["polls"] = sum(1 for row in ledger.unknown_observations
                             if isinstance(row, Mapping) and row.get("source_key") == key)
    observed["safe_to_continue"] = not ledger.halted
    ledger.flush()
    return observed


async def _refusal(ledger: _NodeLedger, case: str, operation: str, body: Mapping[str, Any],
                   expected_codes: set[str]) -> bool:
    key, payload, mode = await ledger.call(operation, body, case)
    if mode in {"exception", "halted"} or payload is None:
        status = _bad_status(payload, mode)
        ledger.add(case, status, operation=operation, key=key,
                   response=payload,
                   reason=("public action raised before a response; refusal is unverified"
                           if mode == "exception" else
                           "previous UNKNOWN job was not proven quiescent; operation was correctly halted"))
        return False
    code = _error_code(payload)
    witness = _dispatch_witness(payload)
    passed = (not _success(payload) and code in expected_codes and witness["proves_not_executed"]
              and not _unknown(payload))
    ledger.add(case, "PASS" if passed else _bad_status(payload, mode),
               operation=operation, key=key, response=payload,
               assertions={"rejected": not _success(payload), "expected_codes": sorted(expected_codes),
                            "actual_code": code, "validation_witness": witness["proves_not_executed"]},
               dispatch_witness=witness,
               reason=None if passed else "error code/witness did not prove a pre-mutation refusal")
    return passed


def _inspect_property_value(data: Mapping[str, Any], name: str) -> Any:
    """Extract one value from the independent dataset.inspect property map."""
    properties = data.get("properties")
    if isinstance(properties, Mapping):
        return properties.get(name)
    if isinstance(properties, Sequence) and not isinstance(properties, (str, bytes, bytearray)):
        for row in properties:
            if isinstance(row, Mapping) and row.get("property") == name:
                return row.get("value")
    return None


def _close_value(actual: Any, expected: Any) -> bool:
    if isinstance(actual, bool) or isinstance(expected, bool):
        return type(actual) is type(expected) and actual == expected
    numeric_types = (int, float)
    if isinstance(actual, numeric_types) and isinstance(expected, numeric_types):
        return math.isclose(float(actual), float(expected), rel_tol=1e-9, abs_tol=1e-12)
    # Native COMSOL property readback is allowed to publish scalar numeric
    # properties through the string getter.  Compare a finite numeric string
    # numerically, while retaining exact comparison for enum/text properties.
    if isinstance(actual, str) and isinstance(expected, numeric_types):
        try:
            parsed = float(actual.strip())
        except ValueError:
            return False
        return math.isfinite(parsed) and math.isclose(parsed, float(expected), rel_tol=1e-9, abs_tol=1e-12)
    if isinstance(expected, str) and isinstance(actual, numeric_types):
        try:
            parsed = float(expected.strip())
        except ValueError:
            return False
        return math.isfinite(parsed) and math.isclose(float(actual), parsed, rel_tol=1e-9, abs_tol=1e-12)
    return actual == expected


async def _cutpoint_mid_set_failure(ledger: _NodeLedger, tag: str) -> bool:
    """Exercise ordered dataset setters against a temporary CutPoint2D node.

    The request intentionally uses a valid first property, an allowed property name with an
    invalid COMSOL enum value, and a valid trailing property.  A real engine should therefore
    either report a partial setter (``pointx`` applied, ``method`` failed, ``pointy`` skipped)
    or reject the complete request during prevalidation.  The latter is recorded as
    ``preflight_only`` and is accepted only with a no-dispatch witness plus an independent
    inspect proving that the original values were unchanged.  No alternate guessed enum is
    attempted here.
    """
    body = {
        "path": _path("dataset", tag),
        # Dict insertion order is part of this case: it is the setter order sent to the public
        # route and is retained in the evidence ledger's body hash.
        "definition": {"pointx": 0.015, "method": "g33_invalid_method", "pointy": 999.0},
    }
    key, payload, mode = await ledger.call("dataset.update", body, "C11-CutPoint2D-mid-set-failure")
    if mode in {"exception", "halted"} or payload is None:
        ledger.add("C11-property-failure-stop-readback", _bad_status(payload, mode),
                   operation="dataset.update", key=key, response=payload,
                   reason=("public action raised before a response; ordered setter outcome is unverified"
                           if mode == "exception" else
                           "previous UNKNOWN job was not proven quiescent; setter case was halted"))
        return False

    data = _data(payload)
    applied = data.get("applied") if isinstance(data.get("applied"), list) else []
    failed = data.get("failed") if isinstance(data.get("failed"), list) else []
    not_executed = data.get("not_executed") if isinstance(data.get("not_executed"), list) else []
    applied_names = [step.get("property") for step in applied if isinstance(step, Mapping)]
    failed_names = [step.get("property") for step in failed if isinstance(step, Mapping)]
    skipped_names = [step.get("property") for step in not_executed if isinstance(step, Mapping)]
    # ``applied`` and ``failed`` are separate product arrays, so their local indexes cannot be
    # compared.  The route's ordered contract is established by the requested body order plus
    # the three disjoint outcome lists: pointx applied, method failed, pointy skipped.
    partial_evidence = ("pointx" in applied_names and "method" in failed_names
                        and "pointy" in skipped_names)
    witness = _dispatch_witness(payload)
    preflight_only = not applied and not failed and not not_executed and witness["proves_not_executed"]

    # Independent readback is required in either mode.  If a first-hand UNKNOWN was not
    # reconciled, ledger.call has halted and this read is deliberately not attempted.
    inspect_key, inspected, inspect_mode = await ledger.call(
        "dataset.inspect", {"path": _path("dataset", tag)},
        "C11-CutPoint2D-mid-set-failure-independent-inspect",
    )
    inspect_data = _data(inspected)
    inspect_ok = (inspect_mode in {"response", "unknown"} and inspected is not None
                  and _success(inspected)
                  and _close_value(_inspect_property_value(inspect_data, "pointx"),
                                   0.015 if partial_evidence else 0.025)
                  and _close_value(_inspect_property_value(inspect_data, "pointy"), 0.005)
                  and _inspect_property_value(inspect_data, "method") == "coords")
    initial_unknown = _unknown(payload)
    unknown_observation = next(
        (row for row in reversed(ledger.unknown_observations)
         if isinstance(row, Mapping) and row.get("source_key") == key),
        None,
    )
    # A native partial setter can return an execution-state UNKNOWN after the first
    # property has been applied.  Keep that UNKNOWN visible in the response/ledger, but
    # allow this negative-control case to pass only after the original job reached a
    # terminal/quiescent state and model_inspect(refresh=true) proved the managed model
    # clean.  This never converts UNKNOWN into an ordinary success envelope.
    unknown_reconciled = (not initial_unknown) or (
        isinstance(unknown_observation, Mapping)
        and bool(unknown_observation.get("quiescent"))
        and bool(unknown_observation.get("model_reconciled"))
    )
    if preflight_only:
        passed = inspect_ok
        execution_mode = "preflight_only"
        reason = (None if passed else
                  "invalid enum was refused before dispatch but independent inspect did not prove the original values")
    else:
        passed = partial_evidence and inspect_ok and unknown_reconciled
        execution_mode = "partial_setter"
        reason = (None if passed else
                  "ordered setter did not prove pointx applied, method failed, pointy not_executed, independent readback, and (when UNKNOWN) quiescent model reconciliation")
    ledger.add(
        "C11-property-failure-stop-readback", "PASS" if passed else _bad_status(payload, mode),
        operation="dataset.update", key=key, response=payload,
        assertions={
            "execution_mode": execution_mode,
            "applied": applied,
            "failed": failed,
            "not_executed": not_executed,
            "applied_properties": applied_names,
            "failed_properties": failed_names,
            "not_executed_properties": skipped_names,
            "ordered_partial_evidence": partial_evidence,
            "initial_execution_state_unknown": initial_unknown,
            "unknown_reconciled": unknown_reconciled,
            "unknown_observation_source_key": key if initial_unknown else None,
            "preflight_witness": witness,
            "independent_inspect": {
                "success": _success(inspected),
                "mode": inspect_mode,
                "pointx": _inspect_property_value(inspect_data, "pointx"),
                "method": _inspect_property_value(inspect_data, "method"),
                "pointy": _inspect_property_value(inspect_data, "pointy"),
                "match": inspect_ok,
            },
        },
        independent_inspect_key=inspect_key,
        reason=reason,
    )
    return passed


async def _create_dataset(ledger: _NodeLedger, tag: str, type_id: str,
                          definition: Mapping[str, Any], created: list[str]) -> tuple[dict[str, Any] | None, str | None]:
    body = {"tag": tag, "type_id": type_id, "definition": dict(definition)}
    key, payload, mode = await ledger.call("dataset.create", body, f"dataset-create-{tag}")
    if mode != "response" or payload is None:
        ledger.add(f"dataset-create-{tag}", _bad_status(payload, mode), operation="dataset.create", key=key,
                   reason="public action did not return a response")
        return None, key
    passed, assertions = _mutation_verified(payload, type_id=type_id)
    if passed:
        created.append(tag)
    ledger.add(f"dataset-create-{tag}", "PASS" if passed else _bad_status(payload, mode),
               operation="dataset.create", key=key, response=payload, assertions=assertions,
               reason=None if passed else "dataset mutation lacked APPLIED/readable real readback")
    return payload, key


async def _remove_dataset(ledger: _NodeLedger, tag: str) -> None:
    key, payload, mode = await ledger.call("dataset.remove", {"path": _path("dataset", tag)},
                                           f"dataset-remove-{tag}")
    data = _data(payload)
    passed = mode == "response" and _success(payload) and data.get("removed") is True and data.get("verified_removed") is True
    ledger.add(f"dataset-cleanup-{tag}", "PASS" if passed else _bad_status(payload, mode),
               operation="dataset.remove", key=key, response=payload,
               assertions={"removed": data.get("removed"), "verified_removed": data.get("verified_removed")},
               reason=None if passed else "created dataset cleanup was not verified")


async def _remove_probe(ledger: _NodeLedger, tag: str, component: str) -> None:
    key, payload, mode = await ledger.call("probe.remove", {"path": _probe_path(tag, component)},
                                           f"probe-remove-{tag}")
    data = _data(payload)
    passed = mode == "response" and _success(payload) and data.get("removed") is True and data.get("verified_removed") is True
    ledger.add(f"probe-cleanup-{tag}", "PASS" if passed else _bad_status(payload, mode),
               operation="probe.remove", key=key, response=payload,
               assertions={"removed": data.get("removed"), "verified_removed": data.get("verified_removed")},
               reason=None if passed else "created probe cleanup was not verified")


async def _remove_table(ledger: _NodeLedger, tag: str) -> None:
    key, payload, mode = await ledger.call("result.table_manage", {"action": "remove", "path": _path("table", tag)},
                                           f"table-remove-{tag}")
    data = _data(payload)
    passed = mode == "response" and _success(payload) and data.get("removed") is True and data.get("verified_removed") is True
    ledger.add(f"table-cleanup-{tag}", "PASS" if passed else _bad_status(payload, mode),
               operation="result.table_manage", key=key, response=payload,
               assertions={"removed": data.get("removed"), "verified_removed": data.get("verified_removed")},
               reason=None if passed else "created table cleanup was not verified")


async def _table_tags_for_cleanup(ledger: _NodeLedger) -> tuple[set[str], bool]:
    """Read the native table inventory before issuing owned-table cleanup.

    A COMSOL probe may own its result table and remove that table as part of
    ``probe.remove``.  In that case sending a second remove is a false failure
    (``NODE_NOT_FOUND``), so cleanup records the independently observed absence
    and skips the redundant mutation.  An unreadable inventory remains a
    cleanup failure boundary; it never turns an unknown table state into PASS.
    """
    key, payload, mode = await ledger.call(
        "result.table_manage", {"action": "list"}, "cleanup-table-inventory",
    )
    data = _data(payload)
    readable, items = _readable_list(data, "tables")
    tags = {str(item.get("tag")) for item in items
            if isinstance(item, Mapping) and isinstance(item.get("tag"), str)}
    passed = mode == "response" and _success(payload) and readable
    ledger.add(
        "cleanup-table-inventory",
        "PASS" if passed else _bad_status(payload, mode),
        operation="result.table_manage", key=key, response=payload,
        assertions={"readable": readable, "count": len(items), "tags": sorted(tags)},
        reason=None if passed else "owned-table cleanup could not establish a readable native inventory",
    )
    return tags, passed


def _record_absent_owned_table(ledger: _NodeLedger, tag: str) -> None:
    """Record a verified native cascade without issuing a redundant remove."""
    ledger.add(
        f"table-cleanup-{tag}", "PASS",
        operation="result.table_manage", key=None, response=None,
        assertions={"removed": True, "verified_removed": True,
                    "native_cascade_after_probe_remove": True,
                    "inventory_absent": True},
        reason="owned table was already absent in the native inventory after probe removal",
    )


async def _dataset_tags_for_cleanup(ledger: _NodeLedger) -> tuple[set[str], bool]:
    """Read the native dataset inventory before each owned-dataset removal.

    A derived dataset removal can cascade to a dependent dataset in COMSOL.  The
    next cleanup step must therefore verify the tag is still present before
    issuing another mutation; an already absent tag is a verified native
    cascade, not a failed second remove.
    """
    key, payload, mode = await ledger.call(
        "dataset.list", {}, "cleanup-dataset-inventory",
    )
    data = _data(payload)
    readable, items = _readable_list(data, "datasets")
    tags = {str(item.get("tag")) for item in items
            if isinstance(item, Mapping) and isinstance(item.get("tag"), str)}
    passed = mode == "response" and _success(payload) and readable
    ledger.add(
        "cleanup-dataset-inventory",
        "PASS" if passed else _bad_status(payload, mode),
        operation="dataset.list", key=key, response=payload,
        assertions={"readable": readable, "count": len(items), "tags": sorted(tags)},
        reason=None if passed else "owned-dataset cleanup could not establish a readable native inventory",
    )
    return tags, passed


def _record_absent_owned_dataset(ledger: _NodeLedger, tag: str) -> None:
    """Record a verified native dataset cascade without a redundant remove."""
    ledger.add(
        f"dataset-cleanup-{tag}", "PASS",
        operation="dataset.remove", key=None, response=None,
        assertions={"removed": True, "verified_removed": True,
                    "native_cascade_after_dataset_remove": True,
                    "inventory_absent": True},
        reason="owned dataset was already absent in the native inventory after a dependent removal",
    )


async def _snapshot(ledger: _NodeLedger) -> tuple[dict[str, Any], bool]:
    snapshot: dict[str, Any] = {}
    all_ok = True
    requests = (
        ("datasets", "dataset.list", {}, "datasets"),
        ("numerical", "result.numerical_manage", {"action": "list"}, "features"),
        ("tables", "result.table_manage", {"action": "list"}, "tables"),
        ("probes", "probe.list", {}, "probes"),
    )
    for name, operation, body, field in requests:
        key, payload, mode = await ledger.call(operation, body, f"initial-{name}")
        data = _data(payload)
        ok, items = _readable_list(data, field)
        if mode != "response" or payload is None or not _success(payload) or not ok:
            all_ok = False
            ledger.add(f"initial-inventory-{name}", _bad_status(payload, mode),
                       operation=operation, key=key, response=payload,
                       reason=f"{operation} did not return a readable {field} inventory")
            snapshot[name] = {"items": [], "raw": _safe(data)}
            continue
        if name == "probes":
            identities = {(item.get("component"), item.get("tag")) for item in items if isinstance(item, Mapping)}
            tags = [item.get("tag") for item in items if isinstance(item, Mapping)]
        else:
            identities = {item.get("tag") for item in items if isinstance(item, Mapping)}
            tags = [item.get("tag") for item in items if isinstance(item, Mapping)]
        snapshot[name] = {"items": items, "identities": sorted(identities, key=str), "tags": tags, "raw": _safe(data)}
        ledger.add(f"initial-inventory-{name}", "PASS", operation=operation, key=key, response=payload,
                   assertions={"readable": True, "count": len(items), "tags": tags})
    return snapshot, all_ok


def _new_tag(prefix: str, run_name: str, index: int) -> str:
    # COMSOL tags are intentionally conservative here: letters, numbers and underscore only.
    stem = re.sub(r"[^A-Za-z0-9]", "", run_name)[-12:] or "run"
    return f"g33{prefix}{stem}{index:02d}"


async def _preservation_check(ledger: _NodeLedger, initial: Mapping[str, Any]) -> None:
    final, all_ok = await _snapshot(ledger)
    for name in ("datasets", "numerical", "tables", "probes"):
        before = set(initial.get(name, {}).get("identities", []))
        after = set(final.get(name, {}).get("identities", []))
        preserved = before.issubset(after) if all_ok else False
        ledger.add(f"preserve-user-{name}", "PASS" if preserved else ("BLOCKED" if not all_ok else "FAIL"),
                   assertions={"initial": sorted(before, key=str), "final": sorted(after, key=str),
                                "subset": preserved},
                   reason=None if preserved else "final inventory did not prove preservation of all initial user nodes")


async def _cutplane_case(ledger: _NodeLedger, client: Any, execute_java: Any) -> dict[str, Any]:
    descriptor = getattr(client, "cutplane_fixture", None)
    if not isinstance(descriptor, Mapping) or descriptor.get("fixture_ready") is not True:
        ledger.add("C11-CutPlane-3D", "NOT_RUN",
                   reason="Chain-A is 2-D; root must select a fresh solved 3-D fixture before CutPlane can be exercised",
                   fixture_request=CUTPLANE_FIXTURE_REQUEST)
        return {"status": "NOT_RUN"}
    dataset = descriptor.get("dataset")
    component = descriptor.get("component")
    geometry = descriptor.get("geometry")
    if not isinstance(dataset, str) or not isinstance(component, str):
        ledger.add("C11-CutPlane-3D", "BLOCKED", reason="cutplane_fixture descriptor lacks dataset/component strings",
                   fixture_request=CUTPLANE_FIXTURE_REQUEST)
        return {"status": "BLOCKED"}
    # CutPlane's native definition is bound through ``data``.  Component and
    # geometry are read-only upstream binding facts; sending them as arbitrary
    # CutPlane properties makes the native setter reject ``comp``/``geom``
    # after partially applying the plane coordinates.
    definition: dict[str, Any] = {"data": dataset, "planetype": "quick", "quickplane": "xy",
                                  "quickx": 0.0, "quicky": 0.0, "quickz": 0.005}
    tag = _new_tag("cp", "cutplane", 1)
    payload, key = await _create_dataset(ledger, tag, "CutPlane", definition, [])
    if payload is None:
        ledger.add("C11-CutPlane-3D", "FAIL", reason="CutPlane create was not verified")
        return {"status": "FAIL", "fixture": _safe(descriptor)}
    key2, inspected, mode = await ledger.call("dataset.inspect", {"path": _path("dataset", tag)}, "cutplane-inspect")
    data = _data(inspected)
    passed = mode == "response" and _success(inspected) and data.get("type_id") == "CutPlane" and isinstance(data.get("properties"), Mapping)
    ledger.add("C11-CutPlane-3D-readback", "PASS" if passed else _bad_status(inspected, mode),
               operation="dataset.inspect", key=key2, response=inspected,
               assertions={"type_id": data.get("type_id"), "properties_readable": isinstance(data.get("properties"), Mapping)},
               reason=None if passed else "CutPlane did not have an independent typed readback")
    indices_pass = await _dataset_solution_indices(
        ledger, tag, "C11-CutPlane-3D-solution-indices",
        expected_component=component, expected_geometry=geometry if isinstance(geometry, str) else None,
    )
    # A constant field over a 1 x 1 plane has an integral of one.  This is a
    # native result.evaluate read against the newly created CutPlane dataset;
    # no area or temperature value is inferred from the fixture descriptor.
    area_pass, _area_payload, _area_mode = await _evaluate_dataset(
        ledger, tag, expression="1", aggregate="integral", expected=1.0,
        label="C11-CutPlane-3D-area-evaluate", tolerance=1e-5,
    )
    rollup = passed and indices_pass and area_pass
    ledger.add("C11-CutPlane-3D", "PASS" if rollup else "FAIL",
               assertions={"typed_readback": passed, "solution_indices": indices_pass,
                           "constant_one_area": area_pass,
                           "fixture": _safe(descriptor)},
               reason=None if rollup else "CutPlane was not independently bound and evaluated with native readback")
    await _remove_dataset(ledger, tag)
    return {"status": "PASS" if rollup else "FAIL", "fixture": _safe(descriptor), "tag": tag}


async def run_node_probe_cases(
    client: Any,
    run_dir: Path,
    *,
    execute_java: Any = None,
    include_c11: bool = True,
) -> dict[str, Any]:
    """Run bounded public-MCP C11/C14 cases against an already bound Chain-A model.

    ``execute_java`` is accepted for the root driver's optional 3-D fixture integration.  Its
    normal callback shape is ``await execute_java(name, source, arguments=None)``.  This helper
    does not call it implicitly: a descriptor placed on ``client.cutplane_fixture`` after the
    root has selected/solved the fixture is required, preventing an unreviewed Java mutation.
    ``include_c11=False`` leaves the C11 dataset graph and CutPlane case NOT_RUN while still
    running the independent C14 table/probe case.  This is useful after a C11 Join failure has
    made the current model dirty; the caller must provide a fresh bound model or reconcile it.
    """
    ledger = _NodeLedger(client, Path(run_dir))
    state = getattr(client, "state", {})
    bound = isinstance(state, Mapping) and isinstance(state.get("ref"), Mapping)
    if not bound:
        ledger.add("precondition-bound-model", "BLOCKED", reason="ActionClient has no bound model_ref")
        ledger.add("C11", "NOT_RUN", reason="no bound solved Chain-A model")
        ledger.add("C14", "NOT_RUN", reason="no bound solved Chain-A model")
        return ledger.finish(metadata={"bound_model": False, "execute_java_supplied": callable(execute_java),
                                       "native_method_allowlist": NATIVE_METHOD_ALLOWLIST})

    initial, inventory_ok = await _snapshot(ledger)
    datasets = initial.get("datasets", {}).get("items", [])
    solution = next((item for item in datasets if isinstance(item, Mapping) and item.get("tag") == "dset1"), None)
    solution_ok = isinstance(solution, Mapping) and solution.get("type_id") == "Solution"
    ledger.add("C11-precondition-dset1-solution", "PASS" if solution_ok else "BLOCKED",
               assertions={"dset1_present": isinstance(solution, Mapping), "type_id": solution.get("type_id") if isinstance(solution, Mapping) else None},
               reason=None if solution_ok else "dset1 Solution dataset was not confirmed by dataset.list")
    if not solution_ok:
        ledger.add("C11", "BLOCKED", reason="valid 2-D dataset cases cannot run without dset1 Solution")
        ledger.add("C14", "BLOCKED", reason="probe history/table cases require the bound dset1 solution")
        await _preservation_check(ledger, initial)
        return ledger.finish(metadata={"bound_model": True, "inventory_ok": inventory_ok,
                                       "execute_java_supplied": callable(execute_java), "dset1_solution": False,
                                       "native_method_allowlist": NATIVE_METHOD_ALLOWLIST})

    created_datasets: list[str] = []
    created_tables: list[str] = []
    created_probes: list[tuple[str, str]] = []
    run_name = ledger.run_dir.name
    try:
        # C11: real Solution -> CutPoint2D, CutLine2D and Join graph nodes.  The values are
        # official 6.4 dataset property names, and each successful create is followed by inspect.
        if not include_c11:
            ledger.add("C11", "NOT_RUN", reason="include_c11=False; C14 is being run in an independent model scope")
        else:
            # The remaining C11 body is intentionally kept as one bounded sequence.  If a
            # mutation returns UNKNOWN the ledger halts and no later C11 mutation is sent.
            
            c11_specs = (
                ("cpt", "CutPoint2D", {"data": "dset1", "method": "coords", "pointx": 0.025, "pointy": 0.005}),
                ("line", "CutLine2D", {"data": "dset1", "method": "twopoint", "genpoints": [[0.01, 0.005], [0.04, 0.005]]}),
                ("join", "Join", {"data": "dset1", "data2": "dset1", "method": "difference", "solutions": "all", "solutions2": "all"}),
            )
            created_c11: list[str] = []
            c11_ok = True
            for index, (short, type_id, definition) in enumerate(c11_specs, 1):
                tag = _new_tag(short, run_name, index)
                payload, _key = await _create_dataset(ledger, tag, type_id, definition, created_datasets)
                if payload is None:
                    c11_ok = False
                    continue
                created_c11.append(tag)
                inspect_key, inspected, mode = await ledger.call("dataset.inspect", {"path": _path("dataset", tag)},
                                                                 f"dataset-inspect-{tag}")
                data = _data(inspected)
                props = data.get("properties")
                inspect_ok = (mode == "response" and _success(inspected) and data.get("type_id") == type_id
                              and isinstance(props, Mapping) and data.get("tag") == tag)
                ledger.add(f"dataset-inspect-{tag}", "PASS" if inspect_ok else _bad_status(inspected, mode),
                           operation="dataset.inspect", key=inspect_key, response=inspected,
                           assertions={"type_id": data.get("type_id"), "expected_type": type_id,
                            "properties_readable": isinstance(props, Mapping), "tag": data.get("tag")},
                           reason=None if inspect_ok else "create was not independently confirmed by typed dataset.inspect")
                c11_ok = c11_ok and inspect_ok
                if inspect_ok:
                    indices_ok = await _dataset_solution_indices(
                        ledger, tag, f"C11-{type_id}-solution-indices-{tag}",
                    )
                    c11_ok = c11_ok and indices_ok
                    if type_id == "CutPoint2D":
                        # Chain-A's solved block is registered at 323.15 K.  The
                        # value is accepted only from a native evaluation whose
                        # response identifies this CutPoint dataset.
                        point_eval_ok, _point_eval_payload, _point_eval_mode = await _evaluate_dataset(
                            ledger, tag, expression="T", aggregate="none", expected=323.15,
                            label=f"C11-CutPoint2D-evaluate-323.15-{tag}", tolerance=1e-5,
                        )
                        if not point_eval_ok:
                            # A product that publishes at_points but lacks the
                            # evaluate aggregate path may still be tested against
                            # the same CutPoint.  Never fall back after UNKNOWN or
                            # an exception, because the ledger then must halt.
                            last_row = ledger.rows[-1] if ledger.rows else {}
                            last_response = last_row.get("response") if isinstance(last_row, Mapping) else None
                            last_code = last_response.get("error_code") if isinstance(last_response, Mapping) else None
                            if (_point_eval_mode == "response" and last_code in {"API_UNSUPPORTED", "METHOD_REJECTED"}
                                    and not ledger.halted):
                                at_key, at_payload, at_mode = await ledger.call(
                                    "result.at_points",
                                    {"spec": {"expressions": ["T"],
                                               "solution": {"dataset": tag, "solution": "sol1"},
                                               "complex_mode": "real"},
                                     "points": [{"x": 0.025, "y": 0.005}], "coordinate_unit": "m",
                                     "frame": "spatial"},
                                    f"C11-CutPoint2D-at-points-323.15-{tag}",
                                )
                                at_data = _data(at_payload)
                                at_values = _numeric_values(at_data.get("values"))
                                at_dataset = at_data.get("dataset") == tag
                                at_ok = (at_mode == "response" and _success(at_payload) and at_dataset
                                         and bool(at_values)
                                         and all(math.isclose(value, 323.15, rel_tol=1e-6, abs_tol=1e-5)
                                                 for value in at_values))
                                at_status = "PASS" if at_ok else (
                                    "FAIL" if at_mode == "response" and _error_code(at_payload) in {"API_UNSUPPORTED", "METHOD_REJECTED"}
                                    else _bad_status(at_payload, at_mode))
                                ledger.add(f"C11-CutPoint2D-at-points-323.15-{tag}", at_status,
                                           operation="result.at_points", key=at_key, response=at_payload,
                                           assertions={"dataset_echo": at_dataset, "values": at_values,
                                                       "value_match": at_ok},
                                           reason=None if at_ok else "result.at_points did not return 323.15 for the CutPoint dataset")
                                point_eval_ok = at_ok
                        c11_ok = c11_ok and point_eval_ok
                    elif type_id == "CutLine2D":
                        # COMSOL 6.4 defines CutLine2D.genpoints as one row per
                        # point.  The native average is evaluated against this
                        # newly created CutLine dataset, proving the line
                        # coordinates were accepted by the engine rather than
                        # merely matching a property echo.
                        line_eval_ok, _line_eval_payload, _line_eval_mode = await _evaluate_dataset(
                            ledger, tag, expression="T", aggregate="average", expected=323.15,
                            label=f"C11-CutLine2D-evaluate-average-323.15-{tag}", tolerance=1e-5,
                        )
                        c11_ok = c11_ok and line_eval_ok
                    elif type_id == "Join":
                        # COMSOL's Join feature is not accepted by the raw Eval setData
                        # route on the reviewed 6.4 worker.  Use the public point-evaluation
                        # route against this Join dataset at three interior points, and keep
                        # any raw-evaluate refusal as a separate negative control instead of
                        # silently evaluating dset1.
                        join_key, join_payload, join_mode = await ledger.call(
                            "result.at_points",
                            {"spec": {"expressions": ["T"],
                                       "solution": {"dataset": tag, "solution": "sol1"},
                                       "complex_mode": "real"},
                             "points": [{"x": 0.01, "y": 0.005},
                                        {"x": 0.025, "y": 0.005},
                                        {"x": 0.04, "y": 0.005}],
                             "coordinate_unit": "m", "frame": "spatial"},
                            f"C11-Join-difference-zero-at-points-{tag}",
                        )
                        join_data = _data(join_payload)
                        join_values = _numeric_values(join_data.get("values"))
                        response_dataset = join_data.get("dataset")
                        response_dataset_location = "data" if "dataset" in join_data else None
                        if response_dataset is None and isinstance(join_payload, Mapping):
                            top_level_dataset = join_payload.get("dataset")
                            if top_level_dataset is not None:
                                response_dataset = top_level_dataset
                                response_dataset_location = "top_level"
                        response_dataset_field_present = response_dataset_location is not None
                        # The reviewed public at_points route currently returns the native
                        # values/coordinate readback but may omit a top-level dataset echo.
                        # The request is still bound to this Join by the preceding
                        # dataset.solution_indices proof.  Accept the missing echo only with
                        # that prior binding; reject an explicit wrong echo so an upstream
                        # dset1 result cannot masquerade as the Join evaluation.
                        dataset_provenance_ok = bool(indices_ok) and (
                            response_dataset is None or response_dataset == tag
                        )
                        join_points_ok = (join_mode == "response" and _success(join_payload)
                                          and dataset_provenance_ok
                                          and len(join_values) == 3
                                          and all(math.isclose(value, 0.0, abs_tol=1e-6)
                                                  for value in join_values))
                        ledger.add(f"C11-Join-difference-zero-at-points-{tag}",
                                   "PASS" if join_points_ok else _bad_status(join_payload, join_mode),
                                   operation="result.at_points", key=join_key, response=join_payload,
                                   assertions={"dataset": join_data.get("dataset"),
                                               "dataset_provenance": {
                                                   "requested_dataset": tag,
                                                   "response_dataset": response_dataset,
                                                   "response_dataset_field_present": response_dataset_field_present,
                                                   "response_dataset_location": response_dataset_location,
                                                   "binding_precheck": bool(indices_ok),
                                                   "accepted_basis": (
                                                       "response_dataset_and_prior_binding"
                                                       if response_dataset is not None
                                                       else "request_dataset_and_prior_binding"
                                                   ),
                                               },
                                               "dataset_provenance_ok": dataset_provenance_ok,
                                               "points": 3, "values": join_values,
                                               "all_zero": join_points_ok},
                                   reason=None if join_points_ok else
                                   "Join at_points did not return three zero differences with a bound Join dataset")
                        raw_key, raw_payload, raw_mode = await ledger.call(
                            "result.evaluate",
                            {"spec": {"expressions": ["T"],
                                       "solution": {"dataset": tag, "solution": "sol1"},
                                       "aggregate": "none", "complex_mode": "real"}},
                            f"C11-Join-raw-evaluate-negative-{tag}",
                        )
                        raw_code = _error_code(raw_payload)
                        raw_witness = _dispatch_witness(raw_payload)
                        raw_negative = (raw_mode == "response" and not _success(raw_payload)
                                        and raw_code in {"API_UNSUPPORTED", "METHOD_REJECTED"}
                                        and raw_witness["proves_not_executed"])
                        ledger.add(f"C11-Join-raw-evaluate-negative-{tag}",
                                   "PASS" if raw_negative else _bad_status(raw_payload, raw_mode),
                                   operation="result.evaluate", key=raw_key, response=raw_payload,
                                   assertions={"error_code": raw_code,
                                               "expected_unsupported": True,
                                               "no_mutation_witness": raw_witness["proves_not_executed"]},
                                   dispatch_witness=raw_witness,
                                   reason=None if raw_negative else
                                   "raw Join result.evaluate was not an explicit no-mutation unsupported refusal")
                        c11_ok = c11_ok and join_points_ok
    
            wrong_path = {"segments": [{"accessor": "result"}, {"collection": "geometry", "tag": "geom1"}]}
            wrong_collection_ok = await _refusal(ledger, "C11-wrong-collection-rejected", "dataset.inspect", {"path": wrong_path},
                                                 {"INVALID_NODE_PATH", "NODE_NOT_FOUND", "AMBIGUOUS_NODE_PATH"})
            wrong_comp_tag = _new_tag("badcomp", run_name, 1)
            bad_comp_payload_key, bad_comp_payload, bad_comp_mode = await ledger.call(
                "dataset.create", {"tag": wrong_comp_tag, "type_id": "CutPoint2D",
                                    "definition": {"data": "dset1", "comp": "__g33_missing_component__",
                                                    "pointx": 0.025, "pointy": 0.005}}, "C11-wrong-component")
            bad_comp_code = _error_code(bad_comp_payload)
            bad_comp_pass = (bad_comp_mode == "response" and not _success(bad_comp_payload)
                             and bad_comp_code in {"NODE_NOT_FOUND", "INVALID_REQUEST", "API_UNSUPPORTED"}
                             and _dispatch_witness(bad_comp_payload)["proves_not_executed"])
            ledger.add("C11-wrong-component-rejected", "PASS" if bad_comp_pass else _bad_status(bad_comp_payload, bad_comp_mode),
                       operation="dataset.create", key=bad_comp_payload_key, response=bad_comp_payload,
                       assertions={"error_code": bad_comp_code, "created_tag_absent": True},
                       reason=None if bad_comp_pass else "wrong component was not proven to stop before mutation")
    
            # Graph cycle: temporary Join A/B first, then two typed updates.  The second update must
            # be refused before any setter and leave both nodes available for cleanup.
            cycle_a = _new_tag("cyca", run_name, 1)
            cycle_b = _new_tag("cycb", run_name, 1)
            await _create_dataset(ledger, cycle_a, "Join", {"data": "dset1", "data2": "dset1"}, created_datasets)
            await _create_dataset(ledger, cycle_b, "Join", {"data": "dset1", "data2": "dset1"}, created_datasets)
            update_key, update_a, update_mode = await ledger.call("dataset.update", {
                "path": _path("dataset", cycle_a), "definition": {"data": cycle_b}}, "C11-cycle-first-edge")
            update_a_ok = update_mode == "response" and _success(update_a) and _data(update_a).get("status") == "APPLIED"
            ledger.add("C11-cycle-first-edge", "PASS" if update_a_ok else _bad_status(update_a, update_mode),
                       operation="dataset.update", key=update_key, response=update_a,
                       assertions={"first_edge_applied": update_a_ok},
                       reason=None if update_a_ok else "first temporary graph edge did not have verified APPLIED readback")
            cycle_key, cycle_payload, cycle_mode = await ledger.call("dataset.update", {
                "path": _path("dataset", cycle_b), "definition": {"data": cycle_a}}, "C11-cycle-rejected")
            cycle_code = _error_code(cycle_payload)
            cycle_witness = _dispatch_witness(cycle_payload)
            cycle_pass = (cycle_mode == "response" and not _success(cycle_payload)
                          and cycle_code == "DATASET_CYCLE_DETECTED" and cycle_witness["proves_not_executed"])
            ledger.add("C11-cycle-rejected", "PASS" if cycle_pass else _bad_status(cycle_payload, cycle_mode),
                       operation="dataset.update", key=cycle_key, response=cycle_payload,
                       assertions={"error_code": cycle_code, "expected": "DATASET_CYCLE_DETECTED",
                                    "no_mutation_witness": cycle_witness["proves_not_executed"]},
                       dispatch_witness=cycle_witness,
                       reason=None if cycle_pass else "dataset cycle was not rejected with a no-mutation witness")
            # An invalid property is deliberately a prevalidation/no-mutation case.
            invalid_property_ok = await _refusal(ledger, "C11-invalid-property-prevalidation", "dataset.update", {
                "path": _path("dataset", created_c11[0] if created_c11 else cycle_a),
                "definition": {"__g33_unknown_property__": 1}}, {"INVALID_REQUEST"})
            mid_set_failure_ok = False
            if created_c11:
                mid_set_failure_ok = await _cutpoint_mid_set_failure(ledger, created_c11[0])
            else:
                ledger.add("C11-property-failure-stop-readback", "NOT_RUN",
                           reason="CutPoint2D was not created and independently verified")
            cutplane = await _cutplane_case(ledger, client, execute_java)
            c11_rollup = (c11_ok and cycle_pass and wrong_collection_ok and bad_comp_pass
                          and invalid_property_ok and mid_set_failure_ok and solution_ok)
            ledger.add("C11", "PASS" if c11_rollup else "FAIL",
                       assertions={"valid_cutpoint_cutline_join": c11_ok, "cycle_rejected": cycle_pass,
                                   "mid_set_failure_stop_readback": mid_set_failure_ok,
                                   "cutplane": cutplane.get("status", "NOT_RUN") if isinstance(cutplane, Mapping) else "NOT_RUN"})
    
        # C14: keep a complex table as an independent preservation sentinel, then use a
        # *separate empty table* for the probe.  The sentinel's hand-written values are never
        # linked to the probe and therefore cannot masquerade as generated probe history.
        sentinel_table_tag = _new_tag("sentinel", run_name, 1)
        table_body = {"action": "create", "path": _path("table", sentinel_table_tag), "definition": {
            "type_id": "Table", "data": [[1.0, 2.0], [3.0, 4.0]],
            "imaginary": [[0.1, 0.2], [0.3, 0.4]], "headers": ["T", "u"]}}
        table_key, table_payload, table_mode = await ledger.call(
            "result.table_manage", table_body, "C14-complex-table-sentinel-create",
        )
        sentinel_pass, table_assertions = _mutation_verified(table_payload, type_id="Table")
        if sentinel_pass:
            created_tables.append(sentinel_table_tag)
        ledger.add("C14-complex-table-sentinel-create", "PASS" if sentinel_pass else _bad_status(table_payload, table_mode),
                   operation="result.table_manage", key=table_key, response=table_payload, assertions=table_assertions,
                   reason=None if sentinel_pass else "complex sentinel table create lacked verified native readback")
        table_get_key, table_get, table_get_mode = await ledger.call(
            "result.table_manage", {"action": "get", "path": _path("table", sentinel_table_tag)},
            "C14-complex-table-sentinel-get",
        )
        table_data = _data(table_get)
        real_rows = table_data.get("data")
        imag_rows = table_data.get("imaginary")
        sentinel_get_pass = (table_get_mode == "response" and _success(table_get)
                          and real_rows == [[{"real": 1.0, "imag": 0.1}, {"real": 2.0, "imag": 0.2}],
                                             [{"real": 3.0, "imag": 0.3}, {"real": 4.0, "imag": 0.4}]]
                          and imag_rows == [[0.1, 0.2], [0.3, 0.4]]
                          and table_data.get("headers") == ["T", "u"] and _finite(table_data))
        ledger.add("C14-complex-table-sentinel-get", "PASS" if sentinel_get_pass else _bad_status(table_get, table_get_mode),
                   operation="result.table_manage", key=table_get_key, response=table_get,
                   assertions={"independent_real_imag_readback": sentinel_get_pass, "headers": table_data.get("headers"),
                                "finite": _finite(table_data)},
                   reason=None if sentinel_get_pass else "complex sentinel get did not return the native real/imag/header values")

        # This table starts empty.  A successful probe history must contain values generated by
        # the native probe result path after genResult; the helper never seeds it with expected
        # temperatures or copies the sentinel's rows.
        history_table_tag = _new_tag("hist", run_name, 1)
        history_table_key, history_table_payload, history_table_mode = await ledger.call(
            "result.table_manage", {
                "action": "create", "path": _path("table", history_table_tag),
                "definition": {"type_id": "Table", "data": [], "headers": ["T"]},
            }, "C14-probe-history-table-create-empty",
        )
        history_table_pass, history_table_assertions = _mutation_verified(
            history_table_payload, type_id="Table",
        )
        if history_table_pass:
            created_tables.append(history_table_tag)
        ledger.add(
            "C14-probe-history-table-create-empty",
            "PASS" if history_table_pass else _bad_status(history_table_payload, history_table_mode),
            operation="result.table_manage", key=history_table_key, response=history_table_payload,
            assertions=history_table_assertions,
            reason=None if history_table_pass else "empty history table create lacked verified native readback",
        )

        probe_tag = _new_tag("probe", run_name, 1)
        probe_definition = {"properties": {"expr": "T", "unit": "K", "table": history_table_tag},
                            "selection": {"kind": "explicit", "component": "comp1", "geometry": "geom1",
                                          "entity_dimension": 2, "entities": [1]}}
        probe_key, probe_payload, probe_mode = await ledger.call("probe.create", {
            "tag": probe_tag, "type_id": "DomainProbe", "definition": {**probe_definition, "component": "comp1"},
        }, "C14-domain-probe-create")
        probe_pass, probe_assertions = _mutation_verified(probe_payload, type_id="DomainProbe")
        update_pass = False
        empty_history_pass = False
        study_pass = False
        history_pass = False
        if probe_pass:
            created_probes.append((probe_tag, "comp1"))
        ledger.add("C14-domain-probe-create", "PASS" if probe_pass else _bad_status(probe_payload, probe_mode),
                   operation="probe.create", key=probe_key, response=probe_payload, assertions=probe_assertions,
                   reason=None if probe_pass else "DomainProbe create lacked verified native type/property readback")

        if probe_pass and history_table_pass:
            update_key, update_payload, update_mode = await ledger.call("probe.update", {
                "path": _probe_path(probe_tag, "comp1"),
                "definition": {"properties": {"expr": "T", "table": history_table_tag},
                               "gen_result": {"solution": "sol1"}},
            }, "C14-probe-update-gen-result")
            update_pass, update_assertions = _mutation_verified(update_payload, type_id="Domain")
            applied_steps = _data(update_payload).get("applied", [])
            gen_called = any(isinstance(step, Mapping) and step.get("step") == "genResult" for step in applied_steps)
            update_pass = update_pass and gen_called
            update_assertions["genResult_applied"] = gen_called
            ledger.add("C14-probe-update-gen-result", "PASS" if update_pass else _bad_status(update_payload, update_mode),
                       operation="probe.update", key=update_key, response=update_payload, assertions=update_assertions,
                       reason=None if update_pass else "probe update did not prove property readback plus explicit genResult")

            # ``genResult`` creates/prepares the native result feature; COMSOL 6.4 may leave
            # its linked table empty until the owning study is actually solved.  Establish that
            # empty state first so a later nonempty history cannot be mistaken for a pre-seeded
            # table or an echo of the request.
            empty_key, empty_payload, empty_mode = await ledger.call("probe.history", {
                "path": _probe_path(probe_tag, "comp1"),
                "solution": {"dataset": "dset1", "solution": "sol1"},
            }, "C14-probe-history-empty-before-study")
            empty_history = _data(empty_payload)
            empty_source = empty_history.get("source") if isinstance(empty_history.get("source"), Mapping) else {}
            empty_rows = empty_history.get("rows")
            empty_history_pass = (empty_mode == "response" and _success(empty_payload)
                                  and isinstance(empty_rows, list) and not empty_rows
                                  and empty_history.get("table") == history_table_tag
                                  and empty_history.get("table") != sentinel_table_tag
                                  and empty_source.get("real_table_readback") is True
                                  and empty_history.get("readback", {}).get("real_readable") is True)
            ledger.add(
                "C14-probe-history-empty-before-study",
                "PASS" if empty_history_pass else _bad_status(empty_payload, empty_mode),
                operation="probe.history", key=empty_key, response=empty_payload,
                assertions={"rows_empty": isinstance(empty_rows, list) and not empty_rows,
                            "history_table": empty_history.get("table"),
                            "real_table_readback": empty_source.get("real_table_readback"),
                            "readback_real_readable": empty_history.get("readback", {}).get("real_readable")},
                reason=None if empty_history_pass else
                "genResult-prepared linked table was not independently confirmed empty",
            )

            study_pass = False
            study_key: str | None = None
            study_payload: Mapping[str, Any] | None = None
            study_mode = "not_run"
            study_observation: Mapping[str, Any] = {}
            if empty_history_pass:
                study_key, study_reply, study_mode = await ledger.call("study.run", {
                    "study": {"segments": [{"collection": "study", "tag": "std1"}]},
                }, "C14-study-run-after-gen-result")
                study_payload = study_reply
                status = _job_status(study_reply)
                job_id = _job_id(study_reply)
                active = status in _ACTIVE_JOB_STATUSES or (
                    job_id is not None and status not in _TERMINAL_JOB_STATUSES
                )
                if mode_is_unknown := _unknown(study_reply):
                    active = True
                if active and study_reply is not None and study_key is not None:
                    study_observation = await _wait_for_study_job(ledger, study_reply, study_key)
                study_safe = (not active or
                              (bool(study_observation.get("quiescent"))
                               and bool(study_observation.get("model_reconciled"))))
                study_pass = (study_mode == "response" and _success(study_reply)
                              and not mode_is_unknown and study_safe)
                ledger.add(
                    "C14-study-run-after-gen-result",
                    "PASS" if study_pass else _bad_status(study_reply, study_mode),
                    operation="study.run", key=study_key, response=study_reply,
                    assertions={"status": status, "job_id": job_id, "active": active,
                                "quiescent": study_observation.get("quiescent"),
                                "model_reconciled": study_observation.get("model_reconciled"),
                                "polls": study_observation.get("polls"),
                                "safe_to_continue": study_safe,
                                "unknown": mode_is_unknown},
                    reason=None if study_pass else
                    "study.run did not complete with a quiescent reconciled native job",
                )
            else:
                ledger.add("C14-study-run-after-gen-result", "NOT_RUN",
                           reason="empty linked history table was not verified before study.run")

            history_key: str | None = None
            history_payload: Mapping[str, Any] | None = None
            history_mode = "not_run"
            history = {}
            if study_pass:
                history_key, history_payload, history_mode = await ledger.call("probe.history", {
                    "path": _probe_path(probe_tag, "comp1"),
                    "solution": {"dataset": "dset1", "solution": "sol1"},
                }, "C14-probe-history-after-study")
            history = _data(history_payload)
            source = history.get("source") if isinstance(history.get("source"), Mapping) else {}
            rows = history.get("rows")
            history_numbers = _numeric_values(rows)
            history_value_pass = (bool(history_numbers)
                                  and all(math.isclose(value, 323.15, rel_tol=1e-6, abs_tol=1e-5)
                                          for value in history_numbers))
            history_pass = (history_mode == "response" and _success(history_payload)
                            and isinstance(rows, list) and len(rows) > 0
                            and source.get("real_table_readback") is True
                            and source.get("gen_result_called") is False
                            and isinstance(history.get("headers"), list)
                            and history.get("readback", {}).get("real_readable") is True
                            and _finite(rows) and history.get("table") == history_table_tag
                            and history.get("table") != sentinel_table_tag
                            and history_value_pass)
            ledger.add("C14-probe-history-real-table", "PASS" if history_pass else (
                           _bad_status(history_payload, history_mode)
                           if history_mode != "not_run" else "NOT_RUN"),
                       operation="probe.history", key=history_key, response=history_payload,
                       assertions={"rows_nonempty": isinstance(rows, list) and bool(rows),
                                    "real_table_readback": source.get("real_table_readback"),
                                    "gen_result_called": source.get("gen_result_called"),
                                    "history_table": history.get("table"),
                                    "sentinel_table": sentinel_table_tag,
                                    "headers": history.get("headers"), "unit": history.get("unit"),
                                    "time": history.get("time"), "parameters": history.get("parameters"),
                                    "finite_rows": _finite(rows), "numeric_values": history_numbers,
                                    "expected_temperature": 323.15,
                                    "temperature_match": history_value_pass},
                       reason=None if history_pass else (
                           "history did not prove native generated rows for the separate linked table at T=323.15 K; NO_HISTORY/seeded data is not success"
                           if history_mode != "not_run" else "study.run did not complete safely"))
        else:
            reason = ("probe.create was not verified" if not probe_pass
                      else "separate empty history table was not verified")
            ledger.add("C14-probe-update-gen-result", "NOT_RUN", reason=reason)
            ledger.add("C14-probe-history-real-table", "NOT_RUN", reason=reason)

        sentinel_after_key, sentinel_after, sentinel_after_mode = await ledger.call(
            "result.table_manage", {"action": "get", "path": _path("table", sentinel_table_tag)},
            "C14-complex-table-sentinel-preservation",
        )
        sentinel_after_data = _data(sentinel_after)
        sentinel_preserved = (sentinel_after_mode == "response" and _success(sentinel_after)
                              and sentinel_after_data.get("data") == real_rows
                              and sentinel_after_data.get("imaginary") == imag_rows
                              and sentinel_after_data.get("headers") == ["T", "u"])
        ledger.add(
            "C14-complex-table-sentinel-preservation",
            "PASS" if sentinel_preserved else _bad_status(sentinel_after, sentinel_after_mode),
            operation="result.table_manage", key=sentinel_after_key, response=sentinel_after,
            assertions={"preserved": sentinel_preserved, "table": sentinel_table_tag,
                        "history_table_distinct": history_table_tag != sentinel_table_tag},
            reason=None if sentinel_preserved else "complex sentinel changed while the separate probe table was used",
        )

        # This negative uses a valid existing probe path if available.  It verifies the public
        # prevalidation boundary without pretending to inject a native setter exception.
        if probe_pass:
            await _refusal(ledger, "C14-property-failure-prevalidation", "probe.update", {
                "path": _probe_path(probe_tag, "comp1"),
                "definition": {"properties": {"expr": "T", "__g33_unknown_property__": 1}},
            }, {"INVALID_REQUEST"})
        else:
            ledger.add("C14-property-failure-prevalidation", "NOT_RUN", reason="probe.create was not verified")
        ledger.add("C14-history-axis-metadata", "NOT_RUN",
                   reason="Chain-A is steady; time/parameter axes are preserved as absent. Use a separately solved Chain-B fixture for nonempty axes")
        c14_ok = (sentinel_pass and sentinel_get_pass and history_table_pass and probe_pass
                  and update_pass and empty_history_pass and study_pass and history_pass
                  and sentinel_preserved)
        ledger.add("C14", "PASS" if c14_ok else ("BLOCKED" if not inventory_ok else "FAIL"),
                   assertions={"complex_table_sentinel_create": sentinel_pass,
                                "complex_table_sentinel_get": sentinel_get_pass,
                                "probe_history_table_create_empty": history_table_pass,
                                "complex_table_sentinel_preserved": sentinel_preserved,
                                "domain_probe_create": probe_pass, "probe_update": update_pass,
                                "history_empty_before_study": empty_history_pass,
                                "study_run_after_gen_result": study_pass,
                                "probe_history": history_pass})
    except Exception as exc:
        # A malformed live response or an unexpected public route is bounded evidence.  Keep the
        # cleanup/final ledger usable so the parent driver can inspect the exact call preceding it.
        ledger.add("node-probe-helper-unhandled", "BLOCKED",
                   reason=f"{type(exc).__name__}: {exc}", exception_type=type(exc).__name__)
    finally:
        # Cleanup is explicit and verified.  A cleanup refusal remains a failure row and is never
        # hidden behind the rollup case.  Probe removal can natively cascade to its linked table;
        # inventory that state before attempting any explicit table removal so NODE_NOT_FOUND is
        # not misreported as a failed cleanup for an already absent owned node.
        for tag, component in reversed(created_probes):
            await _remove_probe(ledger, tag, component)
        table_tags, table_inventory_ok = await _table_tags_for_cleanup(ledger)
        for tag in reversed(created_tables):
            if table_inventory_ok and tag not in table_tags:
                _record_absent_owned_table(ledger, tag)
            else:
                await _remove_table(ledger, tag)
        for tag in reversed(created_datasets):
            # Removing one derived dataset may natively cascade to another
            # owned node.  Refresh the real tag inventory before each remove so
            # a cascade is recorded as an independently verified cleanup.
            dataset_tags, dataset_inventory_ok = await _dataset_tags_for_cleanup(ledger)
            if dataset_inventory_ok and tag not in dataset_tags:
                _record_absent_owned_dataset(ledger, tag)
            else:
                await _remove_dataset(ledger, tag)
        await _preservation_check(ledger, initial)

    discipline = {
        "call_count": len(ledger.calls),
        "unique_idempotency_keys": len({call.get("idempotency_key") for call in ledger.calls}) == len(ledger.calls),
        "all_reconcile_false": all(call.get("reconcile") is False for call in ledger.calls),
        "all_body_hashes_recorded": all(isinstance(call.get("body_sha256"), str) and call["body_sha256"] for call in ledger.calls),
    }
    ledger.add("request-discipline", "PASS" if all(discipline.values()) else "FAIL", assertions=discipline)
    return ledger.finish(metadata={"bound_model": True, "inventory_ok": inventory_ok,
                                   "include_c11": include_c11,
                                   "execute_java_supplied": callable(execute_java),
                                   "cutplane_fixture_request": CUTPLANE_FIXTURE_REQUEST,
                                   "native_method_allowlist": NATIVE_METHOD_ALLOWLIST})


async def run_cutplane_cases(client: Any, run_dir: Path) -> dict[str, Any]:
    """Run the isolated solved-3D CutPlane case against a root-owned fixture.

    The fixture must already be built and solved by the caller and described by
    ``client.cutplane_fixture``.  This entry point deliberately does not create
    or relabel a model and does not invoke Java; it is safe for the root driver
    to call after its serial fixture setup.
    """
    ledger = _NodeLedger(client, Path(run_dir))
    state = getattr(client, "state", {})
    bound = isinstance(state, Mapping) and isinstance(state.get("ref"), Mapping)
    if not bound:
        ledger.add("cutplane-precondition-bound-model", "BLOCKED",
                   reason="ActionClient has no bound solved 3-D model_ref")
        ledger.add("C11-CutPlane-3D", "NOT_RUN", reason="no bound 3-D fixture")
    else:
        await _cutplane_case(ledger, client, execute_java=None)
    discipline = {
        "call_count": len(ledger.calls),
        "unique_idempotency_keys": len({call.get("idempotency_key") for call in ledger.calls}) == len(ledger.calls),
        "all_reconcile_false": all(call.get("reconcile") is False for call in ledger.calls),
        "all_body_hashes_recorded": all(isinstance(call.get("body_sha256"), str) and call["body_sha256"]
                                         for call in ledger.calls),
    }
    ledger.add("request-discipline", "PASS" if all(discipline.values()) else "FAIL", assertions=discipline)
    return ledger.finish(metadata={"scope": "public_mcp_solved_3d_cutplane",
                                   "bound_model": bound,
                                   "fixture_request": CUTPLANE_FIXTURE_REQUEST,
                                   "native_method_allowlist": NATIVE_METHOD_ALLOWLIST})


TRANSIENT_PROBE_FIXTURE_REQUEST: dict[str, Any] = {
    "kind": "temporary_solved_chain_b_transient_fixture",
    "required_tags": {"dataset": "dset1", "study": "std1", "solution": "sol1"},
    "probe": {
        "type_id": "GlobalProbe", "native_type": "GlobalVariable", "expression": "2",
        "component": "an explicit component owner (default comp1); root collection is bound with ModelEntity.model(String)",
    },
    "acceptance": {
        "history_table": "a newly created empty table linked to the owned probe",
        "time_axis": "at least three finite monotonic values read from probe.history",
        "rows": "same count as the time axis, every native row contains only the value 2",
    },
    "safety": "caller owns a fresh Chain-B model; this helper never builds or relabels a model",
}


def _monotonic_finite_numbers(value: Any, *, minimum: int = 3) -> tuple[list[float], bool]:
    numbers = _numeric_values(value)
    monotonic = all(left <= right for left, right in zip(numbers, numbers[1:]))
    return numbers, len(numbers) >= minimum and all(math.isfinite(number) for number in numbers) and monotonic


def _native_scalar(value: Any) -> float | None:
    """Convert one native table cell/header to a finite real scalar only."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except (TypeError, ValueError):
            return None
    elif isinstance(value, Mapping) and "real" in value:
        # The published history rows are complex wrappers.  A real-valued
        # wrapper (imag == 0, exactly what a time axis is) is accepted; a
        # genuinely complex entry is refused instead of being truncated to its
        # real part.
        if "imag" in value:
            imaginary = _native_scalar(value.get("imag"))
            if imaginary is None or imaginary != 0.0:
                return None
        return _native_scalar(value.get("real"))
    else:
        return None
    return number if math.isfinite(number) else None


def _is_time_header(value: Any) -> bool:
    """Recognise a native time column label.

    COMSOL localizes generated table headers by the session's UI language, so
    the English spelling is not the only native one: the acceptance host emits
    ``时间 (s)``.  A label alone never proves an axis; the caller still has to
    reproduce the bound solution's own time steps.
    """
    if not isinstance(value, str):
        return False
    normalized = value.strip().lower().replace("_", " ")
    return "time" in normalized or "时间" in normalized


def _column_values(rows: list[Any], index: int) -> list[float] | None:
    """Return one column as finite scalars, or ``None`` if a cell is not numeric."""
    values: list[float] = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or index >= len(row):
            return None
        scalar = _native_scalar(row[index])
        if scalar is None:
            return None
        values.append(scalar)
    return values


def _history_time_axis(history: Mapping[str, Any], rows: Any,
                       stored_times: Sequence[Any] | None = None) -> tuple[list[float], dict[str, Any]]:
    """Find the native time axis of a recorded probe table.

    ``TableFeature`` has no time getter in COMSOL 6.4 (verified live: every
    plausible getter raises ``NoSuchMethodException``), so the axis is read from
    the table's own labelled column or numeric row headers.  COMSOL localizes
    that label (the acceptance host emits ``时间 (s)``), and a label alone never
    proves the values: the caller additionally checks that the recorded span
    covers the bound solution's stored output times
    (:func:`_axis_covers_stored_times`).  ``stored_times`` is only reported here
    so the evidence records what the independent native read found.
    """
    evidence: dict[str, Any] = {"source": "none", "header": None, "column_index": None}
    reference = [_native_scalar(value) for value in (stored_times or [])]
    reference = [value for value in reference if value is not None]
    if reference:
        evidence["stored_output_times"] = reference
    if not isinstance(rows, list) or not rows:
        return [], evidence
    headers = history.get("headers")
    if not isinstance(headers, list):
        headers = history.get("column_headers")
    headers = headers if isinstance(headers, list) else []

    for index, header in enumerate(headers):
        if not _is_time_header(header):
            continue
        values = _column_values(rows, index)
        if values is None:
            continue
        axis, ok = _monotonic_finite_numbers(values, minimum=3)
        if ok:
            evidence.update(source="column", header=str(header), column_index=index)
            return axis, evidence

    row_headers = history.get("row_headers")
    if isinstance(row_headers, list) and len(row_headers) == len(rows):
        values = [_native_scalar(value) for value in row_headers]
        if all(value is not None for value in values):
            axis, ok = _monotonic_finite_numbers(values, minimum=3)
            if ok:
                evidence.update(source="row_headers", header="row_headers", column_index=None)
                return axis, evidence
    return [], evidence


def _axis_covers_stored_times(axis: Sequence[float], stored_times: Sequence[Any]) -> tuple[bool, dict[str, Any]]:
    """Require the recorded history to cover the bound solution's time span.

    A Definitions probe records its history at the solver's own (internal) time
    steps, so the row count is *not* the stored-solution count (verified live: 5
    stored output times, 23 recorded rows).  What must hold is that the recorded
    axis starts no later than the first stored time and reaches at least the
    last one -- a truncated or shifted axis is refused, an overshoot past the
    requested end time is the solver's own behaviour and is reported.
    """
    reference = [value for value in (_native_scalar(item) for item in (stored_times or []))
                 if value is not None]
    evidence: dict[str, Any] = {"stored_steps": len(reference), "recorded_steps": len(list(axis or []))}
    if len(reference) < 3 or not axis:
        evidence["covers_stored_span"] = False
        evidence["reason"] = "the bound solution published fewer than three stored time steps"
        return False, evidence
    low, high = min(reference), max(reference)
    start, end = axis[0], axis[-1]
    covers = bool(start <= low + 1e-9 and end >= high - 1e-9)
    evidence.update(stored_span=[low, high], recorded_span=[start, end], covers_stored_span=covers)
    if not covers:
        evidence["reason"] = ("the recorded axis does not cover the stored output times "
                              f"[{low}, {high}] (recorded [{start}, {end}])")
    return covers, evidence


async def run_transient_probe_case(client: Any, run_dir: Path) -> dict[str, Any]:
    """Verify a real Chain-B transient Definitions probe and linked history table.

    This is intentionally independent from :func:`run_node_probe_cases`: it uses a global
    ``GlobalVariable`` probe with expression ``2`` so every generated table row has a simple
    oracle, while the transient time axis must still come from the native table metadata.  The
    helper never seeds rows, calls ``genResult`` from ``probe.history``, or treats a table echo as
    a solve.  The caller must bind a solved/solvable Chain-B model before invoking it.
    """
    ledger = _NodeLedger(client, Path(run_dir))
    state = getattr(client, "state", {})
    bound = isinstance(state, Mapping) and isinstance(state.get("ref"), Mapping)
    if not bound:
        ledger.add("C14-transient-precondition-bound-model", "BLOCKED",
                   reason="ActionClient has no bound Chain-B model_ref",
                   fixture_request=TRANSIENT_PROBE_FIXTURE_REQUEST)
        discipline = {
            "call_count": len(ledger.calls),
            "unique_idempotency_keys": True,
            "all_reconcile_false": True,
            "all_body_hashes_recorded": True,
        }
        ledger.add("request-discipline", "PASS", assertions=discipline)
        return ledger.finish(metadata={"scope": "public_mcp_chain_b_transient_probe",
                                       "bound_model": False,
                                       "fixture_request": TRANSIENT_PROBE_FIXTURE_REQUEST})

    fixture = getattr(client, "transient_probe_fixture", None)
    fixture = fixture if isinstance(fixture, Mapping) else {}
    dataset_tag = fixture.get("dataset", "dset1")
    study_tag = fixture.get("study", "std1")
    component_tag = fixture.get("component", "comp1")
    if (not isinstance(dataset_tag, str) or not dataset_tag or not isinstance(study_tag, str) or not study_tag
            or not isinstance(component_tag, str) or not component_tag):
        ledger.add("C14-transient-fixture-descriptor", "BLOCKED",
                   reason="transient_probe_fixture dataset/study/component tags are not nonempty strings",
                   fixture_request=TRANSIENT_PROBE_FIXTURE_REQUEST)
        discipline = {
            "call_count": len(ledger.calls),
            "unique_idempotency_keys": len({call.get("idempotency_key") for call in ledger.calls}) == len(ledger.calls),
            "all_reconcile_false": all(call.get("reconcile") is False for call in ledger.calls),
            "all_body_hashes_recorded": all(isinstance(call.get("body_sha256"), str) and call["body_sha256"]
                                             for call in ledger.calls),
        }
        ledger.add("request-discipline", "PASS" if all(discipline.values()) else "FAIL", assertions=discipline)
        return ledger.finish(metadata={"scope": "public_mcp_chain_b_transient_probe",
                                       "bound_model": True,
                                       "fixture_request": TRANSIENT_PROBE_FIXTURE_REQUEST,
                                       "fixture": _safe(dict(fixture))})

    initial: dict[str, Any] = {}
    inventory_ok = False
    created_tables: list[str] = []
    created_probe = False
    probe_tag = _new_tag("trprobe", Path(run_dir).name, 1)
    history_table_tag = _new_tag("trhist", Path(run_dir).name, 1)
    probe_pass = False
    update_pass = False
    study_pass = False
    history_pass = False
    list_pass = False
    empty_history_pass = False
    try:
        initial, inventory_ok = await _snapshot(ledger)
        datasets = initial.get("datasets", {}).get("items", [])
        solution_row = next((item for item in datasets
                             if isinstance(item, Mapping) and item.get("tag") == dataset_tag), None)
        solution_ok = (inventory_ok and isinstance(solution_row, Mapping)
                       and solution_row.get("type_id") == "Solution")
        ledger.add(
            "C14-transient-solution-dataset",
            "PASS" if solution_ok else ("BLOCKED" if not inventory_ok else "FAIL"),
            assertions={"dataset": dataset_tag, "study": study_tag,
                        "type_id": solution_row.get("type_id") if isinstance(solution_row, Mapping) else None,
                        "solution_dataset_readable": solution_ok},
            reason=None if solution_ok else "Chain-B Solution dataset was not independently confirmed by dataset.list",
        )
        if not solution_ok:
            raise _TransientProbePrecondition()

        table_key, table_payload, table_mode = await ledger.call(
            "result.table_manage",
            {"action": "create", "path": _path("table", history_table_tag),
             "definition": {"type_id": "Table", "data": [], "headers": ["probe"]}},
            "C14-transient-history-table-create-empty",
        )
        table_pass, table_assertions = _mutation_verified(table_payload, type_id="Table")
        if table_pass:
            created_tables.append(history_table_tag)
        ledger.add("C14-transient-history-table-create-empty",
                   "PASS" if table_pass else _bad_status(table_payload, table_mode),
                   operation="result.table_manage", key=table_key, response=table_payload,
                   assertions=table_assertions,
                   reason=None if table_pass else "empty transient history table lacked native APPLIED/readback proof")

        probe_key, probe_payload, probe_mode = await ledger.call(
            "probe.create",
            {"tag": probe_tag, "type_id": "GlobalProbe",
             "component": component_tag,
             "definition": {"properties": {"expr": "2", "unit": "1", "table": history_table_tag}}},
            "C14-transient-global-probe-create",
        )
        probe_pass, probe_assertions = _mutation_verified(probe_payload, type_id="GlobalProbe")
        if probe_pass:
            created_probe = True
        ledger.add("C14-transient-global-probe-create",
                   "PASS" if probe_pass else _bad_status(probe_payload, probe_mode),
                   operation="probe.create", key=probe_key, response=probe_payload,
                   assertions=probe_assertions,
                   reason=None if probe_pass else "GlobalProbe create lacked native APPLIED/readback proof")

        if probe_pass:
            list_key, list_payload, list_mode = await ledger.call(
                "probe.list", {}, "C14-transient-global-probe-list-readback",
            )
            list_data = _data(list_payload)
            probe_items = list_data.get("probes") if isinstance(list_data.get("probes"), list) else []
            listed = next((item for item in probe_items
                           if isinstance(item, Mapping) and item.get("tag") == probe_tag), None)
            list_pass = (list_mode == "response" and _success(list_payload)
                         and isinstance(listed, Mapping)
                         and listed.get("type_id") == "GlobalVariable"
                         and listed.get("expression") == "2"
                         and listed.get("table") == history_table_tag
                         and listed.get("component") == component_tag)
            ledger.add("C14-transient-global-probe-list-readback",
                       "PASS" if list_pass else _bad_status(list_payload, list_mode),
                       operation="probe.list", key=list_key, response=list_payload,
                       assertions={"listed": _safe(dict(listed)) if isinstance(listed, Mapping) else None,
                                   "native_type": listed.get("type_id") if isinstance(listed, Mapping) else None,
                                   "expression": listed.get("expression") if isinstance(listed, Mapping) else None,
                                   "table": listed.get("table") if isinstance(listed, Mapping) else None,
                                   "global_owner": listed.get("component") == component_tag if isinstance(listed, Mapping) else False},
                       reason=None if list_pass else
                       "probe.list did not confirm GlobalVariable expression=2 and linked history table")

        if probe_pass and table_pass:
            update_key, update_payload, update_mode = await ledger.call(
                "probe.update",
                {"path": _probe_path(probe_tag, component_tag),
                 "definition": {"properties": {"expr": "2", "table": history_table_tag},
                                "gen_result": {"solution": "sol1"}}},
                "C14-transient-global-probe-update-gen-result",
            )
            update_pass, update_assertions = _mutation_verified(update_payload, type_id="GlobalVariable")
            applied_steps = _data(update_payload).get("applied", [])
            gen_called = any(isinstance(step, Mapping) and step.get("step") == "genResult"
                             for step in applied_steps)
            update_pass = update_pass and gen_called
            update_assertions["genResult_applied"] = gen_called
            ledger.add("C14-transient-global-probe-update-gen-result",
                       "PASS" if update_pass else _bad_status(update_payload, update_mode),
                       operation="probe.update", key=update_key, response=update_payload,
                       assertions=update_assertions,
                       reason=None if update_pass else
                       "probe.update did not prove expr/table readback plus explicit genResult preparation")

            if update_pass:
                empty_key, empty_payload, empty_mode = await ledger.call(
                    "probe.history",
                    {"path": _probe_path(probe_tag, component_tag),
                     "solution": {"dataset": dataset_tag, "solution": "sol1"}},
                    "C14-transient-history-empty-before-study",
                )
                empty_data = _data(empty_payload)
                empty_source = empty_data.get("source") if isinstance(empty_data.get("source"), Mapping) else {}
                empty_rows = empty_data.get("rows")
                empty_history_pass = (empty_mode == "response" and _success(empty_payload)
                                      and isinstance(empty_rows, list) and not empty_rows
                                      and empty_data.get("table") == history_table_tag
                                      and empty_source.get("real_table_readback") is True
                                      and empty_source.get("gen_result_called") is False
                                      and empty_data.get("readback", {}).get("real_readable") is True)
                ledger.add("C14-transient-history-empty-before-study",
                           "PASS" if empty_history_pass else _bad_status(empty_payload, empty_mode),
                           operation="probe.history", key=empty_key, response=empty_payload,
                           assertions={"rows_empty": isinstance(empty_rows, list) and not empty_rows,
                                       "history_table": empty_data.get("table"),
                                       "real_table_readback": empty_source.get("real_table_readback"),
                                       "gen_result_called": empty_source.get("gen_result_called"),
                                       "real_readable": empty_data.get("readback", {}).get("real_readable")},
                           reason=None if empty_history_pass else
                           "genResult-prepared transient table was not independently confirmed empty")
            else:
                ledger.add("C14-transient-history-empty-before-study", "NOT_RUN",
                           reason="GlobalProbe update/genResult was not verified")
        else:
            ledger.add("C14-transient-global-probe-update-gen-result", "NOT_RUN",
                       reason="probe or empty history table create was not verified")
            ledger.add("C14-transient-history-empty-before-study", "NOT_RUN",
                       reason="probe update prerequisite was not verified")

        study_key: str | None = None
        study_payload: Mapping[str, Any] | None = None
        study_mode = "not_run"
        study_observation: Mapping[str, Any] = {}
        if empty_history_pass:
            study_key, study_payload, study_mode = await ledger.call(
                "study.run",
                {"study": {"segments": [{"collection": "study", "tag": study_tag}]}},
                "C14-transient-study-run-after-gen-result",
            )
            status = _job_status(study_payload)
            job_id = _job_id(study_payload)
            active = status in _ACTIVE_JOB_STATUSES or (job_id is not None and status not in _TERMINAL_JOB_STATUSES)
            initial_unknown = _unknown(study_payload)
            if initial_unknown:
                active = True
            if active and study_payload is not None and study_key is not None:
                study_observation = await _wait_for_study_job(ledger, study_payload, study_key)
            study_safe = (not active or (bool(study_observation.get("quiescent"))
                                         and bool(study_observation.get("model_reconciled"))))
            # ``study.run`` is documented as synchronous on the worker queue, so its
            # envelope carries the applied action token (``APPLIED``), never a job
            # status.  The completion evidence is therefore the observed native job
            # record when the caller had to wait, and otherwise the published
            # post-dispatch readback: applied step with a changed study computation
            # timestamp and no failed/not-executed dispatch.
            observed_status = study_observation.get("native_status")
            final_status = observed_status if isinstance(observed_status, str) else status
            study_data = _data(study_payload)
            synchronous_applied = (status == "APPLIED"
                                   and str(study_data.get("dispatch_stage")) == "post_dispatch"
                                   and int(study_data.get("applied_count") or 0) >= 1
                                   and not int(study_data.get("failed_count") or 0)
                                   and not int(study_data.get("not_executed_count") or 0)
                                   and bool(study_data.get("computation_timestamp_changed")))
            study_pass = (study_mode == "response" and _success(study_payload)
                          and not initial_unknown and study_safe
                          and (final_status in _SUCCESS_JOB_STATUSES
                               or (observed_status is None and synchronous_applied)))
            ledger.add("C14-transient-study-run-after-gen-result",
                       "PASS" if study_pass else _bad_status(study_payload, study_mode),
                       operation="study.run", key=study_key, response=study_payload,
                       assertions={"status": status, "native_status": observed_status,
                                   "final_status": final_status, "job_id": job_id,
                                   "active": active, "synchronous_applied": synchronous_applied,
                                   "quiescent": study_observation.get("quiescent"),
                                   "model_reconciled": study_observation.get("model_reconciled"),
                                   "safe_to_continue": study_safe, "unknown": bool(initial_unknown)},
                       reason=None if study_pass else
                       "study.run did not complete as a terminal, reconciled native Chain-B solve")
        else:
            ledger.add("C14-transient-study-run-after-gen-result", "NOT_RUN",
                       reason="transient linked table was not independently confirmed empty before study.run")

        native_times: list[Any] = []
        native_axis_ok = False
        if study_pass:
            # The axis must be the solved solution's own time steps, not a
            # header label (NEXT_GOAL §4: 解轴必须来自实际解).
            indices_key, indices_payload, indices_mode = await ledger.call(
                "dataset.solution_indices", {"path": _path("dataset", dataset_tag)},
                "C14-transient-solution-time-steps",
            )
            indices_data = _data(indices_payload)
            raw_times = indices_data.get("time_values")
            native_times = list(raw_times) if isinstance(raw_times, list) else []
            native_axis_ok = (indices_mode == "response" and _success(indices_payload)
                              and indices_data.get("binding_complete") is True
                              and len(_numeric_values(native_times)) >= 3)
            ledger.add("C14-transient-solution-time-steps",
                       "PASS" if native_axis_ok else _bad_status(indices_payload, indices_mode),
                       operation="dataset.solution_indices", key=indices_key, response=indices_payload,
                       assertions={"binding_complete": indices_data.get("binding_complete"),
                                   "solution": indices_data.get("solution"),
                                   "dataset": indices_data.get("dataset"),
                                   "native_time_steps": _numeric_values(native_times),
                                   "native_step_count": len(_numeric_values(native_times))},
                       reason=None if native_axis_ok else
                       "bound solution did not publish >=3 native time steps for the dataset")
        else:
            ledger.add("C14-transient-solution-time-steps", "NOT_RUN",
                       reason="Chain-B study.run did not complete safely")

        if study_pass:
            history_key, history_payload, history_mode = await ledger.call(
                "probe.history",
                {"path": _probe_path(probe_tag, component_tag),
                 "solution": {"dataset": dataset_tag, "solution": "sol1"}},
                "C14-transient-history-after-study",
            )
            history_data = _data(history_payload)
            source = history_data.get("source") if isinstance(history_data.get("source"), Mapping) else {}
            rows = history_data.get("rows")
            times, time_axis = _history_time_axis(history_data, rows,
                                                  native_times if native_axis_ok else None)
            time_axis_ok = len(times) >= 3 and time_axis.get("column_index") is not None
            covers, cover_evidence = _axis_covers_stored_times(times, native_times)
            headers = history_data.get("headers")
            time_column = time_axis.get("column_index") if isinstance(time_axis, Mapping) else None
            row_values = []
            if isinstance(rows, list):
                for row in rows:
                    if not isinstance(row, (list, tuple)):
                        row_values.append([])
                        continue
                    indices = [index for index in range(len(row)) if index != time_column]
                    row_values.append([number for index in indices for number in _numeric_values(row[index])])
            row_values_ok = (len(row_values) >= 3 and all(values and all(
                math.isclose(value, 2.0, rel_tol=1e-8, abs_tol=1e-8) for value in values
            ) for values in row_values))
            row_time_count_match = isinstance(rows, list) and len(rows) == len(times)
            history_pass = (history_mode == "response" and _success(history_payload)
                            and isinstance(rows, list) and len(rows) >= 3
                            and row_time_count_match and time_axis_ok and native_axis_ok and covers
                            and row_values_ok
                            and history_data.get("table") == history_table_tag
                            and isinstance(history_data.get("headers"), list)
                            and source.get("real_table_readback") is True
                            and source.get("gen_result_called") is False
                            and history_data.get("readback", {}).get("real_readable") is True
                            and _finite(rows))
            ledger.add("C14-transient-history-real-table",
                       "PASS" if history_pass else _bad_status(history_payload, history_mode),
                       operation="probe.history", key=history_key, response=history_payload,
                       assertions={"rows_nonempty": isinstance(rows, list) and bool(rows),
                                   "row_count": len(rows) if isinstance(rows, list) else None,
                                   "time_values": times, "time_axis_ok": time_axis_ok,
                                   "time_axis": time_axis,
                                   "row_time_count_match": row_time_count_match,
                                   "row_values": row_values, "row_values_all_two": row_values_ok,
                                   "history_table": history_data.get("table"),
                                   "headers": headers,
                                   "real_table_readback": source.get("real_table_readback"),
                                   "gen_result_called": source.get("gen_result_called"),
                                   "real_readable": history_data.get("readback", {}).get("real_readable")},
                       reason=None if history_pass else
                       "probe.history did not return a real linked table with >=3 time samples, one 2-valued row per time, and native time metadata")
        else:
            ledger.add("C14-transient-history-real-table", "NOT_RUN",
                       reason="Chain-B study.run did not complete safely")

        ledger.add("C14-transient-history-axis-metadata",
                   "PASS" if history_pass else ("NOT_RUN" if not study_pass else "FAIL"),
                   assertions={"time_axis_required": True, "time_axis_from_history": history_pass,
                               "stored_output_times": _numeric_values(native_times),
                               "recorded_axis_covers_stored_span": covers,
                               "axis_source": time_axis.get("source") if isinstance(time_axis, Mapping) else None,
                               "recorded_span": cover_evidence.get("recorded_span"),
                               "stored_span": cover_evidence.get("stored_span"),
                               "recorded_steps": cover_evidence.get("recorded_steps"),
                               "stored_steps": cover_evidence.get("stored_steps")},
                   reason=None if history_pass else "transient time axis was not proven from probe.history")
        rollup = table_pass and probe_pass and list_pass and update_pass and empty_history_pass and study_pass and history_pass
        ledger.add("C14-transient", "PASS" if rollup else ("BLOCKED" if not inventory_ok else "FAIL"),
                   assertions={"table_create": table_pass, "probe_create": probe_pass,
                               "probe_list_readback": list_pass, "probe_update_genResult": update_pass,
                               "empty_history_before_study": empty_history_pass,
                               "study_run": study_pass, "solution_time_steps": native_axis_ok,
                               "history_real_table": history_pass,
                               "time_axis": history_pass},
                   reason=None if rollup else "transient probe/history acceptance did not prove every native boundary")
    except _TransientProbePrecondition:
        pass
    except Exception as exc:
        ledger.add("C14-transient-helper-unhandled", "BLOCKED",
                   reason=f"{type(exc).__name__}: {exc}", exception_type=type(exc).__name__)
    finally:
        if created_probe:
            await _remove_probe(ledger, probe_tag, component_tag)
        table_tags, table_inventory_ok = await _table_tags_for_cleanup(ledger)
        for tag in reversed(created_tables):
            if table_inventory_ok and tag not in table_tags:
                _record_absent_owned_table(ledger, tag)
            else:
                await _remove_table(ledger, tag)
        await _preservation_check(ledger, initial)

    discipline = {
        "call_count": len(ledger.calls),
        "unique_idempotency_keys": len({call.get("idempotency_key") for call in ledger.calls}) == len(ledger.calls),
        "all_reconcile_false": all(call.get("reconcile") is False for call in ledger.calls),
        "all_body_hashes_recorded": all(isinstance(call.get("body_sha256"), str) and call["body_sha256"]
                                         for call in ledger.calls),
    }
    ledger.add("request-discipline", "PASS" if all(discipline.values()) else "FAIL", assertions=discipline)
    return ledger.finish(metadata={"scope": "public_mcp_chain_b_transient_probe",
                                   "bound_model": True,
                                   "fixture": _safe(dict(fixture)),
                                   "dataset": dataset_tag,
                                   "study": study_tag,
                                   "probe_tag": probe_tag,
                                   "history_table_tag": history_table_tag,
                                   "component": component_tag,
                                   "fixture_request": TRANSIENT_PROBE_FIXTURE_REQUEST,
                                   "native_method_allowlist": NATIVE_METHOD_ALLOWLIST})


__all__ = ["CUTPLANE_FIXTURE_REQUEST", "NATIVE_METHOD_ALLOWLIST", "TRANSIENT_PROBE_FIXTURE_REQUEST",
           "run_cutplane_cases", "run_node_probe_cases", "run_transient_probe_case"]
