#!/usr/bin/env python3
"""W20 MCP tools: validate.* operations."""
from __future__ import annotations

from typing import Any


def validate_preflight(
    scope: dict[str, Any] | None = None,
    checks: list[str] | None = None,
    model_data: dict[str, Any] | None = None,
    rules: list[str] | None = None,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate model preflight structure and readiness to solve."""
    merged = dict(arguments or {})
    if scope is not None:
        merged["scope"] = scope
    if checks is not None:
        merged["checks"] = checks
    if model_data is not None:
        merged["model_data"] = model_data
    if rules is not None:
        merged["rules"] = rules
    return merged


def validate_structure(
    scope: dict[str, Any] | str | None = None,
    rules: list[str] | None = None,
    model_data: dict[str, Any] | None = None,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Inspect model structure (components, geometries, selections, physics, mesh, study)."""
    merged = dict(arguments or {})
    if scope is not None:
        merged["scope"] = scope
    if rules is not None:
        merged["rules"] = rules
    if model_data is not None:
        merged["model_data"] = model_data
    return merged


def validate_expressions(
    expressions: list[Any] | None = None,
    context: dict[str, Any] | None = None,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate expression syntax, units, and finite numbers."""
    merged = dict(arguments or {})
    if expressions is not None:
        merged["expressions"] = expressions
    if context is not None:
        merged["context"] = context
    return merged


def validate_boundary_conditions(
    scope: dict[str, Any] | str | None = None,
    rules: list[str] | None = None,
    boundary_data: dict[str, Any] | None = None,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate boundary condition assignments and conflict detection."""
    merged = dict(arguments or {})
    if scope is not None:
        merged["scope"] = scope
    if rules is not None:
        merged["rules"] = rules
    if boundary_data is not None:
        merged["boundary_data"] = boundary_data
    return merged


def validate_solution(
    solution: dict[str, Any] | None = None,
    criteria: dict[str, Any] | None = None,
    dataset: str | None = None,
    tag: str | None = None,
    oracle: str | None = None,
    observations: list[Any] | dict[str, Any] | None = None,
    values: list[Any] | None = None,
    range: list[float] | None = None,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate solution existence, observations, and benchmark errors."""
    merged = dict(arguments or {})
    if solution is not None:
        merged["solution"] = solution
    if criteria is not None:
        merged["criteria"] = criteria
    if dataset is not None:
        merged["dataset"] = dataset
    if tag is not None:
        merged["tag"] = tag
    if oracle is not None:
        merged["oracle"] = oracle
    if observations is not None:
        merged["observations"] = observations
    if values is not None:
        merged["values"] = values
    if range is not None:
        merged["range"] = range
    return merged


def validate_conservation(
    definition: dict[str, Any] | None = None,
    solution: dict[str, Any] | None = None,
    inflow: float | None = None,
    outflow: float | None = None,
    storage_rate: float | None = None,
    source_term: float | None = None,
    power: float | None = None,
    normalization: float | None = None,
    tolerance: float | None = None,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate energy/mass conservation with inflow/outflow, storage, and source terms."""
    merged = dict(arguments or {})
    if definition is not None:
        merged["definition"] = definition
    if solution is not None:
        merged["solution"] = solution
    if inflow is not None:
        merged["inflow"] = inflow
    if outflow is not None:
        merged["outflow"] = outflow
    if storage_rate is not None:
        merged["storage_rate"] = storage_rate
    if source_term is not None:
        merged["source_term"] = source_term
    if power is not None:
        merged["power"] = power
    if normalization is not None:
        merged["normalization"] = normalization
    if tolerance is not None:
        merged["tolerance"] = tolerance
    return merged


def validate_convergence(
    cases: list[dict[str, Any]] | None = None,
    metrics: list[str] | None = None,
    criteria: dict[str, Any] | None = None,
    levels: list[Any] | None = None,
    study: str | None = None,
    threshold: float | None = None,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate mesh, time-step, or solver tolerance convergence across levels."""
    merged = dict(arguments or {})
    if cases is not None:
        merged["cases"] = cases
    if metrics is not None:
        merged["metrics"] = metrics
    if criteria is not None:
        merged["criteria"] = criteria
    if levels is not None:
        merged["levels"] = levels
    if study is not None:
        merged["study"] = study
    if threshold is not None:
        merged["threshold"] = threshold
    return merged


def validate_report(
    validation_ids: list[str] | None = None,
    destination: str | None = None,
    data: dict[str, Any] | None = None,
    format: str | None = None,
    overwrite: bool | None = None,
    source_identity: str | None = None,
    runtime_version: str | None = None,
    rule_version: str | None = None,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Aggregate validation results and generate JSON and Markdown reports."""
    merged = dict(arguments or {})
    if validation_ids is not None:
        merged["validation_ids"] = validation_ids
    if destination is not None:
        merged["destination"] = destination
    if data is not None:
        merged["data"] = data
    if format is not None:
        merged["format"] = format
    if overwrite is not None:
        merged["overwrite"] = overwrite
    if source_identity is not None:
        merged["source_identity"] = source_identity
    if runtime_version is not None:
        merged["runtime_version"] = runtime_version
    if rule_version is not None:
        merged["rule_version"] = rule_version
    return merged


def register(registry: Any) -> None:
    tools = [
        ("validate.preflight", validate_preflight),
        ("validate_preflight", validate_preflight),
        ("validate.structure", validate_structure),
        ("validate_structure", validate_structure),
        ("validate.expressions", validate_expressions),
        ("validate_expressions", validate_expressions),
        ("validate.boundary_conditions", validate_boundary_conditions),
        ("validate_boundary_conditions", validate_boundary_conditions),
        ("validate.solution", validate_solution),
        ("validate_solution", validate_solution),
        ("validate.conservation", validate_conservation),
        ("validate_conservation", validate_conservation),
        ("validate.convergence", validate_convergence),
        ("validate_convergence", validate_convergence),
        ("validate.report", validate_report),
        ("validate_report", validate_report),
    ]
    for name, fn in tools:
        registry.add_tool(fn, name=name)
