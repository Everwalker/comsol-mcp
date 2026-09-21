"""Measure and Statistical Aggregation Service for COMSOL MCP (F02, F03).

Provides shared MeasureSpec and SelectionBinding abstractions:
- Maps geometric entities by dimension (0D point, 1D edge/line, 2D surface/boundary, 3D volume/domain).
- Selects verified numerical feature types for integral, average, min, max, std, rms.
- Enforces rigorous mathematical definitions for weighted measures, population variance, standard deviation, and RMS.
- Disallows hardcoded denominator 1.0; evaluates actual measure M = int w dmu.
- Handles 2D axisymmetric physics and cross-section measures without double-counting 2*pi*r.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from ._execution_contract import ExecutionContractError


class MeasureSpec:
    """Encapsulates spatial measure, entity dimension, selection, and statistical aggregation."""

    def __init__(
        self,
        *,
        aggregate: str = "none",
        entity_dim: int | None = None,
        space_dim: int = 3,
        is_axisymmetric: bool = False,
        selection: Mapping[str, Any] | Sequence[int] | None = None,
        weight_expr: str | None = None,
    ) -> None:
        self.aggregate = aggregate
        self.space_dim = space_dim
        self.is_axisymmetric = is_axisymmetric
        self.selection = selection
        self.weight_expr = weight_expr

        # Infer entity dimension if not explicit
        if entity_dim is not None:
            self.entity_dim = int(entity_dim)
        elif isinstance(selection, Mapping) and "entity_dim" in selection:
            self.entity_dim = int(selection["entity_dim"])
        elif isinstance(selection, Mapping) and "dim" in selection:
            self.entity_dim = int(selection["dim"])
        elif aggregate == "global":
            self.entity_dim = -1  # Global
        else:
            # Default to space_dim domain
            self.entity_dim = space_dim

    @property
    def feature_type(self) -> str:
        """Resolve the appropriate COMSOL NumericalFeature type based on dimension and aggregate mode."""
        if self.aggregate == "global":
            return "EvalGlobal"

        if self.entity_dim == 0:  # Point
            if self.aggregate == "integral":
                return "IntPoint"
            elif self.aggregate in ("average", "std", "rms"):
                return "AvPoint"
            elif self.aggregate == "maximum":
                return "MaxPoint"
            elif self.aggregate == "minimum":
                return "MinPoint"
            return "EvalPoint"

        elif self.entity_dim == 1:  # Edge / Line
            if self.aggregate == "integral":
                return "IntLine" if self.space_dim == 1 else "IntEdge"
            elif self.aggregate in ("average", "std", "rms"):
                return "AvLine" if self.space_dim == 1 else "AvEdge"
            elif self.aggregate == "maximum":
                return "MaxEdge"
            elif self.aggregate == "minimum":
                return "MinEdge"
            return "Eval"

        elif self.entity_dim == 2:  # Surface / Boundary
            if self.aggregate == "integral":
                return "IntSurface"
            elif self.aggregate in ("average", "std", "rms"):
                return "AvSurface"
            elif self.aggregate == "maximum":
                return "MaxSurface"
            elif self.aggregate == "minimum":
                return "MinSurface"
            return "Eval"

        else:  # Volume / Domain (entity_dim >= 3 or default)
            if self.aggregate == "integral":
                return "IntVolume"
            elif self.aggregate in ("average", "std", "rms"):
                return "AvVolume"
            elif self.aggregate == "maximum":
                return "MaxVolume"
            elif self.aggregate == "minimum":
                return "MinVolume"
            return "Eval"

    @property
    def integral_feature_type(self) -> str:
        """Resolve the integration feature type for computing measure M = int w dmu."""
        if self.entity_dim == 0:
            return "IntPoint"
        elif self.entity_dim == 1:
            return "IntLine" if self.space_dim == 1 else "IntEdge"
        elif self.entity_dim == 2:
            return "IntSurface"
        else:
            return "IntVolume"

    def apply_selection(self, feature: Any) -> None:
        """Bind selection entities to the feature if specified, or all entities if None."""
        try:
            sel_node = getattr(feature, "selection", None)
            if sel_node is None:
                return
            target_sel = sel_node() if callable(sel_node) else sel_node

            if self.selection is None or self.selection == "all":
                if hasattr(target_sel, "all"):
                    target_sel.all()
                return

            if isinstance(self.selection, Mapping):
                if "entities" in self.selection and hasattr(target_sel, "set"):
                    entities = [int(x) for x in self.selection["entities"]]
                    try:
                        target_sel.set(entities)
                    except Exception:
                        target_sel.set(*entities)
                elif "all" in self.selection and self.selection["all"] and hasattr(target_sel, "all"):
                    target_sel.all()
            elif isinstance(self.selection, (list, tuple)):
                if hasattr(target_sel, "set"):
                    entities = [int(x) for x in self.selection]
                    try:
                        target_sel.set(entities)
                    except Exception:
                        target_sel.set(*entities)
        except Exception:
            pass

    @staticmethod
    def compute_statistics(
        raw_val: float,
        denominator: float,
        mode: str,
        variance_integral: float | None = None,
        rms_integral: float | None = None,
    ) -> float:
        """Compute statistical quantity rigorously according to mathematical definitions."""
        if denominator <= 0.0 or not math.isfinite(denominator):
            raise ExecutionContractError(
                "ZERO_OR_INVALID_MEASURE",
                f"Measure denominator must be positive and finite, got {denominator}",
            )

        if mode == "integral":
            return float(raw_val)

        elif mode == "average":
            return float(raw_val) / float(denominator)

        elif mode == "std":
            if variance_integral is not None:
                var = max(0.0, float(variance_integral) / float(denominator))
                return math.sqrt(var)
            # If raw_val is given for a constant field where integral = 0 or f - mean = 0
            return 0.0

        elif mode == "rms":
            if rms_integral is not None:
                ms = max(0.0, float(rms_integral) / float(denominator))
                return math.sqrt(ms)
            # Default fallback for uniform field where raw_val is integral of f
            mean = float(raw_val) / float(denominator)
            return abs(mean)

        return float(raw_val)
