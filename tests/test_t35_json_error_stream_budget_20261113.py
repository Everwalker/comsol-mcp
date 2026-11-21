"""Adversarial Python-allocation and selected-source-budget checks.

Only the task-owned pure JSON projector is called. These tests do not use a
store, a model, a Worker, networking or a disk database. Traced Python
allocations are not SQLite C, process RSS or disk measurements; limits below
are proposed regression budgets, not user-specified public contract values.
"""
from __future__ import annotations

import gc
import json
import tracemalloc

import pytest  # pyright: ignore[reportMissingImports]

from comsol_mcp._json_error_stream import (
    SelectedErrorBudgetExceededError,
    project_error_chunks,
)

_CHUNK = 64 * 1024
_FLOAT_DIGITS = 2 * 1024 * 1024
_PEAK_ALLOWANCE = 2 * 1024 * 1024


def _chunks(data: bytes):
    for offset in range(0, len(data), _CHUNK):
        yield data[offset:offset + _CHUNK]


def _peak(data: bytes):
    gc.collect()
    assert not tracemalloc.is_tracing(), "test requires exclusive ownership of tracemalloc"
    tracemalloc.start()
    try:
        value = project_error_chunks(_chunks(data), max_error_bytes=256)
        _, peak = tracemalloc.get_traced_memory()
        return value, peak
    finally:
        tracemalloc.stop()


def test_skipped_large_valid_float_token_does_not_scale_python_memory():
    expected = {"error": {"code": "T35-BUDGET-SMALL"}}
    small = b'{"payload":0.1,"error":{"code":"T35-BUDGET-SMALL"}}'
    large = b'{"payload":0.' + b'1' * _FLOAT_DIGITS + b',"error":{"code":"T35-BUDGET-SMALL"}}'
    # Establish independent whole-document validity before allocation tracing.
    assert json.loads(small)["error"] == expected["error"]
    assert json.loads(large)["error"] == expected["error"]
    small_value, small_peak = _peak(small)
    large_value, large_peak = _peak(large)
    assert small_value == large_value == expected
    assert large_peak - small_peak <= _PEAK_ALLOWANCE, (
        f"skipped float token grew Python traced peak: small={small_peak}, "
        f"large={large_peak}, delta={large_peak - small_peak}, "
        f"proposed allowance={_PEAK_ALLOWANCE}"
    )


def test_selected_error_source_budget_counts_internal_whitespace():
    small = b'{"error":[1]}'
    oversized = b'{"error":[' + b' ' * 512 + b'1]}'
    assert json.loads(small) == json.loads(oversized) == {"error": [1]}
    assert project_error_chunks(_chunks(small), max_error_bytes=64) == {"error": [1]}
    with pytest.raises(SelectedErrorBudgetExceededError):
        project_error_chunks(_chunks(oversized), max_error_bytes=64)
