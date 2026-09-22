"""Fail-closed result memory budgets.

This module deliberately has no COMSOL, Java, worker, or filesystem
dependencies.  A caller supplies metadata that was obtained *before* a raw
numerical getter is invoked.  The planner only permits the getter when the
expression, solution axes, point count, raw layout, and resource limits are
all explicit and verified.

In particular, ``getNData()`` is retained as observation metadata only.  It
is never used as a coordinate or point count.  COMSOL's documented raw data
shape is ``[expression][solnum][vertex]``; an adapter must therefore provide
an independently justified upper bound for Eval-like features.  Interp is
allowed to derive that bound from explicitly supplied interpolation
coordinates and a verified solution binding.

The memory records distinguish client transport/array observations from the
COMSOL Worker/engine's internal cache.  The latter remains ``UNMEASURED``
unless a separate engine telemetry witness is supplied by a future adapter.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Sequence


RAW_ARRAY_GETTERS = frozenset(
    {
        "getData",
        "getImagData",
    }
)
_INTERP_KINDS = frozenset({"interp", "interpolation"})


class ResultBudgetRefused(RuntimeError):
    """Raised by :func:`require_getdata_budget` before an unsafe getter call."""

    def __init__(self, decision: Mapping[str, Any]) -> None:
        self.decision = dict(decision)
        code = self.decision.get("reason_code", "RESULT_BUDGET_REFUSED")
        reason = self.decision.get("reason", "result data budget was not admitted")
        super().__init__(f"{code}: {reason}")


def _integer(value: Any, name: str, *, positive: bool = True) -> tuple[int | None, str | None]:
    if isinstance(value, bool) or not isinstance(value, int):
        return None, f"{name} must be an integer"
    if (positive and value <= 0) or (not positive and value < 0):
        qualifier = "positive" if positive else "non-negative"
        return None, f"{name} must be {qualifier}"
    return value, None


def _base_result(
    *,
    operation: str,
    feature_kind: str,
    raw_getter: str,
    raw_layout: str,
    expression_count: Any,
    point_count: Any,
    outer_count: Any,
    inner_count: Any,
    inner_counts_by_outer: Sequence[int] | None,
    complex_components: Any,
    scalar_bytes: Any,
    max_elements: Any,
    max_bytes: Any,
    reported_data_vector_count: Any,
) -> dict[str, Any]:
    return {
        "status": "FAIL",
        "allowed": False,
        "verification": "INVALID",
        "reason_code": "RESULT_BUDGET_INVALID",
        "reason": "result budget request is invalid",
        "operation": operation,
        "feature_kind": feature_kind,
        "raw_getter": raw_getter,
        "raw_layout": raw_layout,
        "pre_getdata": True,
        "getdata_called": False,
        "publish_allowed": False,
        "requested": {
            "expression_count": expression_count,
            "point_count": point_count,
            "outer_count": outer_count,
            "inner_count": inner_count,
            "inner_counts_by_outer": list(inner_counts_by_outer)
            if inner_counts_by_outer is not None
            else None,
            "complex_components": complex_components,
            "scalar_bytes": scalar_bytes,
            "max_elements": max_elements,
            "max_bytes": max_bytes,
            "reported_data_vector_count": reported_data_vector_count,
            "raw_point_upper_bound": None,
            "raw_upper_bound_verified": False,
            "raw_upper_bound_source": None,
        },
        "computed": None,
        "limits": {
            "max_elements": max_elements,
            "max_bytes": max_bytes,
            "max_numeric_payload_bytes": max_bytes,
        },
        "budget_scope": "numeric_payload_only",
        "data_vector_count": {
            "value": reported_data_vector_count,
            "used_for_point_bound": False,
            "interpretation": "observation only; never treated as coordinate count",
        },
    }


def _reject(
    result: dict[str, Any],
    reason_code: str,
    reason: str,
    *,
    verification: str = "UNVERIFIED",
) -> dict[str, Any]:
    result.update(
        {
            "status": "BLOCKED" if verification == "UNVERIFIED" else "FAIL",
            "allowed": False,
            "verification": verification,
            "reason_code": reason_code,
            "reason": reason,
            "publish_allowed": False,
        }
    )
    return result


def plan_result_budget(
    *,
    expression_count: int | None,
    point_count: int | None,
    outer_count: int | None,
    inner_count: int | None = None,
    inner_counts_by_outer: Sequence[int] | None = None,
    max_elements: int | None,
    max_bytes: int | None,
    operation: str = "result.evaluate",
    feature_kind: str = "Interp",
    raw_getter: str = "getData",
    raw_layout: str = "expression,solnum,vertex",
    expression_count_verified: bool = False,
    point_count_verified: bool = False,
    solution_axes_verified: bool = False,
    expression_source: str | None = None,
    point_count_source: str | None = None,
    solution_axes_source: str | None = None,
    raw_point_upper_bound: int | None = None,
    raw_point_upper_bounds_by_outer: Sequence[int] | None = None,
    raw_upper_bound_verified: bool = False,
    raw_upper_bound_source: str | None = None,
    complex_components: int = 1,
    scalar_bytes: int = 8,
    reported_data_vector_count: int | None = None,
) -> dict[str, Any]:
    """Plan the largest raw result allocation before ``getData``.

    ``point_count`` is a coordinate/sample count, never an inferred list
    length.  ``inner_counts_by_outer`` is accepted for bindings with a
    genuinely ragged inner axis and is summed only after the caller marks the
    solution metadata verified.  The raw upper bound is a count of samples
    per solution vector, not a data-vector count.

    For ``Interp`` with verified coordinates, the explicit coordinate count is
    an exact upper bound.  For ``Eval`` and other raw solution features the
    adapter must pass ``raw_point_upper_bound`` together with a source and
    ``raw_upper_bound_verified=True``.  This prevents ``getNData`` from being
    silently promoted into a point count.
    """

    result = _base_result(
        operation=operation,
        feature_kind=str(feature_kind),
        raw_getter=str(raw_getter),
        raw_layout=str(raw_layout),
        expression_count=expression_count,
        point_count=point_count,
        outer_count=outer_count,
        inner_count=inner_count,
        inner_counts_by_outer=inner_counts_by_outer,
        complex_components=complex_components,
        scalar_bytes=scalar_bytes,
        max_elements=max_elements,
        max_bytes=max_bytes,
        reported_data_vector_count=reported_data_vector_count,
    )
    if raw_point_upper_bound is not None and raw_point_upper_bounds_by_outer is not None:
        return _reject(
            result,
            "RAW_POINT_BOUND_AMBIGUOUS",
            "provide raw_point_upper_bound or raw_point_upper_bounds_by_outer, not both",
            verification="INVALID",
        )
    raw_point_bound_values: tuple[int, ...] | None = None
    if raw_point_upper_bounds_by_outer is not None:
        if isinstance(raw_point_upper_bounds_by_outer, (str, bytes)):
            return _reject(
                result,
                "RAW_POINT_UPPER_BOUND_INVALID",
                "raw_point_upper_bounds_by_outer must be a sequence of integers",
                verification="INVALID",
            )
        try:
            raw_point_bound_values = tuple(raw_point_upper_bounds_by_outer)
            result["requested"]["raw_point_upper_bounds_by_outer"] = list(raw_point_bound_values)
        except TypeError:
            return _reject(
                result,
                "RAW_POINT_UPPER_BOUND_INVALID",
                "raw_point_upper_bounds_by_outer must be iterable",
                verification="INVALID",
            )

    if not isinstance(operation, str) or not operation.strip():
        return _reject(result, "OPERATION_INVALID", "operation must be a non-empty string", verification="INVALID")
    if not isinstance(feature_kind, str) or not feature_kind.strip():
        return _reject(result, "FEATURE_KIND_INVALID", "feature_kind must be a non-empty string", verification="INVALID")
    if not isinstance(raw_getter, str) or not raw_getter.strip():
        return _reject(result, "RAW_GETTER_INVALID", "raw_getter must be a non-empty string", verification="INVALID")
    if not isinstance(raw_layout, str) or not raw_layout.strip():
        return _reject(result, "RAW_LAYOUT_INVALID", "raw_layout must be a non-empty string", verification="INVALID")

    expr, error = _integer(expression_count, "expression_count")
    if error:
        return _reject(result, "EXPRESSION_COUNT_INVALID", error, verification="INVALID")
    points, error = _integer(point_count, "point_count")
    if error:
        return _reject(result, "POINT_COUNT_INVALID", error, verification="INVALID")
    outers, error = _integer(outer_count, "outer_count")
    if error:
        return _reject(result, "OUTER_COUNT_INVALID", error, verification="INVALID")

    if inner_counts_by_outer is not None and inner_count is not None:
        return _reject(
            result,
            "INNER_AXIS_AMBIGUOUS",
            "provide inner_count or inner_counts_by_outer, not both",
            verification="INVALID",
        )
    if inner_counts_by_outer is not None:
        if isinstance(inner_counts_by_outer, (str, bytes)):
            return _reject(result, "INNER_AXIS_INVALID", "inner_counts_by_outer must be a sequence of integers", verification="INVALID")
        try:
            inner_values = tuple(inner_counts_by_outer)
        except TypeError:
            return _reject(result, "INNER_AXIS_INVALID", "inner_counts_by_outer must be iterable", verification="INVALID")
        if len(inner_values) != outers:
            return _reject(
                result,
                "INNER_AXIS_OUTER_MISMATCH",
                "inner_counts_by_outer length must equal outer_count",
                verification="INVALID",
            )
        checked_inner: list[int] = []
        for index, value in enumerate(inner_values):
            checked, error = _integer(value, f"inner_counts_by_outer[{index}]")
            if error:
                return _reject(result, "INNER_COUNT_INVALID", error, verification="INVALID")
            checked_inner.append(checked)
        inner_total = sum(checked_inner)
        inner_counts_for_total = checked_inner
        result["requested"]["inner_counts_by_outer"] = checked_inner
        result["requested"]["inner_count"] = None
    else:
        inner, error = _integer(inner_count, "inner_count")
        if error:
            return _reject(result, "INNER_COUNT_INVALID", error, verification="INVALID")
        checked_inner = None
        inner_total = outers * inner
        inner_counts_for_total = [inner] * outers

    components, error = _integer(complex_components, "complex_components")
    if error or components not in (1, 2):
        return _reject(
            result,
            "COMPLEX_COMPONENTS_INVALID",
            error or "complex_components must be 1 (real) or 2 (real+imaginary)",
            verification="INVALID",
        )
    scalar, error = _integer(scalar_bytes, "scalar_bytes")
    if error:
        return _reject(result, "SCALAR_BYTES_INVALID", error, verification="INVALID")
    max_elems, error = _integer(max_elements, "max_elements")
    if max_elements is None:
        return _reject(
            result,
            "BUDGET_LIMIT_UNVERIFIED",
            "max_elements must be explicitly configured before getData",
        )
    if error:
        return _reject(result, "ELEMENT_LIMIT_INVALID", error, verification="INVALID")
    max_mem, error = _integer(max_bytes, "max_bytes")
    if max_bytes is None:
        return _reject(
            result,
            "BUDGET_LIMIT_UNVERIFIED",
            "max_bytes must be explicitly configured before getData",
        )
    if error:
        return _reject(result, "BYTE_LIMIT_INVALID", error, verification="INVALID")
    if reported_data_vector_count is not None:
        checked_vectors, error = _integer(reported_data_vector_count, "reported_data_vector_count")
        if error:
            return _reject(result, "DATA_VECTOR_COUNT_INVALID", error, verification="INVALID")
        result["data_vector_count"]["value"] = checked_vectors

    if not expression_count_verified or not expression_source:
        return _reject(
            result,
            "EXPRESSION_METADATA_UNVERIFIED",
            "expression count must come from a verified expression binding before getData",
        )
    if not point_count_verified or not point_count_source:
        return _reject(
            result,
            "POINT_COUNT_UNVERIFIED",
            "point count must come from verified coordinates or another explicit point contract",
        )
    if not solution_axes_verified or not solution_axes_source:
        return _reject(
            result,
            "SOLUTION_AXES_UNVERIFIED",
            "outer/inner counts must come from a verified solution binding",
        )

    kind = feature_kind.strip().lower()
    getter_is_raw_array = raw_getter in RAW_ARRAY_GETTERS
    if getter_is_raw_array:
        layout = raw_layout.replace(" ", "").lower()
        if layout not in {"expression,solnum,vertex", "expr,solnum,vertex"}:
            return _reject(
                result,
                "RAW_LAYOUT_UNVERIFIED",
                "raw getter layout must be the documented [expression][solnum][vertex] layout",
            )
        raw_points_by_outer: list[int] | None = None
        if raw_point_bound_values is not None:
            raw_values = raw_point_bound_values
            if len(raw_values) != outers:
                return _reject(
                    result,
                    "RAW_POINT_BOUND_OUTER_MISMATCH",
                    "raw point upper-bound list must contain one value per outer solution",
                    verification="INVALID",
                )
            raw_points_by_outer = []
            for index, value in enumerate(raw_values):
                checked, error = _integer(value, f"raw_point_upper_bounds_by_outer[{index}]")
                if error:
                    return _reject(result, "RAW_POINT_UPPER_BOUND_INVALID", error, verification="INVALID")
                if checked < points:
                    return _reject(
                        result,
                        "RAW_POINT_BOUND_TOO_SMALL",
                        "a per-outer raw point upper bound is smaller than the requested point count",
                        verification="INVALID",
                    )
                raw_points_by_outer.append(checked)
            raw_points = max(raw_points_by_outer)
            bound_source = raw_upper_bound_source
            bound_verified = raw_upper_bound_verified
        elif kind in _INTERP_KINDS and raw_point_upper_bound is None:
            # Interp's setInterpolationCoordinates/getCoordinates contract
            # makes a verified coordinate count an exact raw upper bound for
            # every outer solution when no per-outer shape witness is needed.
            raw_points = points
            raw_points_by_outer = [points] * outers
            bound_source = f"{point_count_source};interp-coordinate-columns"
            bound_verified = True
        else:
            raw_points = raw_point_upper_bound
            if raw_points is not None:
                raw_points_by_outer = [raw_points] * outers
            bound_source = raw_upper_bound_source
            bound_verified = raw_upper_bound_verified
        if not bound_verified or raw_points is None or not bound_source:
            return _reject(
                result,
                "RAW_POINT_UPPER_BOUND_UNVERIFIED",
                "raw getter requires an independently verified per-vector point upper bound; data-vector count is insufficient",
            )
        checked_raw_points, error = _integer(raw_points, "raw_point_upper_bound")
        if error:
            return _reject(result, "RAW_POINT_UPPER_BOUND_INVALID", error, verification="INVALID")
        if checked_raw_points < points:
            return _reject(
                result,
                "RAW_POINT_BOUND_TOO_SMALL",
                "raw point upper bound is smaller than the requested verified point count",
                verification="INVALID",
            )
        result["requested"].update(
            {
                "raw_point_upper_bound": checked_raw_points,
                "raw_point_upper_bounds_by_outer": raw_points_by_outer,
                "raw_upper_bound_verified": True,
                "raw_upper_bound_source": bound_source,
            }
        )
    else:
        return _reject(
            result,
            "RAW_GETTER_UNSUPPORTED",
            f"no pre-getData budget contract is registered for raw getter {raw_getter!r}",
        )

    # Use the raw upper bound, not the observed point count, for a conservative
    # allocation estimate.  This is still only the client-visible numeric
    # array estimate; COMSOL's internal cache is recorded separately.
    if raw_points_by_outer is None:
        return _reject(
            result,
            "RAW_POINT_UPPER_BOUND_UNVERIFIED",
            "no per-outer raw point upper bound was established",
        )
    total_scalar_values = expr * sum(
        inner_counts_for_total[index] * raw_points_by_outer[index]
        for index in range(outers)
    ) * components
    total_bytes = total_scalar_values * scalar
    computed = {
        "expression_count": expr,
        "outer_count": outers,
        "inner_count_total": inner_total,
        "inner_counts_by_outer": checked_inner,
        "requested_point_count": points,
        "raw_point_upper_bound": checked_raw_points,
        "raw_point_upper_bounds_by_outer": raw_points_by_outer,
        "complex_components": components,
        "scalar_bytes": scalar,
        "numeric_payload_scalar_bytes": scalar,
        "elements": total_scalar_values,
        "bytes": total_bytes,
        "numeric_payload_bytes": total_bytes,
        "numeric_payload_scope": "float64 scalar payload only; excludes JSON, wire, Python-list, and engine-cache overhead",
        "raw_upper_bound_source": bound_source,
    }
    result["computed"] = computed
    result["limits"]["max_numeric_payload_bytes"] = max_mem
    result["budget_scope"] = "numeric_payload_only"
    result["metadata_sources"] = {
        "expression": expression_source,
        "point_count": point_count_source,
        "solution_axes": solution_axes_source,
        "raw_point_upper_bound": bound_source,
    }
    if total_scalar_values > max_elems:
        return _reject(
            result,
            "ELEMENT_BUDGET_EXCEEDED",
            f"conservative result allocation {total_scalar_values} elements exceeds limit {max_elems}",
        )
    if total_bytes > max_mem:
        return _reject(
            result,
            "BYTE_BUDGET_EXCEEDED",
            f"conservative result allocation {total_bytes} bytes exceeds limit {max_mem}",
        )

    result.update(
        {
            "status": "PASS",
            "allowed": True,
            "verification": "VERIFIED",
            "reason_code": "BUDGET_WITHIN_LIMIT",
            "reason": "verified pre-getData result allocation is within both configured limits",
            "publish_allowed": True,
        }
    )
    return result


def plan_interp_budget(
    *,
    expression_count: int,
    point_count: int,
    outer_indices: Sequence[Any],
    inner_indices_by_outer: Sequence[Sequence[Any]],
    max_elements: int,
    max_bytes: int,
    complex_components: int = 1,
    scalar_bytes: int = 8,
    operation: str = "result.evaluate",
    expression_source: str = "validated-request-expressions",
    point_count_source: str = "validated-Interp-coordinates",
    solution_axes_source: str = "validated-SolutionBinding",
    raw_point_upper_bounds_by_outer: Sequence[int] | None = None,
    raw_upper_bound_source: str | None = None,
) -> dict[str, Any]:
    """Convenience planner for an Interp request with real solution axes."""

    outer_values = tuple(outer_indices)
    if len(outer_values) == 0:
        return plan_result_budget(
            expression_count=expression_count,
            point_count=point_count,
            outer_count=0,
            inner_counts_by_outer=(),
            max_elements=max_elements,
            max_bytes=max_bytes,
            operation=operation,
            feature_kind="Interp",
            expression_count_verified=False,
            point_count_verified=False,
            solution_axes_verified=False,
            expression_source=expression_source,
            point_count_source=point_count_source,
            solution_axes_source=solution_axes_source,
            complex_components=complex_components,
            scalar_bytes=scalar_bytes,
        )
    inner_values = tuple(tuple(values) for values in inner_indices_by_outer)
    return plan_result_budget(
        expression_count=expression_count,
        point_count=point_count,
        outer_count=len(outer_values),
        inner_counts_by_outer=tuple(len(values) for values in inner_values),
        max_elements=max_elements,
        max_bytes=max_bytes,
        operation=operation,
        feature_kind="Interp",
        expression_count_verified=True,
        point_count_verified=True,
        solution_axes_verified=True,
        expression_source=expression_source,
        point_count_source=point_count_source,
        solution_axes_source=solution_axes_source,
        raw_point_upper_bounds_by_outer=raw_point_upper_bounds_by_outer,
        raw_upper_bound_verified=raw_point_upper_bounds_by_outer is not None,
        raw_upper_bound_source=raw_upper_bound_source,
        complex_components=complex_components,
        scalar_bytes=scalar_bytes,
    )


def require_getdata_budget(decision: Mapping[str, Any]) -> Mapping[str, Any]:
    """Raise before an engine getter unless a planner returned ``PASS``."""

    if decision.get("status") != "PASS" or decision.get("allowed") is not True:
        raise ResultBudgetRefused(decision)
    return decision


def guarded_getdata(
    feature: Any,
    decision: Mapping[str, Any],
    *args: Any,
    method: str = "getData",
) -> Any:
    """Call a getter only after :func:`require_getdata_budget` passes.

    This small adapter is useful at a future core call site and makes the
    no-getData-on-refusal contract directly testable without a COMSOL runtime.
    """

    require_getdata_budget(decision)
    getter = getattr(feature, method)
    return getter(*args)


def new_memory_ledger(*, operation: str | None = None, run_id: str | None = None) -> dict[str, Any]:
    """Return a ledger with separate client and engine memory claims."""

    unmeasured = {
        "status": "UNMEASURED",
        "peak_bytes": None,
        "source": None,
        "claim_scope": "unknown",
    }
    ledger = {
        "operation": operation,
        "run_id": run_id,
        "engine_internal_cache": {
            **unmeasured,
            "claim_scope": "COMSOL Worker/engine internal cache",
            "numeric_payload_only": False,
            "reason": "No engine allocator telemetry was supplied; client measurements cannot infer this peak",
        },
        "client_transport": {
            **unmeasured,
            "claim_scope": "client transport bytes only",
            "numeric_payload_only": False,
        },
        "client_array": {
            **unmeasured,
            "claim_scope": "client/Python materialized array only",
            "numeric_payload_only": False,
        },
        "claims": {
            "engine_peak_inferred_from_client": False,
            "client_measurements_are_engine_measurements": False,
            "numeric_payload_limit_is_not_process_peak": True,
        },
    }
    return ledger


def record_client_peak(
    ledger: Mapping[str, Any],
    scope: str,
    peak_bytes: int,
    *,
    source: str,
    sample_count: int | None = None,
) -> dict[str, Any]:
    """Record a measured client-side peak without changing engine claims."""

    if scope not in {"client_transport", "client_array"}:
        raise ValueError("only client_transport and client_array may be recorded here")
    checked_peak, error = _integer(peak_bytes, "peak_bytes", positive=False)
    if error:
        raise ValueError(error)
    if not isinstance(source, str) or not source.strip():
        raise ValueError("source must be a non-empty measurement source")
    result = deepcopy(dict(ledger))
    entry = dict(result.get(scope) or {})
    entry.update(
        {
            "status": "MEASURED",
            "peak_bytes": checked_peak,
            "source": source,
            "claim_scope": "client transport bytes only"
            if scope == "client_transport"
            else "client/Python materialized array only",
            "measurement_scope": scope,
            "numeric_payload_only": False,
            "does_not_prove_engine_peak": True,
        }
    )
    if sample_count is not None:
        checked_samples, error = _integer(sample_count, "sample_count", positive=False)
        if error:
            raise ValueError(error)
        entry["sample_count"] = checked_samples
    result[scope] = entry
    result.setdefault("claims", {})["engine_peak_inferred_from_client"] = False
    result.setdefault("claims", {})["client_measurements_are_engine_measurements"] = False
    return result


# Names kept short for a future core adapter and for callers that describe the
# operation as a pre-getData guard rather than a planner.
pre_getdata_budget = plan_result_budget
estimate_result_budget = plan_result_budget


__all__ = [
    "RAW_ARRAY_GETTERS",
    "ResultBudgetRefused",
    "estimate_result_budget",
    "guarded_getdata",
    "new_memory_ledger",
    "plan_interp_budget",
    "plan_result_budget",
    "pre_getdata_budget",
    "record_client_peak",
    "require_getdata_budget",
]
