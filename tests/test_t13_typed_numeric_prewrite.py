"""T13: typed-numeric pre-write refusal pins for huge ints and non-finite floats.

These regression cases pin one contract: a typed numeric payload that cannot
round-trip a real IEEE-754 value — a Python integer beyond float64 range or a
synthetic NaN/+infinity — must be refused with PROPERTY_TYPE_MISMATCH before
the first setter dispatch, leaving the fake engine node untouched.  The
finite positive controls pin the other side: a valid value still executes the
actual setter/readback path and keeps its numeric data and shape unchanged.

The ``_Node``/``_Worker`` doubles are shared with
tests/test_t12_typed_property_roundtrip.py; they are not the real engine worker,
and these tests do not assert real-engine, public-wire or scientific behaviour.
"""
from __future__ import annotations

import pytest

from comsol_mcp._execution_contract import ExecutionContractError, PreWriteRefusal
from comsol_mcp._g2_contract import validate_typed_value
from comsol_mcp._g3_common import typed_value_from_json
from tests.test_t12_typed_property_roundtrip import _Node, _sets, _write

#: 10**400 overflows float(); float(10**400) raises OverflowError.
HUGE = 10**400
NEG_HUGE = -(10**400)


# ---------------------------------------------------------------------------
# 1. Actual _g2_engine.property_set through the in-memory fake node
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("huge", [HUGE, NEG_HUGE],
                        ids=["float64-scalar-huge-int-positive", "float64-scalar-huge-int-negative"])
def test_huge_int_in_float64_scalar_refused_before_first_set(huge):
    """A float64 scalar payload whose data overflows float64 must be a
    PreWriteRefusal: no fake set() call and node.values stays untouched."""
    node = _Node()
    with pytest.raises(PreWriteRefusal) as exc:
        _write(node, "ndbl", {"kind": "float64", "shape": [], "data": huge})
    assert exc.value.code == "PROPERTY_TYPE_MISMATCH"
    assert not _sets(node)
    assert node.values["ndbl"] == 2.0


def test_finite_float64_scalar_positive_control_executes_set_and_readback():
    """The finite 2.5 control keeps the supported test_t12 round trip."""
    node = _Node()
    result = _write(node, "ndbl", {"kind": "float64", "shape": [], "data": 2.5})
    assert result["success"] is True, result
    assert _sets(node), "the positive control must reach the fake setter"
    assert _sets(node)[0][2]["java_signature"] == "double"
    assert node.values["ndbl"] == 2.5
    row = result["data"]["applied"][0]
    assert row["readback"]["kind"] == "float64" and row["readback"]["data"] == 2.5
    assert row["comparison"]["rule"] == "exact"


# ---------------------------------------------------------------------------
# 2. Direct validate_typed_value on a complex128 scalar two-number pair.
#    Synthetic non-finite Python floats are inputs here, not strict JSON tokens.
# ---------------------------------------------------------------------------

_COMPLEX_PAIR_CASES = [
    ([HUGE, 1.0], "complex-pair-huge-int-real"),
    ([1.0, HUGE], "complex-pair-huge-int-imag"),
    ([float("nan"), 1.0], "complex-pair-nan-real"),
    ([1.0, float("nan")], "complex-pair-nan-imag"),
    ([float("inf"), 1.0], "complex-pair-posinf-real"),
    ([1.0, float("inf")], "complex-pair-posinf-imag"),
    ([float("-inf"), 1.0], "complex-pair-neginf-real"),
    ([1.0, float("-inf")], "complex-pair-neginf-imag"),
]


@pytest.mark.parametrize("pair", [case[0] for case in _COMPLEX_PAIR_CASES],
                        ids=[case[1] for case in _COMPLEX_PAIR_CASES])
def test_non_finite_or_huge_complex_scalar_pair_refused(pair):
    with pytest.raises(PreWriteRefusal) as exc:
        validate_typed_value({"kind": "complex128", "shape": [], "data": pair})
    assert exc.value.code == "PROPERTY_TYPE_MISMATCH"


def test_finite_complex_pair_positive_control_keeps_exact_components():
    out = validate_typed_value({"kind": "complex128", "shape": [], "data": [1.5, -2.25]})
    assert out["kind"] == "complex128"
    assert list(out["shape"]) == []
    assert list(out["data"]) == [1.5, -2.25]


# ---------------------------------------------------------------------------
# 3. Direct typed_value_from_json on a rank-2 float64 matrix
# ---------------------------------------------------------------------------

_MATRIX_SCHEMA = {"kind": "float64", "shape_rank": 2}

_MATRIX_CASES = [
    ([[HUGE, 1.0], [2.0, 3.0]], "rank2-float64-huge-int-at-row0-col0"),
    ([[1.0, HUGE], [2.0, 3.0]], "rank2-float64-huge-int-at-row0-col1"),
    ([[1.0, 2.0], [3.0, HUGE]], "rank2-float64-huge-int-at-row1-col1"),
]


@pytest.mark.parametrize("matrix", [case[0] for case in _MATRIX_CASES],
                        ids=[case[1] for case in _MATRIX_CASES])
def test_rank2_float64_matrix_with_huge_int_refused(matrix):
    with pytest.raises(ExecutionContractError) as exc:
        typed_value_from_json(matrix, _MATRIX_SCHEMA, label="matrix")
    assert exc.value.code == "PROPERTY_TYPE_MISMATCH"


def test_finite_rank2_float64_matrix_control_keeps_data_and_shape():
    out = typed_value_from_json([[1.0, 2.0], [3.0, 4.0]], _MATRIX_SCHEMA, label="matrix")
    assert out["kind"] == "float64"
    assert out["shape"] == [2, 2]
    assert out["data"] == [[1.0, 2.0], [3.0, 4.0]]
