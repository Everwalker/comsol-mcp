"""Public-MCP export/artifact acceptance cases for a bound Chain-A model.

The caller owns the MCP host and has already bound a freshly solved Chain-A model.  This
module only sends public ``ActionClient.action`` requests; it does not start COMSOL, import
the backend, or create a model.  Every request receives a new idempotency key and all calls
use ``reconcile=False`` so the caller's witness/ledger remains the source of truth.

``run_export_cases`` writes an appendable JSON ledger under ``run_dir``.  It deliberately
keeps UNKNOWN and unavailable fault injectors distinct from PASS: no private exception or
fake response is promoted to live evidence.
"""
from __future__ import annotations

import base64
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import re
try:
    import resource
except ImportError:  # pragma: no cover - resource is available on supported macOS/Linux hosts
    resource = None  # type: ignore[assignment]
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))
import phase4_run_mcp as protocol  # noqa: E402


_CHUNK_BYTES = 64 * 1024
_MAX_CHUNKS = 10000
_MAX_METADATA_BYTES = 4 * 1024 * 1024
_MAX_ARTIFACT_INVENTORY_ENTRIES = 10000
_BUDGET_PROBE_MAX_EXPRESSIONS = 1000
_BUDGET_PROBE_MAX_POINTS = 100_000
# The current public result adapter rejects more than this many expressions
# before it creates an Eval feature.  Keep this separate from the helper's
# safety cap: a C13 plan may be mathematically bounded yet still be
# impossible to dispatch through the current public contract.
_PUBLIC_RESULT_EXPRESSION_CAP = 32
_BUDGET_REFUSAL_CODES = frozenset({"ELEMENT_BUDGET_EXCEEDED", "BYTE_BUDGET_EXCEEDED"})
_RAW_GETTER_METHOD_LEAVES = frozenset({"getdata", "getimagdata"})
_KEY_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")
_STATUSES = {"PASS", "FAIL", "BLOCKED", "NOT_RUN", "UNVERIFIED"}
_EVALUATION_FAILURE_CODES = frozenset({
    "EVALUATION_FAILED",
    "ENGINE_CALL_FAILED",
    "ENGINE_EVALUATION_FAILED",
    "COMSOL_EVALUATION_FAILED",
    "INVALID_EXPRESSION",
})
_EVALUATION_METHOD_LEAVES = frozenset({"run", "getdata", "getreal", "getimagdata", "getimag"})


def _safe(value: Any) -> Any:
    """Use the protocol's fixed JSON conversion when available."""
    converter = getattr(protocol, "_json_safe", None)
    if callable(converter):
        return converter(value)
    if isinstance(value, Mapping):
        return {str(key): _safe(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(child) for child in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _sample_process_rss() -> dict[str, Any]:
    """Sample the driver's cumulative resident-set high-water mark.

    ``ru_maxrss`` is a process-level sample, not a Worker/COMSOL measurement.
    The evidence labels it as sampled and keeps the engine cache explicitly
    unmeasured.  macOS reports bytes; Linux reports KiB.
    """
    if resource is None:
        return {
            "status": "UNAVAILABLE",
            "source": "resource.getrusage(RUSAGE_SELF).ru_maxrss",
            "scope": "driver_process",
            "sampled_peak": False,
            "engine_internal_cache": "UNMEASURED",
        }
    try:
        raw = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        scale = 1 if sys.platform == "darwin" else 1024
        return {
            "status": "SAMPLED",
            "source": "resource.getrusage(RUSAGE_SELF).ru_maxrss",
            "scope": "driver_process",
            "raw_value": raw,
            "raw_unit": "bytes" if scale == 1 else "KiB",
            "sampled_peak_rss_bytes": raw * scale,
            "sampled_peak": True,
            "absolute_peak": False,
            "engine_internal_cache": "UNMEASURED",
        }
    except (OSError, ValueError, AttributeError):
        return {
            "status": "UNAVAILABLE",
            "source": "resource.getrusage(RUSAGE_SELF).ru_maxrss",
            "scope": "driver_process",
            "sampled_peak": False,
            "engine_internal_cache": "UNMEASURED",
        }


def _memory_interval(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    before_value = before.get("sampled_peak_rss_bytes")
    after_value = after.get("sampled_peak_rss_bytes")
    values = [value for value in (before_value, after_value)
              if isinstance(value, int) and not isinstance(value, bool)]
    return {
        "before": dict(before),
        "after": dict(after),
        "sampled_peak_rss_bytes": max(values) if values else None,
        "sampled_peak": bool(values),
        "absolute_peak": False,
        "scope": "driver_process",
        "engine_internal_cache": "UNMEASURED",
    }


def _attach_memory_sample(call_record: dict[str, Any], before: Mapping[str, Any]) -> None:
    call_record["memory_sample"] = _memory_interval(before, _sample_process_rss())


def _data(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    value = payload.get("data") if isinstance(payload, Mapping) else None
    return dict(value) if isinstance(value, Mapping) else {}


def _error_code(payload: Mapping[str, Any] | None) -> str | None:
    value = getattr(protocol, "_error_code", None)
    if callable(value):
        return value(payload)
    error = payload.get("error") if isinstance(payload, Mapping) else None
    return str(error.get("code")) if isinstance(error, Mapping) and error.get("code") else None


def _success(payload: Mapping[str, Any] | None) -> bool:
    value = getattr(protocol, "_success", None)
    if callable(value):
        return bool(value(payload))
    return isinstance(payload, Mapping) and payload.get("success") is True


def _unknown(payload: Mapping[str, Any] | None) -> dict[str, Any] | None:
    value = getattr(protocol, "_unknown_outcome", None)
    return value(payload) if callable(value) else None


def _nested_codes(value: Any) -> list[str]:
    """Collect structured error codes without parsing free-form engine text."""
    found: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            if key in {"code", "error_code", "cause_code"} and isinstance(child, str) and child:
                found.append(child)
            found.extend(_nested_codes(child))
    elif isinstance(value, (list, tuple)):
        for child in value:
            found.extend(_nested_codes(child))
    return found


def _product_failure_code(payload: Mapping[str, Any] | None) -> str | None:
    """Return a known product-contract defect hidden inside an UNKNOWN envelope.

    A failed field-array shape check can be wrapped as ``EXECUTION_STATE_UNKNOWN``
    when the callback did not prove a pre-dispatch refusal.  It still remains a
    product defect for acceptance classification, while the job must be queried
    before the helper returns.  Transport/engine UNKNOWN responses stay BLOCKED.
    """
    structural = {
        "FIELD_ARRAY_SHAPE_MISMATCH",
        "SOLUTION_AXIS_METADATA_UNAVAILABLE",
        "NONFINITE_FIELD_VALUE",
        "EMPTY_FIELD_ARRAY",
    }
    for code in _nested_codes(payload):
        if code in structural:
            return code
    return None


def _product_failure_code_from_text(value: Any) -> str | None:
    """Recognize a bounded set of structured product defects in exception text."""
    text = str(value or "")
    for code in (
        "FIELD_ARRAY_SHAPE_MISMATCH",
        "SOLUTION_AXIS_METADATA_UNAVAILABLE",
        "NONFINITE_FIELD_VALUE",
        "EMPTY_FIELD_ARRAY",
    ):
        if code in text:
            return code
    return None


def _failure_status(payload: Mapping[str, Any] | None, mode: str) -> str:
    """Map a call outcome without hiding a known product defect as environmental BLOCKED."""
    if _product_failure_code(payload):
        return "FAIL"
    if mode in {"exception", "invalid-response", "halted"} or _unknown(payload):
        return "BLOCKED"
    return "FAIL"


def _reconciled_quiescent(payload: Mapping[str, Any] | None) -> bool | None:
    """Read only the published reconcile location for the quiescence release decision."""
    data = _data(payload)
    metadata = data.get("metadata")
    if isinstance(metadata, Mapping):
        value = metadata.get("reconciled_quiescent")
        if isinstance(value, bool):
            return value
    value = data.get("reconciled_quiescent")
    return value if isinstance(value, bool) else None


def _unknown_job_id(payload: Mapping[str, Any] | None) -> str | None:
    unknown = _unknown(payload)
    if isinstance(unknown, Mapping) and isinstance(unknown.get("job_id"), str) and unknown["job_id"]:
        return str(unknown["job_id"])
    if isinstance(payload, Mapping):
        execution = payload.get("execution")
        if isinstance(execution, Mapping) and isinstance(execution.get("job_id"), str) and execution["job_id"]:
            return str(execution["job_id"])
        data = payload.get("data")
        if isinstance(data, Mapping) and isinstance(data.get("job_id"), str) and data["job_id"]:
            return str(data["job_id"])
    return None


def _unknown_evidence_for(ledger: "_ExportLedger", payload: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Find the reconciliation record belonging to one UNKNOWN response."""
    job_id = _unknown_job_id(payload)
    for evidence in reversed(ledger.unknown_evidence):
        if job_id is None or evidence.get("job_id") == job_id:
            return evidence
    return None


def _slug(value: str) -> str:
    return _KEY_SAFE.sub("-", value).strip(".-")[:64] or "case"


def _stream_hash(path: Path, *, block_size: int = 1024 * 1024) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(block_size), b""):
            digest.update(block)
            size += len(block)
    return size, digest.hexdigest()


def _artifact_inventory(root: Path) -> dict[str, Any]:
    """Record a bounded run-root file inventory for no-publish assertions.

    The inventory is deliberately path/type/size metadata.  It proves that a
    failed export added no run-root artifact without reading every existing
    artifact into memory or presenting a directory listing as an engine-state
    proof.  Symlinks are recorded as entries and never followed for the
    comparison.
    """
    entries: list[dict[str, Any]] = []
    complete = True
    error: str | None = None
    try:
        candidates = sorted(root.rglob("*"), key=lambda path: path.as_posix())
    except (OSError, RuntimeError) as exc:
        return {
            "root": str(root), "complete": False, "entries": [], "paths": [],
            "count": 0, "error": f"{type(exc).__name__}: {exc}",
        }
    for path in candidates:
        try:
            is_link = path.is_symlink()
            if not is_link and not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            entry: dict[str, Any] = {"path": relative, "kind": "symlink" if is_link else "file"}
            if not is_link:
                entry["size"] = path.stat().st_size
            entries.append(entry)
            if len(entries) >= _MAX_ARTIFACT_INVENTORY_ENTRIES:
                complete = False
                error = f"inventory exceeds {_MAX_ARTIFACT_INVENTORY_ENTRIES} entries"
                break
        except (OSError, RuntimeError, ValueError) as exc:
            complete = False
            error = f"{type(exc).__name__}: {exc}"
            break
    return {
        "root": str(root),
        "complete": complete,
        "entries": entries,
        "paths": [entry["path"] for entry in entries],
        "count": len(entries),
        "error": error,
    }


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.partial")
    temporary.write_text(json.dumps(_safe(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                         encoding="utf-8")
    os.replace(temporary, path)


def _response_summary(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    data = _data(payload)
    summary: dict[str, Any] = {
        "success": _success(payload),
        "error_code": _error_code(payload),
        "unknown": _safe(_unknown(payload)),
        "data_keys": sorted(str(key) for key in data),
    }
    for key in ("file_path", "artifact_ref", "sha256", "format", "byte_size", "total_elements",
                "chunk_info", "storage", "status", "cleanup", "execution_state_unknown"):
        if key in data:
            summary[key] = _safe(data[key])
    if isinstance(payload, Mapping) and isinstance(payload.get("execution"), Mapping):
        execution = payload["execution"]
        summary["execution"] = {
            key: _safe(execution.get(key))
            for key in ("operation_id", "job_id", "request_id", "idempotency_key", "revision", "dispatch_stage")
            if key in execution
        }
    return summary


def _response_path(payload: Mapping[str, Any] | None) -> Path | None:
    data = _data(payload)
    for key in ("file_path", "artifact_ref", "path"):
        value = data.get(key)
        if isinstance(value, str) and value:
            return Path(value)
    return None


def _field_metadata(document: Mapping[str, Any]) -> dict[str, Any]:
    metadata = document.get("metadata") if isinstance(document.get("metadata"), Mapping) else {}
    field_array = metadata.get("field_array") if isinstance(metadata.get("field_array"), Mapping) else {}
    axes = metadata.get("axes") or field_array.get("axes") or metadata.get("axis")
    units = (metadata.get("units") or metadata.get("expression_units") or
             field_array.get("units") or field_array.get("expression_units"))
    coordinates = (metadata.get("coordinates") or metadata.get("coords")
                   or field_array.get("coordinates") or field_array.get("coords"))
    source = {
        "expressions": metadata.get("expressions"),
        "dataset": metadata.get("dataset"),
        "solution": metadata.get("solution"),
        "axes": axes,
        "units": units,
        "coordinates": coordinates,
        "field_array": field_array or None,
    }
    source["has_source_identity"] = all(source.get(name) not in (None, "", [])
                                         for name in ("expressions", "dataset", "solution"))
    source["has_axes"] = isinstance(axes, (list, tuple)) and len(axes) > 0
    source["has_units"] = isinstance(units, (list, tuple, str, Mapping)) and len(units) > 0
    source["has_coordinates"] = isinstance(coordinates, (list, tuple, Mapping)) and len(coordinates) > 0
    source["complete"] = bool(source["has_source_identity"] and source["has_axes"]
                               and source["has_units"] and source["has_coordinates"])
    return _safe(source)


def _plan_budget_probe(document: Mapping[str, Any], *, seed_expression: str) -> dict[str, Any]:
    """Derive one bounded C13 over-limit request from a successful Eval witness.

    The request is sized from the native point shape and actual solution axes
    already published in the successful JSON metadata.  It never guesses from
    ``getNData`` and never grows beyond the explicit expression cap.
    """
    metadata = document.get("metadata") if isinstance(document.get("metadata"), Mapping) else {}
    budget = metadata.get("result_budget") if isinstance(metadata, Mapping) else None
    records = budget.get("records") if isinstance(budget, Mapping) else None
    if not isinstance(records, list):
        return {"ready": False, "reason": "successful JSON has no result_budget.records witness"}
    record = next((item for item in reversed(records) if isinstance(item, Mapping)), None)
    if record is None:
        return {"ready": False, "reason": "result_budget.records has no mapping record"}
    computed = record.get("computed") if isinstance(record.get("computed"), Mapping) else {}
    requested = record.get("requested") if isinstance(record.get("requested"), Mapping) else {}
    limits = budget.get("limits") if isinstance(budget, Mapping) and isinstance(budget.get("limits"), Mapping) else {}
    source = requested.get("raw_upper_bound_source") or computed.get("raw_upper_bound_source")
    point_count = computed.get("raw_point_upper_bound") or requested.get("raw_point_upper_bound")
    outer_count = computed.get("outer_count") or requested.get("outer_count")
    inner_total = computed.get("inner_count_total")
    if inner_total is None:
        inner_total = requested.get("inner_count_total")
    if inner_total is None:
        inner_count = requested.get("inner_count")
        if isinstance(inner_count, int) and not isinstance(inner_count, bool):
            inner_total = outer_count * inner_count if isinstance(outer_count, int) else None
        else:
            inner_by_outer = requested.get("inner_counts_by_outer")
            if isinstance(inner_by_outer, list) and all(
                isinstance(value, int) and not isinstance(value, bool) and value > 0
                for value in inner_by_outer
            ):
                inner_total = sum(inner_by_outer)
    complex_components = computed.get("complex_components") or requested.get("complex_components")
    scalar_bytes = computed.get("scalar_bytes") or requested.get("scalar_bytes")
    max_elements = limits.get("max_elements")
    max_bytes = limits.get("max_numeric_payload_bytes") or limits.get("max_bytes")
    fields = (point_count, outer_count, inner_total, complex_components, scalar_bytes, max_elements, max_bytes)
    if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in fields):
        return {"ready": False, "reason": "result budget witness lacks positive native shape, axes, or limits"}
    if not isinstance(source, str) or not source or "native" not in source.lower():
        return {"ready": False, "reason": "point bound is not explicitly sourced from native coordinate shape"}
    if not isinstance(seed_expression, str) or not seed_expression.strip():
        return {"ready": False, "reason": "seed expression is not a non-empty string"}
    if point_count > _BUDGET_PROBE_MAX_POINTS:
        return {
            "ready": False,
            "reason": f"native point shape exceeds helper point cap {_BUDGET_PROBE_MAX_POINTS}",
            "point_count": point_count,
            "point_count_source": source,
            "outer_count": outer_count,
            "inner_count_total": inner_total,
            "complex_components": complex_components,
            "scalar_bytes": scalar_bytes,
            "max_elements": max_elements,
            "max_numeric_payload_bytes": max_bytes,
            "point_cap": _BUDGET_PROBE_MAX_POINTS,
            "expressions": [],
            "budget_scope": "numeric_payload_only; engine_internal_cache=UNMEASURED",
        }
    # ``inner_count_total`` is already the sum across the selected outer
    # solutions in the published result-budget record.  Multiplying by
    # ``outer_count`` again would double-count ragged or multi-outer bindings.
    per_expression_elements = inner_total * point_count * complex_components
    per_expression_bytes = per_expression_elements * scalar_bytes
    required_by_elements = max_elements // per_expression_elements + 1
    required_by_bytes = max_bytes // per_expression_bytes + 1
    # Crossing either configured limit is sufficient for a refusal.  Choose
    # the smaller request so the negative control remains bounded; requiring
    # both limits to be exceeded would inflate this harmless probe for no
    # additional evidence.
    expression_count = min(required_by_elements, required_by_bytes)
    trigger_limit = (
        "ELEMENT_BUDGET_EXCEEDED"
        if required_by_elements <= required_by_bytes
        else "BYTE_BUDGET_EXCEEDED"
    )
    theoretical_elements = expression_count * per_expression_elements
    theoretical_bytes = expression_count * per_expression_bytes
    if expression_count > _BUDGET_PROBE_MAX_EXPRESSIONS:
        return {
            "ready": False,
            "reason": "computed probe exceeds helper expression cap",
            "helper_expression_cap": _BUDGET_PROBE_MAX_EXPRESSIONS,
            "public_expression_cap": _PUBLIC_RESULT_EXPRESSION_CAP,
            "helper_expression_cap_ok": False,
            "public_expression_cap_ok": False,
            "point_count": point_count,
            "point_count_source": source,
            "outer_count": outer_count,
            "inner_count_total": inner_total,
            "complex_components": complex_components,
            "scalar_bytes": scalar_bytes,
            "max_elements": max_elements,
            "max_numeric_payload_bytes": max_bytes,
            "per_expression_elements": per_expression_elements,
            "per_expression_bytes": per_expression_bytes,
            "expression_count": expression_count,
            "trigger_limit": trigger_limit,
            "theoretical_elements": theoretical_elements,
            "theoretical_numeric_payload_bytes": theoretical_bytes,
            "expression_request_bytes": None,
            "expression_sha256": None,
            "expression_first": None,
            "expression_last": None,
            "expressions": [],
            "budget_scope": "numeric_payload_only; engine_internal_cache=UNMEASURED",
        }
    expressions = [f"({seed_expression})+0*{index}" for index in range(expression_count)]
    expression_bytes = len(json.dumps(expressions, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    helper_cap_ok = expression_count <= _BUDGET_PROBE_MAX_EXPRESSIONS
    public_cap_ok = expression_count <= _PUBLIC_RESULT_EXPRESSION_CAP
    plan: dict[str, Any] = {
        "ready": helper_cap_ok and public_cap_ok,
        "reason": (
            None
            if helper_cap_ok and public_cap_ok
            else f"computed probe exceeds public result expression cap {_PUBLIC_RESULT_EXPRESSION_CAP}"
            if helper_cap_ok
            else "computed probe exceeds helper expression cap"
        ),
        "helper_expression_cap": _BUDGET_PROBE_MAX_EXPRESSIONS,
        "public_expression_cap": _PUBLIC_RESULT_EXPRESSION_CAP,
        "helper_expression_cap_ok": helper_cap_ok,
        "public_expression_cap_ok": public_cap_ok,
        "point_count": point_count,
        "point_count_source": source,
        "outer_count": outer_count,
        "inner_count_total": inner_total,
        "complex_components": complex_components,
        "scalar_bytes": scalar_bytes,
        "max_elements": max_elements,
        "max_numeric_payload_bytes": max_bytes,
        "per_expression_elements": per_expression_elements,
        "per_expression_bytes": per_expression_bytes,
        "expression_count": expression_count,
        "trigger_limit": trigger_limit,
        "theoretical_elements": theoretical_elements,
        "theoretical_numeric_payload_bytes": theoretical_bytes,
        "expression_request_bytes": expression_bytes,
        "expression_sha256": hashlib.sha256(
            json.dumps(expressions, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "expression_first": expressions[0],
        "expression_last": expressions[-1],
        "expressions": expressions,
        "budget_scope": "numeric_payload_only; engine_internal_cache=UNMEASURED",
    }
    return plan


def _contains_code_text(value: Any, code: str) -> bool:
    """Find one allowed structured code in bounded nested error text."""
    if isinstance(value, Mapping):
        return any(_contains_code_text(child, code) for child in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_code_text(child, code) for child in value)
    return isinstance(value, str) and code in value


def _finite_column(value: str) -> bool:
    if value == "":
        return True
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _finite_json(value: Any) -> bool:
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, Mapping):
        return all(_finite_json(child) for child in value.values())
    if isinstance(value, (list, tuple)):
        return all(_finite_json(child) for child in value)
    return True


class _ExportLedger:
    def __init__(self, client: Any, run_dir: Path) -> None:
        self.client = client
        self.run_dir = Path(run_dir).expanduser().resolve()
        self.run_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.rows: list[dict[str, Any]] = []
        self.calls: list[dict[str, Any]] = []
        self.counter = 0
        self.artifact_root: Path | None = None
        self.stopped_unknown = False
        self.stop_reason: str | None = None
        self.unknown_evidence: list[dict[str, Any]] = []

    def key(self, label: str) -> str:
        self.counter += 1
        return f"g33-export-{self.counter:03d}-{_slug(label)}"

    def add(self, case: str, status: str, *, operation: str | None = None,
            key: str | None = None, assertions: Mapping[str, Any] | None = None,
            response: Mapping[str, Any] | None = None, reason: str | None = None,
            **extra: Any) -> dict[str, Any]:
        if status not in _STATUSES:
            raise ValueError(f"invalid export case status: {status}")
        row: dict[str, Any] = {
            "case": case,
            "status": status,
            "operation": operation,
            "idempotency_key": key,
            "assertions": _safe(dict(assertions or {})),
            "response": _response_summary(response) if isinstance(response, Mapping) else None,
        }
        if reason:
            row["reason"] = reason
        row.update(_safe(extra))
        self.rows.append(row)
        self.flush()
        return row

    def flush(self) -> None:
        _write_json(self.run_dir / "g33_export_cases.json", {
            "schema_version": 1,
            "scope": "public_mcp_chain_a_export_artifact",
            "run_dir": str(self.run_dir),
            "calls": self.calls,
            "rows": self.rows,
            "summary": {
                status: sum(1 for row in self.rows if row["status"] == status)
                for status in sorted(_STATUSES)
            },
        })


async def _call(ledger: _ExportLedger, operation: str, body: Mapping[str, Any], label: str,
                *, require_model: bool = True, rpc_timeout_s: float | None = None,
                _probe_unknown: bool = True, _allow_stopped: bool = False) -> tuple[str, dict[str, Any] | None, str]:
    key = ledger.key(label)
    kwargs: dict[str, Any] = {
        "require_model": require_model,
        "key": key,
        "request": f"g33-export-{_slug(label)}",
        "reconcile": False,
    }
    if rpc_timeout_s is not None:
        kwargs["rpc_timeout_s"] = rpc_timeout_s
    body_hash_fn = getattr(protocol, "_body_sha256", None)
    body_hash = body_hash_fn(body) if callable(body_hash_fn) else hashlib.sha256(
        json.dumps(_safe(body), sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    ledger.calls.append({
        "operation": operation,
        "idempotency_key": key,
        "request": kwargs["request"],
        "body_sha256": body_hash,
        "reconcile": kwargs["reconcile"],
        "require_model": kwargs["require_model"],
        "rpc_timeout_s": kwargs.get("rpc_timeout_s"),
    })
    ledger.flush()
    rss_before = _sample_process_rss()

    # Once an UNKNOWN job has been observed and its published reconcile read did not prove
    # quiescence, no later mutation is allowed.  Reserve a fresh evidence key for the refused
    # logical step, but do not send it to the server.  The control queries themselves opt out of
    # this gate and are the only calls allowed to complete the original UNKNOWN inspection.
    if ledger.stopped_unknown and operation not in {"job_status", "job_log", "job_result", "job_reconcile"} \
            and not _allow_stopped:
        ledger.calls[-1].update({"mode": "halted-before-dispatch", "elapsed_s": 0.0,
                                 "halt_reason": ledger.stop_reason})
        _attach_memory_sample(ledger.calls[-1], rss_before)
        ledger.flush()
        return key, None, "halted"

    try:
        payload = await ledger.client.action(operation, dict(body), **kwargs)
    except Exception as exc:  # a missing published route is evidence, not a synthetic failure
        code = _product_failure_code_from_text(exc)
        ledger.calls[-1].update({"mode": "exception", "exception_type": type(exc).__name__,
                                 "exception": str(exc)[:300],
                                 "product_failure_code": code})
        _attach_memory_sample(ledger.calls[-1], rss_before)
        ledger.flush()
        return key, None, "exception-product-defect" if code else "exception"
    if not isinstance(payload, Mapping):
        ledger.calls[-1].update({"mode": "invalid-response", "response_type": type(payload).__name__})
        _attach_memory_sample(ledger.calls[-1], rss_before)
        ledger.flush()
        return key, None, "invalid-response"

    observed = dict(payload)
    call_record = ledger.calls[-1]
    call_record.update({"mode": "response", "response": _response_summary(observed)})
    _attach_memory_sample(call_record, rss_before)
    ledger.flush()

    # Every public UNKNOWN response is inspected here, before the caller can issue another
    # field export or overwrite.  The query helper uses _probe_unknown=False for its own four
    # control reads to avoid recursion.
    if _probe_unknown and _unknown(observed):
        evidence = await _query_unknown_job(ledger, observed, label)
        call_record["unknown_job_queries"] = _safe(evidence)
        ledger.flush()
    return key, observed, "response"


async def _query_unknown_job(ledger: _ExportLedger, payload: Mapping[str, Any] | None,
                             label: str) -> dict[str, Any]:
    """Read an UNKNOWN job through all four public job queries before returning.

    These are fresh logical requests with fresh keys.  They are deliberately
    sent through ``ActionClient.action(..., reconcile=False)`` so the helper
    does not invent a replay or let a driver-side reconciliation policy obscure
    the product's own status/result/reconcile envelopes.
    """
    job_id = _unknown_job_id(payload)
    if not job_id:
        result = {"job_id": None, "queries_complete": False, "reason": "UNKNOWN response had no job_id",
                  "quiescent": None, "queries": []}
        ledger.stopped_unknown = True
        ledger.stop_reason = result["reason"]
        ledger.unknown_evidence.append(result)
        ledger.add("unknown-outcome-job-queries", "FAIL", operation="job_status/job_log/job_result/job_reconcile",
                   assertions=result, reason=result["reason"])
        return result
    queries: list[dict[str, Any]] = []
    complete = True
    reconcile_payload: Mapping[str, Any] | None = None
    for operation in ("job_status", "job_log", "job_result", "job_reconcile"):
        key, observed, mode = await _call(
            ledger, operation, {"job_id": job_id}, f"{label}-{operation}", require_model=False,
            _probe_unknown=False,
        )
        if operation == "job_reconcile":
            reconcile_payload = observed
        row = {
            "operation": operation,
            "idempotency_key": key,
            "mode": mode,
            "response": _response_summary(observed) if isinstance(observed, Mapping) else None,
        }
        queries.append(row)
        if mode != "response" or observed is None:
            complete = False
    quiescent = _reconciled_quiescent(reconcile_payload)
    model_reconciled: bool | None = None
    model_reconcile: dict[str, Any] | None = None
    # A terminal/quiescent job only proves that the engine request stopped.  The
    # managed revision may still be stale in ActionClient.state, so always use a
    # fresh public model_inspect(refresh=true) before allowing another mutation.
    # ActionClient.action adopts the returned readback; the explicit call below
    # also records that adoption boundary in this helper's evidence.
    if complete and quiescent is True:
        refresh_key, refreshed, refresh_mode = await _call(
            ledger,
            "model_inspect",
            {"refresh": True},
            f"{label}-model-inspect",
            require_model=True,
            _probe_unknown=False,
            _allow_stopped=True,
        )
        refreshed_execution = {}
        if isinstance(refreshed, Mapping):
            candidate = refreshed.get("execution") or _data(refreshed).get("execution")
            if isinstance(candidate, Mapping):
                refreshed_execution = dict(candidate)
        model_reconciled = bool(
            refresh_mode == "response"
            and isinstance(refreshed, Mapping)
            and _success(refreshed)
            and refreshed_execution.get("dirty") is False
            and isinstance(refreshed_execution.get("revision"), int)
            and not isinstance(refreshed_execution.get("revision"), bool)
        )
        adopt = getattr(ledger.client, "_adopt_readback", None)
        adopted = False
        if callable(adopt) and isinstance(refreshed, Mapping):
            adopt(refreshed)
            adopted = True
        model_reconcile = {
            "operation": "model_inspect",
            "idempotency_key": refresh_key,
            "mode": refresh_mode,
            "response": _response_summary(refreshed) if isinstance(refreshed, Mapping) else None,
            "revision": refreshed_execution.get("revision"),
            "dirty": refreshed_execution.get("dirty"),
            "adopted_readback": adopted,
            "clean_managed_state": model_reconciled,
        }
        ledger.add(
            "unknown-model-reconcile",
            "PASS" if model_reconciled else "BLOCKED",
            operation="model_inspect",
            key=refresh_key,
            response=refreshed,
            assertions={
                "refresh": True,
                "clean_managed_state": model_reconciled,
                "adopted_readback": adopted,
                "revision": refreshed_execution.get("revision"),
                "dirty": refreshed_execution.get("dirty"),
            },
            reason=None if model_reconciled else
            "model_inspect(refresh=true) did not prove a clean managed revision",
        )
    result = {"job_id": job_id, "queries_complete": complete, "quiescent": quiescent,
              "queries": queries, "model_reconciled": model_reconciled,
              "model_reconcile": model_reconcile}
    if not complete or quiescent is not True or model_reconciled is not True:
        ledger.stopped_unknown = True
        ledger.stop_reason = (
            "original UNKNOWN job queries incomplete"
            if not complete else
            "job_reconcile data.metadata.reconciled_quiescent did not prove quiescence"
            if quiescent is not True else
            "model_inspect(refresh=true) did not prove clean managed state"
        )
    ledger.unknown_evidence.append(result)
    ledger.add(
        "unknown-outcome-job-queries",
        "PASS" if complete else "BLOCKED",
        operation="job_status/job_log/job_result/job_reconcile",
        assertions={"job_id": job_id, "queries_complete": complete,
                    "quiescent": quiescent,
                    "quiescent_source": "data.metadata.reconciled_quiescent",
                    "model_reconciled": model_reconciled,
                    "query_operations": [row["operation"] for row in queries]},
        query_results=queries,
        reason=None if complete else ledger.stop_reason,
    )
    if complete and (quiescent is not True or model_reconciled is not True):
        ledger.add(
            "unknown-quiescence-gate", "BLOCKED",
            operation="job_reconcile",
            assertions={"quiescent": quiescent,
                        "source": "data.metadata.reconciled_quiescent",
                        "model_reconciled": model_reconciled,
                        "subsequent_mutations_dispatched": False},
            reason=ledger.stop_reason,
        )
    return result


def _finish(ledger: _ExportLedger) -> dict[str, Any]:
    """Write the final ledger and make any non-PASS outcome visible to callers."""
    summary = {
        status: sum(1 for row in ledger.rows if row["status"] == status)
        for status in sorted(_STATUSES)
    }
    overall = "PASS" if summary["FAIL"] == 0 and summary["BLOCKED"] == 0 and summary["UNVERIFIED"] == 0 else (
        "BLOCKED" if summary["FAIL"] == 0 else "FAIL"
    )
    result = {
        "calls": ledger.calls,
        "rows": ledger.rows,
        "summary": summary,
        "overall": overall,
        "run_dir": str(ledger.run_dir),
        "artifact_root": str(ledger.artifact_root) if ledger.artifact_root is not None else None,
    }
    _write_json(ledger.run_dir / "g33_export_cases.final.json", result)
    return result


def _dispatch_proof(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    product = getattr(protocol, "product_dispatch_stage", lambda _p: None)(payload)
    observed = getattr(protocol, "observed_dispatch_stage", lambda _p: "unknown")(payload)
    proves = bool(isinstance(product, Mapping) and product.get("proves_not_executed"))
    stages = getattr(protocol, "NOT_EXECUTED_STAGES", frozenset())
    if observed in stages:
        proves = True
    # A real evaluation failure is admissible only when the product's own
    # witness proves that an engine mutation-class call was issued.  A generic
    # non-success envelope, or a pre-dispatch refusal code, is not evidence
    # that evaluation ran.  This remains false for the common fail-closed
    # UNKNOWN envelope unless its nested product witness says post-dispatch
    # with mutation_issued=true.
    proves_dispatched = observed == "dispatched"
    if isinstance(product, Mapping):
        proves_dispatched = proves_dispatched or (
            product.get("mutation_issued") is True
            and product.get("stage") in {"post_dispatch", "dispatched"}
        )
    methods = product.get("methods") if isinstance(product, Mapping) else None
    evaluation_methods = [
        str(method) for method in methods if isinstance(method, str)
        and method.rsplit(".", 1)[-1].split("(", 1)[0].strip().lower() in _EVALUATION_METHOD_LEAVES
    ] if isinstance(methods, list) else []
    raw_getter_methods = [
        str(method) for method in methods if isinstance(method, str)
        and method.rsplit(".", 1)[-1].split("(", 1)[0].strip().lower() in _RAW_GETTER_METHOD_LEAVES
    ] if isinstance(methods, list) else []
    proves_evaluation_dispatched = bool(proves_dispatched and evaluation_methods)
    return {
        "observed_stage": observed,
        "product_dispatch": _safe(product),
        "proves_not_executed": proves,
        "proves_dispatched": proves_dispatched,
        "proves_evaluation_dispatched": proves_evaluation_dispatched,
        "evaluation_methods": evaluation_methods,
        "raw_getter_methods": raw_getter_methods,
        "proof_source": (
            "product_witness_or_published_dispatch_stage"
            if proves else
            "product_witness_post_dispatch"
            if proves_dispatched else
            "unproven"
        ),
    }


async def _pre_eval_refusal(ledger: _ExportLedger, case: str, operation: str,
                            body: Mapping[str, Any], expected_code: str | None = None) -> None:
    key, payload, mode = await _call(ledger, operation, body, case)
    if mode == "exception" or payload is None:
        ledger.add(case, _failure_status(payload, mode), operation=operation, key=key,
                   assertions={"evaluation_not_dispatched": mode == "halted",
                               "response_received": False},
                   reason="public refusal did not return a verifiable response")
        return
    proof = _dispatch_proof(payload)
    code = _error_code(payload)
    target = _response_path(payload)
    absent = target is None or not target.exists()
    passed = (not _success(payload) and proof["proves_not_executed"] and absent
              and (expected_code is None or code == expected_code))
    ledger.add(case, "PASS" if passed else _failure_status(payload, mode),
               operation=operation, key=key, response=payload,
               assertions={"rejected": not _success(payload), "expected_code": expected_code,
                            "actual_code": code, "no_file_side_effect": absent,
                            "evaluation_not_dispatched": proof["proves_not_executed"]},
               dispatch_proof=proof,
               reason=None if passed else "MCP witness did not prove a pre-evaluation refusal"
               if not proof["proves_not_executed"] else None)


def _destination(root: Path, run_name: str, suffix: str) -> Path:
    return root / "g2_artifacts" / "results" / f"g33_export_{_slug(run_name)}_{_slug(suffix)}"


async def _read_artifact(ledger: _ExportLedger, artifact: Path, expected_hash: str,
                         size: int, label: str) -> dict[str, Any]:
    reconstruction = ledger.run_dir / f"{_slug(label)}.reconstructed"
    reconstruction.unlink(missing_ok=True)
    digest = hashlib.sha256()
    offset = 0
    calls: list[dict[str, Any]] = []
    success = True
    halted = False
    chunk_payload_peak = 0
    chunk_encoded_peak = 0
    chunk_buffer_peak = 0
    while offset < size and len(calls) < _MAX_CHUNKS:
        length = min(_CHUNK_BYTES, size - offset)
        key, payload, mode = await _call(
            ledger, "artifact.read",
            {"artifact_id": str(artifact), "offset": offset, "length": length,
             "expected_sha256": expected_hash},
            f"{label}-chunk-{len(calls):04d}",
        )
        if mode == "exception" or payload is None:
            halted = mode == "halted" or ledger.stopped_unknown
            success = False
            break
        data = _data(payload)
        try:
            encoded = str(data["data_base64"])
            block = base64.b64decode(encoded, validate=True)
        except (KeyError, ValueError, TypeError):
            success = False
            calls.append({"key": key, "error": "invalid_base64"})
            break
        chunk_payload_peak = max(chunk_payload_peak, len(block))
        chunk_encoded_peak = max(chunk_encoded_peak, len(encoded))
        # This is the measured/observed pair of client chunk buffers.  It is
        # a transport-side observation, not a claim about Python object or
        # engine-cache overhead.
        chunk_buffer_peak = max(chunk_buffer_peak, len(block) + len(encoded))
        block_hash = hashlib.sha256(block).hexdigest()
        row = {
            "key": key, "offset": offset, "requested_length": length,
            "returned_length": len(block), "chunk_sha256": data.get("chunk_sha256"),
            "chunk_hash_matches": data.get("chunk_sha256") == block_hash,
            "whole_file_sha256": data.get("whole_file_sha256"),
        }
        calls.append(_safe(row))
        if not _success(payload) or len(block) == 0 or len(block) > length or not row["chunk_hash_matches"]:
            success = False
            break
        with reconstruction.open("ab") as handle:
            handle.write(block)
        digest.update(block)
        offset += len(block)
        if data.get("eof") is True and offset < size:
            success = False
            break
    reconstructed_size = reconstruction.stat().st_size if reconstruction.exists() else 0
    reconstructed_hash = digest.hexdigest()
    passed = success and offset == size and reconstructed_size == size and reconstructed_hash == expected_hash
    return {
        "pass": passed,
        "halted": halted or ledger.stopped_unknown,
        "expected_sha256": expected_hash,
        "reconstructed_sha256": reconstructed_hash,
        "expected_size": size,
        "reconstructed_size": reconstructed_size,
        "chunk_count": len(calls),
        "chunks": calls,
        "reconstruction_path": str(reconstruction),
        "chunk_memory": {
            "chunk_payload_peak_bytes": chunk_payload_peak,
            "chunk_encoded_peak_bytes": chunk_encoded_peak,
            "sampled_driver_chunk_buffer_peak_bytes": chunk_buffer_peak,
            "sampled_peak": True,
            "absolute_peak": False,
            "scope": "driver_artifact_read_chunk_buffers",
            "engine_internal_cache": "UNMEASURED",
        },
    }


async def run_export_cases(client: Any, run_dir: Path) -> dict[str, Any]:
    """Run bounded public-MCP C12/C13/F07-F09 cases against an already bound model.

    The function never creates or loads a model.  It does not dispatch a timeout/UNKNOWN probe:
    no public fault injector here proves destination absence and job quiescence together, so
    UNKNOWN and cleanup-failure remain explicit ``NOT_RUN`` rows rather than manufactured live
    evidence.
    """
    ledger = _ExportLedger(client, Path(run_dir))
    run_name = ledger.run_dir.name
    state = getattr(client, "state", {})
    if not isinstance(state, Mapping) or not isinstance(state.get("ref"), Mapping):
        ledger.add("precondition-bound-model", "BLOCKED", reason="ActionClient has no bound model_ref")
        return _finish(ledger)

    # Obtain a trusted root only from a successful product-created artifact.  We never use cwd or
    # the caller's run directory as an authorization root.
    base_spec = {
        "expressions": ["T"],
        "solution": {"dataset": "dset1", "solution": "sol1"},
        "aggregate": "none",
        "complex_mode": "real",
        "storage": "inline",
        "units": ["K"],
    }
    first_destination = f"g2_artifacts/results/g33_export_{_slug(run_name)}_json.json"
    first_body = {"spec": base_spec, "format": "json", "destination": first_destination}
    # A deliberately valid export gives the helper the backend's actual artifact root and is also
    # the JSON success case.  The placeholder is replaced after the response root is known only
    # when a caller supplied a prior root in client metadata; otherwise use the documented default
    # artifact directory relative to the trusted project root via a short first request.
    key, first, mode = await _call(ledger, "result.field_export", first_body, "json-initial")
    if mode == "exception" or first is None:
        ledger.add("json-success", "BLOCKED", operation="result.field_export", key=key,
                   reason="initial public result.field_export did not return a response")
        ledger.add("unknown-outcome", "NOT_RUN", reason="no live export root was established")
        ledger.add("cleanup-failure", "NOT_RUN", reason="no public cleanup fault injector was used")
        return _finish(ledger)
    if not _success(first):
        unknown_evidence = ledger.unknown_evidence[-1] if _unknown(first) and ledger.unknown_evidence else None
        product_code = _product_failure_code(first)
        status = "FAIL" if product_code else ("BLOCKED" if _unknown(first) else "FAIL")
        ledger.add("json-success", status,
                   operation="result.field_export", key=key, response=first,
                   assertions={
                       "unknown_job_queried": bool(unknown_evidence is not None),
                       "unknown_job_queries_complete": bool(unknown_evidence and unknown_evidence.get("queries_complete")),
                       "product_failure_code": product_code,
                   },
                   reason=(f"valid Chain-A JSON export exposed product defect {product_code}"
                           if product_code else "valid Chain-A JSON export did not succeed"))
        # The query set is retained as diagnostic evidence, but this branch has
        # no successful artifact root and does not verify the destination path.
        # Therefore it cannot certify no-publish even when reconciliation is
        # complete; keep the acceptance row explicitly NOT_RUN.
        ledger.add("unknown-outcome-no-publish", "NOT_RUN",
                   reason="UNKNOWN response has no verified artifact root; destination absence and job quiescence are not a no-publish proof",
                   assertions={"artifact_not_published": None,
                               "destination_absent": None,
                               "job_queries_complete": bool(unknown_evidence and unknown_evidence.get("queries_complete")),
                               "job_quiescent": bool(unknown_evidence and unknown_evidence.get("quiescent") is True)})
        ledger.add("cleanup-failure", "NOT_RUN", reason="no public cleanup fault injector was used")
        return _finish(ledger)

    first_path = _response_path(first)
    if first_path is None or not first_path.is_file():
        ledger.add("json-success", "FAIL", operation="result.field_export", key=key, response=first,
                   reason="successful response did not publish a readable file_path")
        return _finish(ledger)
    ledger.artifact_root = first_path.parent.parent.parent
    json_path = first_path
    json_size, json_hash = _stream_hash(json_path)
    json_document: dict[str, Any] | None = None
    if json_size <= _MAX_METADATA_BYTES:
        try:
            with json_path.open(encoding="utf-8") as handle:
                decoded = json.load(handle)
            json_document = decoded if isinstance(decoded, dict) else None
        except (OSError, ValueError, TypeError):
            json_document = None
    metadata = _field_metadata(json_document or {})
    json_data = _data(first)
    json_assertions = {
        "response_success": _success(first),
        "file_exists": json_path.is_file(),
        "hash_matches_response": json_hash == json_data.get("sha256"),
        "nonempty": json_size > 0,
        "finite_values": _finite_json(json_document),
        "field_metadata_complete": metadata.get("complete") is True,
        "metadata": metadata,
    }
    ledger.add("json-success", "PASS" if all(json_assertions[name] for name in
                                              ("response_success", "file_exists", "hash_matches_response",
                                               "nonempty", "finite_values", "field_metadata_complete")) else "FAIL",
               operation="result.field_export", key=key, response=first, assertions=json_assertions,
               path=str(json_path), local_sha256=json_hash, local_size=json_size)

    # C13: derive one bounded over-limit request from the successful native
    # point-shape/solution-axis witness.  The request is only dispatched when
    # the current public expression contract can carry it; a mathematically
    # valid plan that exceeds that contract is recorded as NOT_RUN rather than
    # being mislabeled as a budget refusal.
    budget_plan = _plan_budget_probe(
        json_document or {}, seed_expression=str(base_spec["expressions"][0])
    )
    budget_plan_summary = {key: value for key, value in budget_plan.items() if key != "expressions"}
    budget_path = _destination(ledger.artifact_root, run_name, "budget-refusal").with_suffix(".json")
    if not budget_plan.get("ready"):
        ledger.add(
            "result-budget-refusal-no-getdata", "NOT_RUN",
            operation="result.field_export",
            assertions={
                "plan_only": True,
                "runtime_verified": False,
                "budget_plan": budget_plan_summary,
                "destination_absent": not (budget_path.exists() or budget_path.is_symlink()),
                "engine_internal_cache": "UNMEASURED",
            },
            reason=str(budget_plan.get("reason") or "bounded C13 plan was not dispatchable"),
        )
    else:
        budget_expressions = list(budget_plan["expressions"])
        declared_units = base_spec.get("units")
        budget_units = ([str(declared_units[0])] * len(budget_expressions)
                        if isinstance(declared_units, list) and declared_units else None)
        budget_spec = {**base_spec, "expressions": budget_expressions}
        if budget_units is not None:
            budget_spec["units"] = budget_units
        budget_inventory_root = budget_path.parent
        budget_inventory_before = _artifact_inventory(budget_inventory_root)
        budget_key, budget_payload, budget_mode = await _call(
            ledger,
            "result.field_export",
            {"spec": budget_spec, "format": "json", "destination": str(budget_path)},
            "result-budget-refusal",
        )
        budget_inventory_after = _artifact_inventory(budget_inventory_root)
        budget_before_paths = set(budget_inventory_before.get("paths", []))
        budget_after_paths = set(budget_inventory_after.get("paths", []))
        budget_new_paths = sorted(budget_after_paths - budget_before_paths)
        budget_inventory_complete = bool(
            budget_inventory_before.get("complete") is True
            and budget_inventory_after.get("complete") is True
        )
        budget_inventory_no_new = budget_inventory_complete and not budget_new_paths
        budget_exists = budget_path.exists() or budget_path.is_symlink()
        budget_call_record = next(
            (record for record in reversed(ledger.calls)
             if record.get("idempotency_key") == budget_key),
            {},
        )
        budget_proof = _dispatch_proof(budget_payload)
        budget_codes = set(_nested_codes(budget_payload))
        budget_reason_codes = sorted(
            code for code in _BUDGET_REFUSAL_CODES
            if code in budget_codes or _contains_code_text(budget_payload, code)
        )
        budget_unknown = bool(_unknown(budget_payload))
        budget_unknown_evidence = (
            _unknown_evidence_for(ledger, budget_payload) if budget_unknown else None
        )
        budget_unknown_reconciled = bool(
            budget_unknown_evidence
            and budget_unknown_evidence.get("queries_complete") is True
            and budget_unknown_evidence.get("quiescent") is True
            and budget_unknown_evidence.get("model_reconciled") is True
        )
        budget_raw_getter_methods = budget_proof.get("raw_getter_methods", [])
        budget_refusal_pass = (
            budget_mode == "response"
            and not _success(budget_payload)
            and bool(budget_reason_codes)
            and budget_proof.get("proves_evaluation_dispatched") is True
            and not budget_raw_getter_methods
            and not budget_exists
            and budget_inventory_no_new
            and (not budget_unknown or budget_unknown_reconciled)
        )
        budget_classification = (
            "LIVE_NATIVE_RESULT_BUDGET_REFUSAL_UNKNOWN_PROPAGATED_NO_GETDATA"
            if budget_refusal_pass and budget_unknown else
            "LIVE_NATIVE_RESULT_BUDGET_REFUSAL_NO_GETDATA"
            if budget_refusal_pass else None
        )
        budget_assertions = {
            "plan_only": False,
            "runtime_verified": budget_refusal_pass,
            "budget_plan": budget_plan_summary,
            "budget_reason_codes": budget_reason_codes,
            "rejected": not _success(budget_payload),
            "dispatch_mutation_issued": budget_proof.get("proves_dispatched") is True,
            "evaluation_dispatched": budget_proof.get("proves_evaluation_dispatched") is True,
            "evaluation_methods": budget_proof.get("evaluation_methods", []),
            "raw_getter_methods": budget_raw_getter_methods,
            "raw_getter_witness_absent": not budget_raw_getter_methods,
            "getdata_called": False if not budget_raw_getter_methods else None,
            "unknown": budget_unknown,
            "unknown_state_preserved": budget_unknown if budget_refusal_pass else None,
            "unknown_reconciled_quiescent": budget_unknown_reconciled if budget_unknown else None,
            "destination_absent": not budget_exists,
            "artifact_inventory_complete": budget_inventory_complete,
            "artifact_inventory_no_new": budget_inventory_no_new,
            "artifact_inventory_new_paths": budget_new_paths,
            "classification": budget_classification,
            "driver_memory_sample": budget_call_record.get("memory_sample"),
            "memory_scope": "driver_process_sampled_peak_only",
            "engine_internal_cache": "UNMEASURED",
        }
        ledger.add(
            "result-budget-refusal-no-getdata",
            "PASS" if budget_refusal_pass else _failure_status(budget_payload, budget_mode),
            operation="result.field_export",
            key=budget_key,
            response=budget_payload,
            assertions=budget_assertions,
            dispatch_proof=budget_proof,
            unknown_reconciliation=budget_unknown_evidence if budget_unknown else None,
            artifact_inventory={"before": budget_inventory_before, "after": budget_inventory_after},
            reason=(
                None if budget_refusal_pass else
                "response did not prove a bounded native budget refusal before raw getter/publish"
            ),
        )

    # A second successful export exercises the CSV serializer and its explicit row contract.
    csv_target = _destination(ledger.artifact_root, run_name, "csv").with_suffix(".csv")
    csv_key, csv_payload, csv_mode = await _call(
        ledger, "result.field_export",
        {"spec": base_spec, "format": "csv", "destination": str(csv_target)}, "csv-success",
    )
    csv_path = _response_path(csv_payload)
    csv_header: list[str] = []
    csv_rows = 0
    csv_finite = True
    if csv_path is not None and csv_path.is_file():
        try:
            with csv_path.open(newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                csv_header = list(reader.fieldnames or [])
                for row in reader:
                    csv_rows += 1
                    csv_finite = csv_finite and all(
                        _finite_column(row.get(column, ""))
                        for column in ("outer", "inner", "point", "real", "imag",
                                       "coord_0", "coord_1", "coord_2")
                    )
        except (OSError, csv.Error, UnicodeError):
            csv_finite = False
    required_columns = {"axis", "expr", "outer", "inner", "point", "real", "imag", "unit",
                        "coord_0", "coord_1", "coord_2"}
    csv_assertions = {
        "response_success": csv_mode == "response" and _success(csv_payload),
        "file_exists": csv_path is not None and csv_path.is_file(),
        "required_columns": required_columns.issubset(set(csv_header)),
        "has_rows": csv_rows > 0,
        "finite_real_imag": csv_finite,
    }
    ledger.add("csv-success", "PASS" if all(csv_assertions.values()) else
               _failure_status(csv_payload, csv_mode),
               operation="result.field_export", key=csv_key, response=csv_payload,
               assertions={**csv_assertions, "header": csv_header, "rows": csv_rows},
               path=str(csv_path) if csv_path else None)

    # Existing target and explicit overwrite.  The first JSON artifact is task-created and is the
    # only target reused; no user file is probed.
    old_size, old_hash = _stream_hash(json_path)
    overwrite_key, overwrite_payload, overwrite_mode = await _call(
        ledger, "result.field_export",
        {"spec": base_spec, "format": "json", "destination": str(json_path)}, "overwrite-default",
    )
    overwrite_code = _error_code(overwrite_payload)
    overwrite_pass = (overwrite_mode == "response" and not _success(overwrite_payload)
                      and overwrite_code == "DESTINATION_EXISTS"
                      and _stream_hash(json_path)[1] == old_hash)
    ledger.add("overwrite-default-refused", "PASS" if overwrite_pass else
               _failure_status(overwrite_payload, overwrite_mode),
               operation="result.field_export", key=overwrite_key, response=overwrite_payload,
               assertions={"rejected": not _success(overwrite_payload), "code": overwrite_code,
                            "old_hash_preserved": _stream_hash(json_path)[1] == old_hash})

    explicit_key, explicit_payload, explicit_mode = await _call(
        ledger, "result.field_export",
        {"spec": base_spec, "format": "json", "destination": str(json_path), "overwrite": True},
        "overwrite-explicit",
    )
    explicit_hash = _stream_hash(json_path)[1]
    explicit_data = _data(explicit_payload)
    explicit_pass = (explicit_mode == "response" and _success(explicit_payload)
                     and explicit_hash == explicit_data.get("sha256")
                     and explicit_hash != "")
    ledger.add("overwrite-explicit", "PASS" if explicit_pass else
               _failure_status(explicit_payload, explicit_mode),
               operation="result.field_export", key=explicit_key, response=explicit_payload,
               assertions={"success": _success(explicit_payload), "old_hash": old_hash,
                            "new_hash": explicit_hash, "response_hash_matches": explicit_hash == explicit_data.get("sha256"),
                            "overwrite_contract": explicit_pass})

    # Pre-evaluation path/format probes use only run-owned sentinel names.  The product witness is
    # required; an error code alone never proves that evaluation was skipped.
    escape_body = {"spec": base_spec, "format": "json", "destination": "../../g33_export_escape_probe.json"}
    await _pre_eval_refusal(ledger, "path-escape-before-evaluate", "result.field_export", escape_body,
                            expected_code="ACCESS_VIOLATION")

    unknown_format_body = {"spec": base_spec, "format": "yaml",
                           "destination": str(_destination(ledger.artifact_root, run_name, "unknown").with_suffix(".yaml"))}
    await _pre_eval_refusal(ledger, "unknown-format-before-evaluate", "result.field_export", unknown_format_body,
                            expected_code="API_UNSUPPORTED")

    symlink_case = ledger.artifact_root / f".g33_export_symlink_{_slug(run_name)}"
    symlink_target = ledger.artifact_root / f".g33_export_symlink_target_{_slug(run_name)}"
    symlink_ready = False
    try:
        symlink_target.write_text("task sentinel\n", encoding="utf-8")
        symlink_case.unlink(missing_ok=True)
        symlink_case.symlink_to(symlink_target)
        symlink_ready = True
        await _pre_eval_refusal(
            ledger, "symlink-before-evaluate", "result.field_export",
            {"spec": base_spec, "format": "json", "destination": str(symlink_case)},
            expected_code="ACCESS_VIOLATION",
        )
    except (OSError, NotImplementedError) as exc:
        ledger.add("symlink-before-evaluate", "NOT_RUN", operation="result.field_export",
                   reason=f"task sentinel symlink could not be created: {type(exc).__name__}: {exc}")
    finally:
        if symlink_ready:
            symlink_case.unlink(missing_ok=True)
        symlink_target.unlink(missing_ok=True)

    # A real upstream evaluation failure: an invalid expression is sent to the bound model.  No
    # file is accepted even if a product accidentally returns success with an empty payload.
    failure_path = _destination(ledger.artifact_root, run_name, "evaluation-failure").with_suffix(".json")
    # The trusted project root may contain source, venv, and private runtime
    # state.  Inventory only the actual artifact subtree that contains the
    # requested destination, keeping the no-publish proof bounded and scoped.
    failure_inventory_root = failure_path.parent
    failure_inventory_before = _artifact_inventory(failure_inventory_root)
    failure_key, failure_payload, failure_mode = await _call(
        ledger, "result.field_export",
        {"spec": {**base_spec, "expressions": ["__G33_INVALID_EXPRESSION__"]},
         "format": "json", "destination": str(failure_path)}, "evaluation-failure",
    )
    failure_inventory_after = _artifact_inventory(failure_inventory_root)
    failure_before_paths = set(failure_inventory_before.get("paths", []))
    failure_after_paths = set(failure_inventory_after.get("paths", []))
    failure_new_paths = sorted(failure_after_paths - failure_before_paths)
    failure_inventory_complete = bool(
        failure_inventory_before.get("complete") is True
        and failure_inventory_after.get("complete") is True
    )
    failure_inventory_no_new = failure_inventory_complete and not failure_new_paths
    failure_exists = failure_path.exists() or failure_path.is_symlink()
    failure_proof = _dispatch_proof(failure_payload)
    failure_codes = set(_nested_codes(failure_payload))
    outer_failure_code = _error_code(failure_payload)
    if outer_failure_code:
        failure_codes.add(outer_failure_code)
    evaluation_failure_code = next(
        (code for code in sorted(failure_codes) if code in _EVALUATION_FAILURE_CODES), None
    )
    unknown_evidence = _unknown_evidence_for(ledger, failure_payload) if _unknown(failure_payload) else None
    unknown_reconciled = bool(
        unknown_evidence
        and unknown_evidence.get("queries_complete") is True
        and unknown_evidence.get("quiescent") is True
        and unknown_evidence.get("model_reconciled") is True
    )
    ordinary_failure_pass = (
        failure_mode == "response"
        and not _success(failure_payload)
        and not _unknown(failure_payload)
        and not failure_exists
        and evaluation_failure_code is not None
        and failure_proof["proves_evaluation_dispatched"]
        and failure_inventory_no_new
    )
    # A real engine evaluation exception is commonly wrapped by the managed
    # service as EXECUTION_STATE_UNKNOWN.  Preserve that dangerous state, but
    # allow this negative control to certify no publish only after the original
    # job's four public observations, quiescence, clean model refresh, and a
    # before/after artifact inventory all prove the bounded no-publish claim.
    unknown_failure_pass = (
        failure_mode == "response"
        and not _success(failure_payload)
        and bool(_unknown(failure_payload))
        and evaluation_failure_code is not None
        and failure_proof["proves_evaluation_dispatched"]
        and not failure_exists
        and unknown_reconciled
        and failure_inventory_no_new
    )
    failure_pass = ordinary_failure_pass or unknown_failure_pass
    failure_classification = (
        "LIVE_NATIVE_EVALUATION_FAILURE_UNKNOWN_PROPAGATED_NO_PUBLISH"
        if unknown_failure_pass else
        "LIVE_NATIVE_EVALUATION_FAILURE_NO_PUBLISH"
        if ordinary_failure_pass else
        None
    )
    failure_assertions = {
        "rejected": not _success(failure_payload),
        "destination_absent": not failure_exists,
        "error_code": outer_failure_code,
        "evaluation_failure_code": evaluation_failure_code,
        "dispatch_mutation_issued": failure_proof["proves_dispatched"],
        "evaluation_dispatched": failure_proof["proves_evaluation_dispatched"],
        "evaluation_methods": failure_proof["evaluation_methods"],
        "unknown": bool(_unknown(failure_payload)),
        "unknown_state_preserved": bool(_unknown(failure_payload)) if unknown_failure_pass else None,
        "unknown_reconciled_quiescent": unknown_reconciled if unknown_failure_pass else None,
        "artifact_inventory_complete": failure_inventory_complete,
        "artifact_inventory_no_new": failure_inventory_no_new,
        "artifact_inventory_new_paths": failure_new_paths,
        "classification": failure_classification,
    }
    ledger.add("evaluation-failure-no-publish", "PASS" if failure_pass else
               _failure_status(failure_payload, failure_mode),
               operation="result.field_export", key=failure_key, response=failure_payload,
               assertions=failure_assertions,
               dispatch_proof=failure_proof,
               unknown_reconciliation=unknown_evidence if unknown_failure_pass else None,
               artifact_inventory={"before": failure_inventory_before, "after": failure_inventory_after},
               reason=("response did not prove a dispatched evaluation failure; preflight, unresolved UNKNOWN, or "
                        "a new/unchecked artifact is not accepted"
                        if not failure_pass else None))

    # Artifact read is a public bounded request.  It reconstructs to a run-owned file and never
    # uses read_bytes/read-all in memory.  The subsequent append/truncate is a task-created tamper
    # sentinel and restores the original size before the function returns.
    read_result = await _read_artifact(ledger, json_path, json_hash, json_size, "json-artifact")
    ledger.add("artifact-read-reconstruct", "PASS" if read_result["pass"] else
               ("BLOCKED" if read_result.get("halted") else "FAIL"),
               operation="artifact.read", assertions=read_result)

    wrong_range_key, wrong_range, wrong_mode = await _call(
        ledger, "artifact.read", {"artifact_id": str(json_path), "offset": json_size + 1, "length": 16},
        "artifact-wrong-range",
    )
    ledger.add("artifact-read-range-refusal", "PASS" if wrong_mode == "response" and
               _error_code(wrong_range) in {"INVALID_CHUNK_RANGE", "INVALID_REQUEST"} else
               _failure_status(wrong_range, wrong_mode),
               operation="artifact.read", key=wrong_range_key, response=wrong_range,
               assertions={"error_code": _error_code(wrong_range), "rejected": not _success(wrong_range)})

    tamper_pass = False
    tamper_reason = None
    try:
        with json_path.open("ab") as handle:
            handle.write(b"\nG33-TAMPER-SENTINEL\n")
        tamper_key, tamper_payload, tamper_mode = await _call(
            ledger, "artifact.read",
            {"artifact_id": str(json_path), "offset": 0, "length": min(_CHUNK_BYTES, json_size),
             "expected_sha256": json_hash},
            "artifact-tamper-detection",
        )
        tamper_pass = tamper_mode == "response" and _error_code(tamper_payload) == "ARTIFACT_HASH_MISMATCH"
        ledger.add("artifact-read-tamper-detection", "PASS" if tamper_pass else
                   _failure_status(tamper_payload, tamper_mode),
                   operation="artifact.read", key=tamper_key, response=tamper_payload,
                   assertions={"expected_hash_mismatch": tamper_pass})
    except OSError as exc:
        tamper_reason = f"tamper sentinel failed: {type(exc).__name__}: {exc}"
        ledger.add("artifact-read-tamper-detection", "NOT_RUN", operation="artifact.read", reason=tamper_reason)
    finally:
        try:
            with json_path.open("r+b") as handle:
                handle.truncate(json_size)
        except OSError as exc:
            ledger.add("artifact-restore-after-tamper", "FAIL", reason=f"could not restore artifact size: {exc}")
    if not tamper_pass and tamper_reason:
        ledger.add("artifact-restore-after-tamper", "NOT_RUN", reason=tamper_reason)

    outside = ledger.artifact_root.parent / f"g33_export_outside_{_slug(run_name)}"
    outside_key, outside_payload, outside_mode = await _call(
        ledger, "artifact.read", {"artifact_id": str(outside), "offset": 0, "length": 16},
        "artifact-read-access-refusal",
    )
    ledger.add("artifact-read-access-refusal", "PASS" if outside_mode == "response" and
               _error_code(outside_payload) == "ACCESS_VIOLATION" else
               _failure_status(outside_payload, outside_mode),
               operation="artifact.read", key=outside_key, response=outside_payload,
               assertions={"error_code": _error_code(outside_payload), "not_created": not outside.exists()})

    # No public operation in this helper can safely force an UNKNOWN engine
    # state.  Never dispatch a timeout probe: a timeout response cannot prove
    # destination absence or quiescence.  A real invalid-expression request
    # that propagated UNKNOWN is handled above and may be referenced here only
    # after its completed reconciliation and inventory proof.
    if unknown_failure_pass:
        ledger.add(
            "unknown-outcome-no-publish", "PASS",
            operation="result.field_export", key=failure_key, response=failure_payload,
            assertions={
                "source_case": "evaluation-failure-no-publish",
                "classification": failure_classification,
                "unknown_state_preserved": True,
                "job_queries_complete": True,
                "job_quiescent": True,
                "model_reconciled": True,
                "destination_absent": True,
                "artifact_inventory_no_new": True,
                "timeout_probe_dispatched": False,
            },
            reason="real native evaluation failure propagated UNKNOWN; no publish proved after quiescent reconciliation",
        )
    else:
        ledger.add(
            "unknown-outcome-no-publish", "NOT_RUN",
            reason="no safe public fault injector; timeout cannot prove destination absence or job quiescence",
        )
    ledger.add("cleanup-failure-no-publish", "NOT_RUN",
               reason="no public cleanup fault injector; no synthetic cleanup_failed response was accepted")

    call_keys = [row.get("idempotency_key") for row in ledger.calls]
    request_discipline = {
        "call_count": len(ledger.calls),
        "unique_idempotency_keys": len(call_keys) == len(set(call_keys)),
        "all_reconcile_false": all(row.get("reconcile") is False for row in ledger.calls),
        "all_body_hashes_recorded": all(isinstance(row.get("body_sha256"), str) and row["body_sha256"]
                                         for row in ledger.calls),
    }
    ledger.add("request-discipline", "PASS" if all(request_discipline.values()) else "FAIL",
               assertions=request_discipline)

    return _finish(ledger)


__all__ = ["run_export_cases"]
