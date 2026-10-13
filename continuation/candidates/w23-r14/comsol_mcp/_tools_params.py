#!/usr/bin/env python3
"""MCP tools: get_parameters, set_parameters, evaluate_expressions, get_core_metrics."""

from __future__ import annotations

import json
from typing import Any
from collections.abc import Mapping

from comsol_mcp._state import (
    _run_tool, _run_tool_readonly, _safe_model_label, ToolExecutionError,
)
from comsol_mcp._model import _require_visible_main
from comsol_mcp._model_ops import (
    _parameter_rows, _evaluate_named_expressions,
    _find_initialized_solution_tag, _read_last_time_day,
    _eval_global_last, _eval_domain_average_last,
    _eval_boundary_average_last, _eval_extremum_last,
    _numeric_result, _evaluate_aggregate, _coerce_eval_value, _last_scalar,
    _evaluate_expression_safely, _evaluation_binding, _value_is_empty, _value_shape,
    NumericalCleanupError,
    temporary_node_inventory, temporary_node_summary,
)


def get_parameters() -> str:
    """Return current global parameters from the selected server-side model."""

    def _impl() -> dict[str, Any]:
        model = _require_visible_main("get_parameters")
        rows = _parameter_rows(model)
        return {"label": _safe_model_label(model), "parameters": rows, "count": len(rows)}

    return _run_tool_readonly("get_parameters", _impl)


def evaluate_expressions(expressions_json: str = "[]", max_result_size: int = 0, evaluation_policy: str = "ephemeral_mutation") -> str:
    """Evaluate one or more expressions on the current server-side model.

    Each expression item supports: {"name": "...", "expression": "...",
      "aggregate": "max"|"min"|"avg"|"integral"|"none",
      "domains": [1,2], "boundaries": [5,6], "time_point": "last"|"all"|"N"}.

    Backward compatible: [{"name": "...", "expression": "..."}] still works.
    evaluation_policy: ``ephemeral_mutation`` is serialized and creates only
    short-lived MCP-owned nodes. ``pure_read`` rejects evaluation because this
    backend cannot evaluate without temporary nodes. max_result_size is retained
    for backwards compatibility; it no longer truncates numerical results.
    """

    def _impl() -> dict[str, Any]:
        model = _require_visible_main("evaluate_expressions")
        policy = str(evaluation_policy or "ephemeral_mutation").strip().lower()
        if policy == "pure_read":
            raise ValueError("pure_read evaluation is unavailable: this backend requires temporary numerical nodes; use ephemeral_mutation or an isolated model copy.")
        if policy != "ephemeral_mutation":
            raise ValueError("evaluation_policy must be pure_read or ephemeral_mutation.")
        parsed = json.loads(expressions_json)
        if not isinstance(parsed, list):
            raise ValueError("expressions_json must be a JSON array.")
        results = []
        call_binding: dict[str, Any] | None = None
        with temporary_node_inventory(
            owner={
                "operation": "evaluate_expressions",
                "policy": policy,
                "request": {"tool": "evaluate_expressions", "expressions": len(parsed)},
            }
        ) as temporary_nodes:
            for item in parsed:
                if not isinstance(item, dict):
                    raise ValueError("Each expression entry must be an object.")
                name = str(item.get("name", "")).strip()
                expression = str(item.get("expression", "")).strip()
                aggregate = str(item.get("aggregate", "none")).strip().lower()
                domains = item.get("domains")
                boundaries = item.get("boundaries")
                time_point = str(item.get("time_point", "last")).strip()

                if not name:
                    raise ValueError("Expression name is required.")
                if not expression:
                    raise ValueError(f'Expression is required for "{name}".')

                row: dict[str, Any] = {"name": name, "expression": expression}

                int_domains = [int(d) for d in domains] if domains else None
                int_bounds = [int(b) for b in boundaries] if boundaries else None

                # C04: the evaluated value is published together with the dataset/solution it
                # belongs to and the shape of the result.  ``provenance`` is the sink the one
                # evaluation path fills in; the row copies the fields out of it below.
                provenance: dict[str, Any] = {}

                try:
                    if aggregate and aggregate != "none":
                        value = _evaluate_aggregate(model, expression, aggregate, int_domains, int_bounds, time_point)
                        binding = _evaluation_binding(model)
                        call_binding = call_binding or binding
                        provenance.update({
                            "route": f"results:aggregate:{aggregate}",
                            "dataset": binding.get("dataset"), "solution": binding.get("solution"),
                            "binding": {key: entry for key, entry in binding.items() if key != "inventory"},
                            "dataset_inventory": binding.get("inventory"),
                            "engine_calls": [f"result().numerical(<owned tag>) {aggregate}"],
                        })
                        row["value"] = value
                        row["last_value"] = value
                        row["aggregate"] = aggregate
                    else:
                        raw = _evaluate_expression_safely(model, expression, time_point, provenance)
                        value = _coerce_eval_value(raw)
                        row["value"] = value
                        row["last_value"] = _coerce_eval_value(_last_scalar(value))

                    row["shape"] = _value_shape(row.get("value"))
                    if _value_is_empty(row.get("value")):
                        row["last_value"] = None
                    for key in ("route", "dataset", "solution", "data_mode"):
                        if key in provenance:
                            row[key] = provenance[key]
                    call_binding = call_binding or (provenance.get("binding") if provenance else None)
                    if _value_is_empty(row.get("value")):
                        # An empty read is not a computed value: the engine answered with no
                        # numbers at all, so the row is a business failure that names the route it
                        # took, the dataset/solution state and the engine's own messages.  Never
                        # ``ok: true`` with ``value: []`` (m1d W13_T006 read exactly that shape).
                        route = str(provenance.get("route") or "unknown")
                        dataset = provenance.get("dataset")
                        detail = ("the engine returned no value for this expression "
                                  f"(route: {route}; data: {provenance.get('data_mode')!r}; dataset: "
                                  f"{dataset!r}; solution: {provenance.get('solution')!r})")
                        reasons = []
                        if provenance.get("empty_results_read"):
                            reasons.append(str(provenance["empty_results_read"]["reason"]))
                        binding_reason = (provenance.get("binding") or {}).get("reason")
                        if binding_reason:
                            reasons.append(str(binding_reason))
                        data_reason = (provenance.get("binding") or {}).get("data_reason")
                        if data_reason:
                            reasons.append(str(data_reason))
                        if reasons:
                            detail = f"{detail}; " + "; ".join(reasons)
                        row["ok"] = False
                        row["error_code"] = "NO_VALUES_RETURNED"
                        row["error"] = detail
                        if provenance:
                            row["diagnosis"] = provenance
                    else:
                        row["ok"] = True
                        if provenance:
                            # C04: the same provenance is published for a value that was produced
                            # (which route answered, the dataset/solution binding and its readback).
                            row["diagnosis"] = provenance
                except NumericalCleanupError as exc:
                    # Cleanup failures are never a row-level error: the engine state
                    # is unknown, and the reply has to say so through the shared C01
                    # contract (``cleanup.cleanup_failed``) instead of a bespoke flag.
                    raise ToolExecutionError(
                        "Temporary numerical cleanup failed; engine result state is unknown.",
                        data={
                            "results": results,
                            "temporary_nodes": list(temporary_nodes),
                            "cleanup": {**temporary_node_summary(temporary_nodes), "cleanup_failed": True},
                            "cleanup_failed": True,
                            "engine_state_unknown": True,
                            "safe_retry": False,
                        },
                    ) from exc
                except Exception as exc:
                    row["ok"] = False
                    row["error"] = str(exc)
                    if provenance:
                        row["diagnosis"] = provenance
                        for key in ("route", "dataset", "solution", "data_mode"):
                            if key in provenance:
                                row[key] = provenance[key]
                results.append(row)

            payload = {
                "label": _safe_model_label(model),
                "results": results,
                "count": len(results),
                "evaluation_policy": policy,
                "ephemeral_mutation": True,
                "temporary_nodes": list(temporary_nodes),
                "cleanup": temporary_node_summary(temporary_nodes),
                "max_result_size_ignored": int(max_result_size) if max_result_size > 0 else None,
            }
            if call_binding:
                # C04: the call-level binding is the dataset/solution the values belong to, with
                # the engine readback the binding was resolved from.
                payload["dataset"] = call_binding.get("dataset")
                payload["solution"] = call_binding.get("solution")
                payload["binding"] = call_binding
            inventory = None
            for row in results:
                diagnosis = row.get("diagnosis")
                if isinstance(diagnosis, Mapping) and diagnosis.get("dataset_inventory"):
                    inventory = diagnosis["dataset_inventory"]
                    break
            if inventory is not None:
                payload["dataset_inventory"] = inventory
            if not all(row.get("ok", False) for row in results):
                # An invalid expression is a business failure, never a successful
                # read.  Temporary-node cleanup has already succeeded here; keep
                # it distinct from NumericalCleanupError above, which is unsafe.
                raise ToolExecutionError(
                    "One or more expressions could not be evaluated.",
                    data={**payload, "status": "partial", "partial_change": False,
                          "failed_item_may_have_changed": False, "safe_retry": True},
                )
            return payload

    return _run_tool("evaluate_expressions", _impl)


def _validate_metric_definitions(metrics_json: str) -> list[dict[str, Any]]:
    definitions = json.loads(metrics_json or "[]")
    if not isinstance(definitions, list) or not definitions:
        raise ValueError("Explicit non-empty metric definitions are required.")
    for definition in definitions:
        if not isinstance(definition, dict) or not all(isinstance(definition.get(key), str) and definition[key].strip() for key in ("name", "expression")):
            raise ValueError("Each metric definition requires string name and expression.")
        if definition.get("aggregate", "none") not in ("none", "max", "min", "avg", "integral"):
            raise ValueError("Invalid metric aggregate.")
        time_point = str(definition.get("time_point", "last"))
        if time_point not in ("first", "last", "all") and not (time_point.isdigit() and int(time_point) > 0):
            raise ValueError("Invalid metric time_point.")
        for key in ("domains", "boundaries"):
            values = definition.get(key)
            if values is not None and (not isinstance(values, list) or any(type(v) is not int or v < 1 for v in values)):
                raise ValueError("Metric selections must be arrays of positive integers.")
        if definition.get("domains") and definition.get("boundaries"):
            raise ValueError("Specify metric domains or boundaries, not both.")
    return definitions


def get_core_metrics(metrics_json: str = "[]", evaluation_policy: str = "ephemeral_mutation") -> str:
    """Evaluate explicit task metrics; no model-specific defaults are assumed."""

    def _impl() -> dict[str, Any]:
        model = _require_visible_main("get_core_metrics")
        policy = str(evaluation_policy or "ephemeral_mutation").strip().lower()
        if policy == "pure_read":
            raise ValueError("pure_read metrics are unavailable because metric evaluation requires temporary numerical nodes.")
        if policy != "ephemeral_mutation":
            raise ValueError("evaluation_policy must be pure_read or ephemeral_mutation.")
        definitions = _validate_metric_definitions(metrics_json)
        sol_tag = _find_initialized_solution_tag(model)
        results = []
        with temporary_node_inventory(
            owner={
                "operation": "get_core_metrics",
                "policy": policy,
                "request": {"tool": "get_core_metrics", "metrics": len(definitions), "solution": sol_tag or None},
            }
        ) as temporary_nodes:
            for definition in definitions:
                if not isinstance(definition, dict):
                    raise ValueError("Each metric definition must be an object.")
                name = str(definition.get("name", "")).strip()
                expression = str(definition.get("expression", "")).strip()
                if not name or not expression:
                    raise ValueError("Each metric definition requires name and expression.")
                try:
                    value = _evaluate_aggregate(model, expression, str(definition.get("aggregate", "none")), definition.get("domains"), definition.get("boundaries"), str(definition.get("time_point", "last")))
                    results.append(_numeric_result(name, expression, value))
                except NumericalCleanupError as exc:
                    raise ToolExecutionError(
                        "Temporary numerical cleanup failed while evaluating required metrics; engine result state is unknown.",
                        data={
                            "results": results,
                            "temporary_nodes": list(temporary_nodes),
                            "cleanup": {**temporary_node_summary(temporary_nodes), "cleanup_failed": True},
                            "cleanup_failed": True,
                            "engine_state_unknown": True,
                            "safe_retry": False,
                        },
                    ) from exc
                except Exception as exc:
                    results.append(_numeric_result(name, expression, ok=False, error=str(exc)))
            solve_status = "success" if all(row.get("ok", False) for row in results) else "partial"
            payload = {
                "label": _safe_model_label(model),
                "solve_status": solve_status,
                "solution_tag": sol_tag,
                "results": results,
                "evaluation_policy": policy,
                "ephemeral_mutation": True,
                "temporary_nodes": list(temporary_nodes),
                "cleanup": temporary_node_summary(temporary_nodes),
            }
            if solve_status != "success":
                raise ToolExecutionError(
                    "Required metric evaluation was partial; no acceptance result is available.",
                    data={
                        **payload,
                        "execution_success": True,
                        "acceptance_status": "failed",
                    },
                )
            return payload

    return _run_tool("get_core_metrics", _impl)


def _parse_parameter_updates(parameters_json: str) -> list[dict[str, str]]:
    """Validate the complete parameter batch before mutating a model."""
    parsed = json.loads(parameters_json)
    if not isinstance(parsed, list):
        raise ValueError("parameters_json must be a JSON array.")
    updates: list[dict[str, str]] = []
    for item in parsed:
        if not isinstance(item, dict):
            raise ValueError("Each parameter entry must be an object.")
        raw_name = item.get("name")
        raw_expression = item.get("expression")
        if not isinstance(raw_name, str) or not raw_name.strip():
            raise ValueError("Parameter name is required.")
        if not isinstance(raw_expression, str) or not raw_expression.strip():
            raise ValueError("Parameter expression is required.")
        updates.append({"name": raw_name.strip(), "expression": raw_expression.strip()})
    return updates


def _apply_parameter_updates(model, updates: list[dict[str, str]]) -> list[dict[str, str]]:
    """Apply a prevalidated batch and make partial engine mutation explicit."""
    applied: list[dict[str, str]] = []
    for index, update in enumerate(updates):
        try:
            model.java.param().set(update["name"], update["expression"])
        except Exception as exc:
            failed = {**update, "error": str(exc)}
            raise ToolExecutionError(
                "Parameter batch was partially applied; inspect applied, failed, and not_executed before retrying.",
                data={
                    "applied": applied,
                    "failed": failed,
                    "not_executed": updates[index + 1 :],
                    "atomic": False,
                    "partial_change": bool(applied),
                    "failed_item_may_have_changed": True,
                    "safe_retry": False,
                },
            ) from exc
        applied.append(dict(update))
    return applied


def set_parameters(parameters_json: str) -> str:
    """Set multiple global parameters on the selected server-side model."""

    def _impl() -> dict[str, Any]:
        model = _require_visible_main("set_parameters")
        updates = _parse_parameter_updates(parameters_json)
        updated = _apply_parameter_updates(model, updates)
        return {"updated": updated, "count": len(updated)}

    return _run_tool("set_parameters", _impl)


def register(mcp_instance) -> None:
    mcp_instance.add_tool(get_parameters)
    mcp_instance.add_tool(evaluate_expressions)
    mcp_instance.add_tool(get_core_metrics)
    mcp_instance.add_tool(set_parameters)
