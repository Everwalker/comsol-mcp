#!/usr/bin/env python3
"""W21 MCP tools: parameter case management, study sweeps, optimization, and stage state transfer."""
from __future__ import annotations

from typing import Any

from ._g3_w21 import (
    BoundedOptimizer,
    ComputationBudget,
    ParameterCase,
    ParameterIndexTable,
    ResultCache,
    StageStateTransferManager,
    _GLOBAL_CACHE,
    _GLOBAL_STATE_XFER,
)


def parameter_case_manage(
    action: str = "list",
    group: str = "default",
    case_tag: str = "case_01",
    values: dict[str, Any] | None = None,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Manage parametric cases (create, list, inspect, apply)."""
    merged = dict(arguments or {})
    merged["action"] = action
    merged["group"] = group
    merged["case_tag"] = case_tag
    if values is not None:
        merged["values"] = values
    return merged


def study_sweep_manage(
    action: str = "run",
    study: str = "std1",
    definition: dict[str, Any] | None = None,
    max_cases: int = 30,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Configure and execute parameter sweeps with inner/outer/time tracking."""
    merged = dict(arguments or {})
    merged["action"] = action
    merged["study"] = study
    if definition is not None:
        merged["definition"] = definition
    merged["max_cases"] = max_cases
    return merged


def optimization_bounded_run(
    objective_name: str,
    minimize: bool = True,
    parameter_bounds: dict[str, list[float]] | None = None,
    constraints: list[dict[str, Any]] | None = None,
    max_cases: int = 25,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute bounded parameter optimization under computation budget and constraints."""
    merged = dict(arguments or {})
    merged["objective_name"] = objective_name
    merged["minimize"] = minimize
    if parameter_bounds is not None:
        merged["parameter_bounds"] = {k: (v[0], v[1]) for k, v in parameter_bounds.items()}
    if constraints is not None:
        merged["constraints"] = constraints
    merged["max_cases"] = max_cases
    return merged


def stage_checkpoint_create(
    stage_id: str,
    timestamp_s: float,
    variables: dict[str, Any],
    units: dict[str, str],
    selection: dict[str, Any] | None = None,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Capture terminal state of a physical stage as an immutable checkpoint."""
    merged = dict(arguments or {})
    merged["action"] = "create_checkpoint"
    merged["stage_id"] = stage_id
    merged["timestamp_s"] = timestamp_s
    merged["variables"] = variables
    merged["units"] = units
    if selection is not None:
        merged["selection"] = selection
    return merged


def stage_state_transfer(
    checkpoint_id: str,
    target_stage_id: str,
    variable_mapping: dict[str, str],
    reset_history: bool = False,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Transfer state from source checkpoint to target initial conditions preserving lineage."""
    merged = dict(arguments or {})
    merged["action"] = "transfer"
    merged["checkpoint_id"] = checkpoint_id
    merged["target_stage_id"] = target_stage_id
    merged["variable_mapping"] = variable_mapping
    merged["reset_history"] = reset_history
    return merged


def register(registry: Any) -> None:
    tools = [
        ("parameter.case_manage", parameter_case_manage),
        ("parameter_case_manage", parameter_case_manage),
        ("study.sweep_manage", study_sweep_manage),
        ("study_sweep_manage", study_sweep_manage),
        ("optimization.bounded_run", optimization_bounded_run),
        ("optimization_bounded_run", optimization_bounded_run),
        ("stage.checkpoint_create", stage_checkpoint_create),
        ("stage_checkpoint_create", stage_checkpoint_create),
        ("stage.state_transfer", stage_state_transfer),
        ("stage_state_transfer", stage_state_transfer),
    ]
    for name, fn in tools:
        registry.add_tool(fn, name=name)
