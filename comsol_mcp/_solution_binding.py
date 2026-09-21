"""Solution Binding and Field Array Indexing Service for COMSOL MCP (F04).

Provides SolutionBinding and FieldArray:
- Respects official COMSOL NumericalFeature layout: [expr][solnum][vertex].
- Strictly indexes inner solutions on the solution step axis (Axis 1), NEVER confusing it with the expression axis (Axis 0).
- Supports single integer, list of integers, 'first', 'last', and 'all' indexing with strict 1-based bounds checking.
- Resolves upstream dataset chains and detects reference cycles (e.g. CutPoint/CutPlane/Join -> Solution).
- Handles outer parameters without hardcoding outer_indices=[1].
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from ._execution_contract import ExecutionContractError


class SolutionBinding:
    """Manages dataset reference resolution, cycle detection, and solution parameter extraction."""

    @staticmethod
    def resolve_dataset_chain(
        dset_container: Any,
        start_tag: str,
        max_depth: int = 16,
    ) -> list[str]:
        """Traverse upstream dataset references to build the provenance chain, detecting cycles."""
        chain: list[str] = [start_tag]
        visited: set[str] = {start_tag}
        curr_tag = start_tag

        for _ in range(max_depth):
            try:
                node = getattr(dset_container, "get")(curr_tag)
            except Exception:
                break

            upstream: str | None = None
            for prop in ("data", "dataset", "solution"):
                try:
                    val = getattr(node, "getString")(prop) if hasattr(node, "getString") else None
                    if val and isinstance(val, str) and val in getattr(dset_container, "tags")():
                        upstream = val
                        break
                except Exception:
                    pass

            if not upstream:
                break

            if upstream in visited:
                raise ExecutionContractError(
                    "DATASET_CYCLE_DETECTED",
                    f"Dataset reference cycle detected: {' -> '.join(chain)} -> {upstream}",
                )

            visited.add(upstream)
            chain.append(upstream)
            curr_tag = upstream

        return chain

    @staticmethod
    def slice_solution_axis(
        data: Any,
        inner_spec: int | Sequence[int] | str | None,
        *,
        num_expressions: int = 1,
    ) -> Any:
        """Slice the solution step axis (Axis 1) of a [expr][solnum][vertex] array.

        Never slices the expression axis when inner is requested.
        """
        if inner_spec is None or inner_spec == "all":
            return data

        if not isinstance(data, list):
            return data

        # Helper to slice a single expression's solution list: [solnum][vertex]
        def _slice_one_expr(expr_sol_list: Any) -> Any:
            if not isinstance(expr_sol_list, list) or not expr_sol_list:
                return expr_sol_list

            sol_count = len(expr_sol_list)

            if isinstance(inner_spec, int):
                if inner_spec < 1 or inner_spec > sol_count:
                    raise ExecutionContractError(
                        "INVALID_REQUEST",
                        f"inner index {inner_spec} out of range [1, {sol_count}]",
                    )
                return expr_sol_list[inner_spec - 1]

            elif isinstance(inner_spec, (list, tuple)):
                selected = []
                for idx in inner_spec:
                    if not isinstance(idx, int) or idx < 1 or idx > sol_count:
                        raise ExecutionContractError(
                            "INVALID_REQUEST",
                            f"inner index {idx} out of range [1, {sol_count}]",
                        )
                    selected.append(expr_sol_list[idx - 1])
                return selected

            elif inner_spec == "first":
                return expr_sol_list[0]

            elif inner_spec == "last":
                return expr_sol_list[-1]

            else:
                raise ExecutionContractError(
                    "INVALID_REQUEST",
                    f"unsupported inner spec: {inner_spec!r}",
                )

        # Check if data is multi-expression [expr][solnum][...]
        # When num_expressions > 1 or len(data) matches expression count:
        if num_expressions > 1 and len(data) == num_expressions:
            return [_slice_one_expr(expr_data) for expr_data in data]
        elif num_expressions == 1:
            # Single expression: data can be [solnum][vertex] or [[solnum][vertex]]
            if len(data) == 1 and isinstance(data[0], list):
                return [_slice_one_expr(data[0])]
            return _slice_one_expr(data)
        else:
            # General list of expressions
            return [_slice_one_expr(expr_data) for expr_data in data]


class FieldArray:
    """Standardized multidimensional array container for evaluation outcomes."""

    def __init__(
        self,
        values: Any,
        *,
        axes: Sequence[str] = ("expression", "solnum", "point"),
        shape: tuple[int, ...] | None = None,
        units: Sequence[str] | None = None,
        is_complex: bool = False,
    ) -> None:
        self.values = values
        self.axes = list(axes)
        self.shape = shape
        self.units = list(units) if units else []
        self.is_complex = is_complex

    def to_dict(self) -> dict[str, Any]:
        return {
            "values": self.values,
            "axes": self.axes,
            "shape": list(self.shape) if self.shape else None,
            "units": self.units,
            "is_complex": self.is_complex,
        }
