"""Complex Field Transformation Service for COMSOL MCP (F05).

Provides strict complex transformations:
- Modes: preserve, real, imag, abs, phase.
- Disallows silent zero-padding when imaginary data is missing on known complex results.
- Enforces strict shape matching between real and imaginary arrays.
- Implements standard branch cut [-pi, pi] for phase and explicit zero-magnitude behavior (phase=0.0).
"""
from __future__ import annotations

import math
from typing import Any, Sequence

from ._execution_contract import ExecutionContractError

COMPLEX_MODES = frozenset({"preserve", "real", "imag", "abs", "phase"})

_ENGINE_TRANSFORM_FUNCTIONS = {
    "real": "real",
    "imag": "imag",
    "abs": "abs",
    "phase": "arg",
}


def effective_engine_expression(expression: str, mode: str) -> str:
    """Return the COMSOL expression for a pre-statistics complex transform.

    Result statistics must use one scalar field consistently for the mean,
    second moment, RMS, and extrema.  Applying ``phase`` (or another mode)
    after an average would compute ``arg(mean(f))`` rather than
    ``mean(arg(f))``.  The engine has native real/imag/abs/arg expression
    functions, so the caller can bind this expression to every numerical
    feature participating in one aggregate.

    ``preserve`` deliberately returns the requested expression unchanged;
    preserve-mode statistics retain the complex field and use ``|f|²`` for
    their population second moment.
    """
    if not isinstance(expression, str) or not expression.strip():
        raise ExecutionContractError("INVALID_REQUEST", "expression must be a non-empty string")
    if mode not in COMPLEX_MODES:
        raise ExecutionContractError("API_UNSUPPORTED", f"unsupported complex_mode {mode!r}")
    if mode == "preserve":
        return expression
    function = _ENGINE_TRANSFORM_FUNCTIONS[mode]
    return f"{function}(({expression}))"


def transform_complex_value(real_val: float, imag_val: float, mode: str) -> Any:
    """Transform a single complex scalar according to the requested mode."""
    try:
        r = float(real_val)
        i = float(imag_val)
    except (TypeError, ValueError) as exc:
        raise ExecutionContractError("COMPLEX_DATA_ERROR", "complex components must be numeric") from exc
    if not math.isfinite(r) or not math.isfinite(i):
        raise ExecutionContractError("COMPLEX_DATA_ERROR", "complex components must be finite")
    if mode == "preserve":
        return {"real": r, "imag": i}
    elif mode == "real":
        return r
    elif mode == "imag":
        return i
    elif mode == "abs":
        return math.hypot(r, i)
    elif mode == "phase":
        if r == 0.0 and i == 0.0:
            return 0.0
        return math.atan2(i, r)
    raise ExecutionContractError("API_UNSUPPORTED", f"unsupported complex_mode {mode!r}")


def transform_complex_data(
    real_data: Any,
    imag_data: Any,
    mode: str,
    *,
    is_complex: bool | None = None,
    allow_real_fallback: bool = False,
) -> Any:
    """Recursively transform real/imag arrays into the requested complex_mode representation.

    If data is declared complex or imag_data is expected but missing/mismatched,
    raises ExecutionContractError rather than silently padding with zeros.
    """
    if mode not in COMPLEX_MODES:
        raise ExecutionContractError("API_UNSUPPORTED", f"unsupported complex_mode {mode!r}")

    # Scalar case.  ``is_complex=None`` means the engine status was not read;
    # it is not equivalent to a confirmed real field.
    if isinstance(real_data, (int, float)) and not isinstance(real_data, bool):
        if imag_data is None:
            if is_complex is not False:
                raise ExecutionContractError(
                    "COMPLEX_DATA_ERROR",
                    "imaginary component is unavailable while complex status is unknown or complex",
                )
            imag_v = 0.0
        elif isinstance(imag_data, (int, float)) and not isinstance(imag_data, bool):
            imag_v = float(imag_data)
        else:
            raise ExecutionContractError(
                "COMPLEX_DATA_ERROR",
                f"type mismatch: real scalar got non-scalar imaginary {type(imag_data).__name__}",
            )
        return transform_complex_value(float(real_data), imag_v, mode)

    # Sequence case
    if isinstance(real_data, Sequence) and not isinstance(real_data, (str, bytes)):
        if imag_data is None:
            if is_complex is not False:
                raise ExecutionContractError(
                    "COMPLEX_DATA_ERROR",
                    "imaginary component sequence is unavailable while complex status is unknown or complex",
                )
            return [
                transform_complex_data(r, None, mode, is_complex=False, allow_real_fallback=True)
                for r in real_data
            ]

        if not isinstance(imag_data, Sequence) or isinstance(imag_data, (str, bytes)):
            raise ExecutionContractError(
                "COMPLEX_DATA_ERROR",
                f"shape mismatch: real sequence got imaginary of type {type(imag_data).__name__}",
            )

        if len(imag_data) != len(real_data):
            raise ExecutionContractError(
                "COMPLEX_DATA_ERROR",
                f"length mismatch: real length {len(real_data)} != imag length {len(imag_data)}",
            )

        return [
            transform_complex_data(r, i, mode, is_complex=is_complex, allow_real_fallback=allow_real_fallback)
            for r, i in zip(real_data, imag_data)
        ]

    raise ExecutionContractError("COMPLEX_DATA_ERROR", f"real component has unsupported type {type(real_data).__name__}")
