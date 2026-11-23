"""Strict input contract for the plot renderer's explicit and legacy bindings."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ._execution_contract import ExecutionContractError


def _binding_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ExecutionContractError(
            "INVALID_REQUEST", f"{label} must be a non-empty string without surrounding whitespace",
            stage="validation",
        )
    return value


def normalize_plot_render_binding(arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Validate an explicit plot SolutionSpec or preserve the options-only path.

    The plot operation supports the SolutionSpec dataset and solution fields.
    The generic selectors are intentionally not accepted here because this
    renderer has no exact mapping for them.
    """
    raw_options = arguments.get("options", {})
    if raw_options is None:
        raw_options = {}
    if not isinstance(raw_options, Mapping):
        raise ExecutionContractError("INVALID_REQUEST", "plot.render options must be an object", stage="validation")
    options = dict(raw_options)

    if "solution" not in arguments:
        option_datasets = [
            (key, _binding_string(options[key], f"plot.render options.{key}"))
            for key in ("dataset", "data")
            if key in options
        ]
        if len({value for _, value in option_datasets}) > 1:
            raise ExecutionContractError(
                "INVALID_REQUEST", "plot.render options.dataset and options.data conflict", stage="validation",
            )
        option_solution = (
            _binding_string(options["solution"], "plot.render options.solution")
            if "solution" in options else None
        )
        return {
            "mode": "legacy",
            "dataset": option_datasets[0][1] if option_datasets else None,
            "solution": option_solution,
            "options": options,
        }

    spec = arguments["solution"]
    if not isinstance(spec, Mapping):
        raise ExecutionContractError("INVALID_REQUEST", "plot.render solution must be a SolutionSpec object", stage="validation")
    unknown = sorted(set(spec) - {"dataset", "solution"})
    if unknown:
        raise ExecutionContractError(
            "INVALID_REQUEST", f"plot.render solution has unsupported fields: {', '.join(unknown)}",
            stage="validation",
        )
    if "dataset" not in spec:
        raise ExecutionContractError("INVALID_REQUEST", "plot.render solution.dataset is required", stage="validation")
    dataset = _binding_string(spec["dataset"], "plot.render solution.dataset")
    solution = _binding_string(spec["solution"], "plot.render solution.solution") if "solution" in spec else None

    option_datasets = []
    for key in ("dataset", "data"):
        if key in options:
            option_datasets.append((key, _binding_string(options[key], f"plot.render options.{key}")))
    if len({value for _, value in option_datasets}) > 1:
        raise ExecutionContractError("INVALID_REQUEST", "plot.render options.dataset and options.data conflict", stage="validation")
    if option_datasets and option_datasets[0][1] != dataset:
        raise ExecutionContractError("INVALID_REQUEST", "plot.render options dataset conflicts with solution.dataset", stage="validation")
    if "solution" in options:
        option_solution = _binding_string(options["solution"], "plot.render options.solution")
        if solution is None or option_solution != solution:
            raise ExecutionContractError("INVALID_REQUEST", "plot.render options.solution conflicts with solution.solution", stage="validation")

    return {"mode": "explicit", "dataset": dataset, "solution": solution, "options": options}
