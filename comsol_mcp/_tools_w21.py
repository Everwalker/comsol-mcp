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
    units: dict[str, str] | None = None,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Manage parametric cases (create, list, inspect, apply)."""
    merged = dict(arguments or {})
    merged["action"] = action
    merged["group"] = group
    merged["case_tag"] = case_tag
    if values is not None:
        merged["values"] = values
    if units is not None:
        merged["units"] = units
    return merged


def study_sweep_manage(
    action: str = "run",
    study: str = "std1",
    definition: dict[str, Any] | None = None,
    max_cases: int = 30,
    max_wall_time_s: float = 300,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Configure and execute parameter sweeps with inner/outer/time tracking."""
    merged = dict(arguments or {})
    merged["action"] = action
    merged["study"] = study
    if definition is not None:
        merged["definition"] = definition
    merged["max_cases"] = max_cases
    merged["max_wall_time_s"] = max_wall_time_s
    return merged


def optimization_bounded_run(
    objective_name: str,
    minimize: bool = True,
    parameter_bounds: dict[str, list[float]] | None = None,
    constraints: list[dict[str, Any]] | None = None,
    max_cases: int = 25,
    study: str = "std1",
    definition: dict[str, Any] | None = None,
    grid_points_per_dim: int = 3,
    max_wall_time_s: float = 300,
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
    merged.update(study=study, definition=definition, grid_points_per_dim=grid_points_per_dim, max_wall_time_s=max_wall_time_s)
    return merged


def stage_checkpoint_create(
    stage_id: str,
    timestamp_s: float,
    variables: dict[str, Any] | None = None,
    units: dict[str, str] | None = None,
    selection: dict[str, Any] | None = None,
    sample: dict[str, Any] | None = None,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Capture terminal state of a physical stage as an immutable checkpoint."""
    merged = dict(arguments or {})
    merged["action"] = "create_checkpoint"
    merged["stage_id"] = stage_id
    merged["timestamp_s"] = timestamp_s
    merged["variables"] = variables
    merged["units"] = units
    if sample is not None:
        merged["sample"] = sample
    if selection is not None:
        merged["selection"] = selection
    return merged


def stage_state_transfer(
    checkpoint_id: str,
    target_stage_id: str,
    variable_mapping: dict[str, str],
    reset_history: bool = False,
    target_sample: dict[str, Any] | None = None,
    target_step: str = "time",
    initial_tolerance: float | None = None,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Transfer state from source checkpoint to target initial conditions preserving lineage."""
    merged = dict(arguments or {})
    merged["action"] = "transfer"
    merged["checkpoint_id"] = checkpoint_id
    merged["target_stage_id"] = target_stage_id
    merged["variable_mapping"] = variable_mapping
    merged["reset_history"] = reset_history
    merged.update(target_sample=target_sample, target_step=target_step, initial_tolerance=initial_tolerance)
    return merged


def solver_solution_transfer(
    source: dict[str, Any],
    target: dict[str, Any],
    mapping: dict[str, Any],
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Select one stored solution as an explicitly addressed Variables initial value."""
    merged = dict(arguments or {})
    merged.update(source=source, target=target, mapping=mapping)
    return merged


def experiment_design(definition: dict[str, Any], arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """Persist a W21 cartesian-grid experiment bound to the current project/model revision."""
    merged = dict(arguments or {})
    merged["definition"] = definition
    return merged


def experiment_run(
    experiment_id: str,
    resources: dict[str, Any] | None = None,
    timeout_s: float | None = None,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run a registered experiment with optional budget reductions."""
    merged = dict(arguments or {})
    merged["experiment_id"] = experiment_id
    if resources is not None:
        merged["resources"] = resources
    if timeout_s is not None:
        merged["timeout_s"] = timeout_s
    return merged


def experiment_inspect(
    project_id: str,
    experiment_id: str,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Read one project-owned experiment's durable design and case progress."""
    result: dict[str, Any] = {"project_id": project_id, "experiment_id": experiment_id}
    if request_id is not None:
        result["request_id"] = request_id
    return result


def experiment_case_result(
    project_id: str,
    experiment_id: str,
    case_id: str,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Read one exact case_id result from the project's durable experiment records."""
    result: dict[str, Any] = {
        "project_id": project_id,
        "experiment_id": experiment_id,
        "case_id": case_id,
    }
    if request_id is not None:
        result["request_id"] = request_id
    return result


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
        ("solver_solution_transfer", solver_solution_transfer),
        ("experiment_design", experiment_design),
        ("experiment_run", experiment_run),
    ]
    for name, fn in tools:
        registry.add_tool(fn, name=name)
    registry.add_tool(experiment_inspect, name="experiment_inspect", operation_id="experiment.inspect")
    registry.add_tool(experiment_case_result, name="experiment_case_result", operation_id="experiment.case_result")
