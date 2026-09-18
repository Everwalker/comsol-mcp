#!/usr/bin/env python3
"""MCP tools: get_parameters, set_parameters, evaluate_expressions, get_core_metrics."""

from __future__ import annotations

import json
from typing import Any

from comsol_mcp._state import (
    _run_tool, _run_tool_readonly, _safe_model_label,
)
from comsol_mcp._model import _require_visible_main
from comsol_mcp._model_ops import (
    _parameter_rows, _evaluate_named_expressions,
    _find_initialized_solution_tag, _read_last_time_day,
    _eval_global_last, _eval_domain_average_last,
    _eval_boundary_average_last, _eval_extremum_last,
    _numeric_result, _evaluate_aggregate, _coerce_eval_value, _last_scalar,
    _evaluate_expression_safely,
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

            try:
                if aggregate and aggregate != "none":
                    value = _evaluate_aggregate(model, expression, aggregate, int_domains, int_bounds, time_point)
                    row["value"] = value
                    row["last_value"] = value
                    row["aggregate"] = aggregate
                else:
                    raw = _evaluate_expression_safely(model, expression, time_point)
                    value = _coerce_eval_value(raw)
                    row["value"] = value
                    row["last_value"] = _coerce_eval_value(_last_scalar(value))

                row["ok"] = True
            except Exception as exc:
                row["ok"] = False
                row["error"] = str(exc)
            results.append(row)

        return {
            "label": _safe_model_label(model),
            "results": results,
            "count": len(results),
            "evaluation_policy": policy,
            "ephemeral_mutation": True,
            "max_result_size_ignored": int(max_result_size) if max_result_size > 0 else None,
        }

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
            except Exception as exc:
                results.append(_numeric_result(name, expression, ok=False, error=str(exc)))
        solve_status = "success" if all(row.get("ok", False) for row in results) else "partial"
        return {
            "label": _safe_model_label(model),
            "solve_status": solve_status,
            "solution_tag": sol_tag,
            "results": results,
            "evaluation_policy": policy,
        }

    return _run_tool("get_core_metrics", _impl)


def set_parameters(parameters_json: str) -> str:
    """Set multiple global parameters on the selected server-side model."""

    def _impl() -> dict[str, Any]:
        model = _require_visible_main("set_parameters")
        parsed = json.loads(parameters_json)
        if not isinstance(parsed, list):
            raise ValueError("parameters_json must be a JSON array.")
        updated = []
        for item in parsed:
            if not isinstance(item, dict):
                raise ValueError("Each parameter entry must be an object.")
            name = str(item.get("name", "")).strip()
            expression = str(item.get("expression", "")).strip()
            if not name:
                raise ValueError("Parameter name is required.")
            model.java.param().set(name, expression)
            updated.append({"name": name, "expression": expression})
        return {"updated": updated, "count": len(updated)}

    return _run_tool("set_parameters", _impl)


def register(mcp_instance) -> None:
    mcp_instance.add_tool(get_parameters)
    mcp_instance.add_tool(evaluate_expressions)
    mcp_instance.add_tool(get_core_metrics)
    mcp_instance.add_tool(set_parameters)
