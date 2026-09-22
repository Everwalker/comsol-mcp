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
            if isinstance(entity_dim, bool) or not isinstance(entity_dim, int):
                raise ExecutionContractError("INVALID_SELECTION", "entity_dim must be an integer")
            self.entity_dim = entity_dim
        elif isinstance(selection, Mapping) and "entity_dim" in selection:
            value = selection["entity_dim"]
            if isinstance(value, bool) or not isinstance(value, int):
                raise ExecutionContractError("INVALID_SELECTION", "selection.entity_dim must be an integer")
            self.entity_dim = value
        elif isinstance(selection, Mapping) and "entity_dimension" in selection:
            value = selection["entity_dimension"]
            if isinstance(value, bool) or not isinstance(value, int):
                raise ExecutionContractError("INVALID_SELECTION", "selection.entity_dimension must be an integer")
            self.entity_dim = value
        elif isinstance(selection, Mapping) and "dim" in selection:
            value = selection["dim"]
            if isinstance(value, bool) or not isinstance(value, int):
                raise ExecutionContractError("INVALID_SELECTION", "selection.dim must be an integer")
            self.entity_dim = value
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
            # COMSOL 6.4 rejects IntPoint/AvPoint/MinPoint/MaxPoint.  EvalPoint
            # is the native operation; point aggregates are computed from its
            # expression-major rows with an explicit counting measure.
            return "EvalPoint"

        elif self.entity_dim == 1:  # Edge / Line
            if self.aggregate == "integral":
                # Derived-value numerical features use the geometric measure
                # type (IntLine) for a one-dimensional selection even when it
                # is embedded in a 2D/3D geometry.  IntEdge is a GUI/entity
                # label and is not the documented integration feature id.
                return "IntLine"
            elif self.aggregate in ("average", "std", "rms"):
                return "AvLine"
            elif self.aggregate == "maximum":
                return "MaxLine"
            elif self.aggregate == "minimum":
                return "MinLine"
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
            return "EvalPoint"
        elif self.entity_dim == 1:
            return "IntLine"
        elif self.entity_dim == 2:
            return "IntSurface"
        else:
            return "IntVolume"

    def apply_selection(self, feature: Any) -> None:
        """Bind a verified selection and propagate every failed engine call.

        An explicit selection is part of the numerical meaning of the request.
        Swallowing a failed ``selection().set`` changes a domain integral into
        an integral over the feature's default selection, so this method is
        deliberately fail-closed.
        """
        if self.entity_dim < -1 or self.entity_dim > self.space_dim:
            raise ExecutionContractError(
                "INVALID_SELECTION",
                f"entity dimension {self.entity_dim} is outside the model space dimension {self.space_dim}",
            )

        selection = self.selection
        if isinstance(selection, Mapping):
            declared_dim = selection.get(
                "entity_dim",
                selection.get("entity_dimension", selection.get("dim")),
            )
            if declared_dim is not None:
                if isinstance(declared_dim, bool) or not isinstance(declared_dim, int):
                    raise ExecutionContractError("INVALID_SELECTION", "selection dimension must be an integer")
                if int(declared_dim) != self.entity_dim:
                    raise ExecutionContractError(
                        "INVALID_SELECTION",
                        f"selection dimension {declared_dim} does not match entity_dim {self.entity_dim}",
                    )
            allowed = {
                "kind", "component", "geometry", "tag",
                "entities", "all", "named", "name", "entity_dim",
                "entity_dimension", "dim",
            }
            unknown = set(selection) - allowed
            if unknown:
                raise ExecutionContractError("INVALID_SELECTION", f"unsupported selection fields: {', '.join(sorted(unknown))}")
            kind = selection.get("kind")
            if kind is not None:
                if kind in {"spatial", "objects", "inherited"}:
                    raise ExecutionContractError(
                        "API_UNSUPPORTED",
                        f"selection kind {kind!r} is not supported by numerical feature binding",
                    )
                if kind not in {"named", "explicit", "all"}:
                    raise ExecutionContractError("INVALID_SELECTION", f"unsupported selection kind {kind!r}")
            if "entities" in selection:
                entities_value = selection["entities"]
                if isinstance(entities_value, (str, bytes)) or not isinstance(entities_value, Sequence) or not entities_value:
                    raise ExecutionContractError("INVALID_SELECTION", "selection.entities must be a non-empty integer array")
                if any(isinstance(value, bool) or not isinstance(value, int) for value in entities_value):
                    raise ExecutionContractError("INVALID_SELECTION", "selection.entities must contain integer values")
                entities = list(entities_value)
                if any(value < 1 for value in entities):
                    raise ExecutionContractError("INVALID_SELECTION", "selection entity indices must be positive")
            else:
                entities = None
            all_selected = bool(selection.get("all", False)) or kind == "all"
            named = selection.get("named", selection.get("name", selection.get("tag")))
            if kind == "explicit" and entities is None:
                raise ExecutionContractError("INVALID_SELECTION", "selection kind 'explicit' requires entities")
            if kind == "named" and named is None:
                raise ExecutionContractError("INVALID_SELECTION", "selection kind 'named' requires name or tag")
            if kind == "named" and entities is not None:
                raise ExecutionContractError("INVALID_SELECTION", "selection kind 'named' cannot carry entities")
            if all_selected and (entities is not None or named is not None):
                raise ExecutionContractError("INVALID_SELECTION", "selection.all cannot be combined with entities or named")
            if named is not None and (not isinstance(named, str) or not named):
                raise ExecutionContractError("INVALID_SELECTION", "selection.named must be a non-empty string")
        elif selection is None or selection == "all":
            entities = None
            all_selected = True
            named = None
        elif isinstance(selection, Sequence) and not isinstance(selection, (str, bytes)):
            if not selection:
                raise ExecutionContractError("INVALID_SELECTION", "selection array cannot be empty")
            if any(isinstance(value, bool) or not isinstance(value, int) for value in selection):
                raise ExecutionContractError("INVALID_SELECTION", "selection array must contain integer values")
            entities = list(selection)
            if any(value < 1 for value in entities):
                raise ExecutionContractError("INVALID_SELECTION", "selection entity indices must be positive")
            all_selected = False
            named = None
        else:
            raise ExecutionContractError("INVALID_SELECTION", "selection must be all, named, or explicit entity indices")

        sel_node = getattr(feature, "selection", None)
        if sel_node is None:
            if entities is None and named is None and all_selected and selection is None:
                return
            raise ExecutionContractError("SELECTION_APPLY_FAILED", "numerical feature has no selection accessor")
        try:
            target_sel = sel_node() if callable(sel_node) else sel_node
            if target_sel is None:
                raise AttributeError("selection() returned None")
            if named is not None:
                method = getattr(target_sel, "named", None)
                if not callable(method):
                    raise AttributeError("selection does not expose named()")
                method(named)
            elif entities is not None:
                method = getattr(target_sel, "set", None)
                if not callable(method):
                    raise AttributeError("selection does not expose set()")
                try:
                    method(entities)
                except Exception as first_exc:
                    # COMSOL's Java overload is varargs; plain fixtures often
                    # use a list.  Try the other documented shape, then keep
                    # both causes in the contract error.
                    try:
                        method(*entities)
                    except Exception as second_exc:
                        raise ExecutionContractError(
                            "SELECTION_APPLY_FAILED",
                            f"selection.set failed for entities {entities!r}: {first_exc}; varargs retry: {second_exc}",
                        ) from second_exc
            elif all_selected:
                method = getattr(target_sel, "all", None)
                if not callable(method):
                    raise AttributeError("selection does not expose all()")
                method()
        except ExecutionContractError:
            raise
        except Exception as exc:
            raise ExecutionContractError(
                "SELECTION_APPLY_FAILED",
                f"selection binding failed: {type(exc).__name__}: {str(exc)[:500]}",
            ) from exc

    @staticmethod
    def compute_statistics(
        raw_val: float,
        denominator: float,
        mode: str,
        variance_integral: float | None = None,
        rms_integral: float | None = None,
    ) -> float:
        """Compute statistical quantity rigorously according to mathematical definitions."""
        if isinstance(denominator, bool) or not isinstance(denominator, (int, float)) or denominator <= 0.0 or not math.isfinite(float(denominator)):
            raise ExecutionContractError(
                "ZERO_OR_INVALID_MEASURE",
                f"Measure denominator must be positive and finite, got {denominator}",
            )

        try:
            raw_number = float(raw_val)
        except (TypeError, ValueError) as exc:
            raise ExecutionContractError("INVALID_STATISTIC_INTEGRAL", f"raw value is not finite real data: {raw_val!r}") from exc
        if not math.isfinite(raw_number):
            raise ExecutionContractError("INVALID_STATISTIC_INTEGRAL", f"raw value is not finite: {raw_val!r}")

        if mode == "integral":
            return raw_number

        elif mode == "average":
            return raw_number / float(denominator)

        elif mode == "std":
            if variance_integral is None:
                raise ExecutionContractError("MISSING_STATISTIC_INTEGRAL", "std requires an engine-evaluated variance integral")
            value = float(variance_integral)
            if not math.isfinite(value):
                raise ExecutionContractError("INVALID_STATISTIC_INTEGRAL", "variance integral is not finite")
            if value < -1e-12 * max(1.0, abs(raw_number)):
                raise ExecutionContractError("INVALID_STATISTIC_INTEGRAL", f"variance integral is negative: {value}")
            var = 0.0 if value < 0.0 else value / float(denominator)
            return math.sqrt(var)

        elif mode == "rms":
            if rms_integral is None:
                raise ExecutionContractError("MISSING_STATISTIC_INTEGRAL", "rms requires an engine-evaluated |f|^2 integral")
            value = float(rms_integral)
            if not math.isfinite(value):
                raise ExecutionContractError("INVALID_STATISTIC_INTEGRAL", "RMS integral is not finite")
            if value < -1e-12 * max(1.0, abs(raw_number)):
                raise ExecutionContractError("INVALID_STATISTIC_INTEGRAL", f"RMS integral is negative: {value}")
            ms = 0.0 if value < 0.0 else value / float(denominator)
            return math.sqrt(ms)

        return float(raw_val)
