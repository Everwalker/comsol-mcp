"""T12: typed property round-trip pins for the acceptance checkpoint list.

The existing suites (W08/W12 boolean + empty get, R01 expression/units,
R03 indexed/keyed writes, T008 four-family matrices) already pin most of
the typed property contract.  These tests pin only the checkpoint rows that
had no dedicated software evidence: the int32/float64 scalar round trips,
the empty-array set round trip with its preserved zero-length shape, and the
singleton-vector versus scalar rank distinction in both read and write
directions.  No production behaviour is changed here; each test asserts the
authoritative getter/setter actually used and that every refusal happens
before the first setter call, so a rejected value can never be flattened
into a string or scalar behind the caller's back.
"""
from __future__ import annotations

import pytest

from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._g2_engine import property_get, property_set

PATH = {"segments": []}


class _Node:
    def __init__(self):
        self.values = {"nint": 3, "ndbl": 2.0, "dvec": [1.5], "avec": [7]}
        self.types = {"nint": "Int", "ndbl": "Double", "dvec": "DoubleArray", "avec": "IntArray"}
        self.calls = []

    def properties(self):
        return list(self.values)

    def getValueType(self, name):
        return self.types[name]

    def getAllowedPropertyValues(self, _name):
        return None

    def getType(self):
        return "FakeFeature"

    def getInt(self, name):
        self.calls.append(("getInt", name)); return self.values[name]

    def getIntArray(self, name):
        self.calls.append(("getIntArray", name)); return list(self.values[name])

    def getDouble(self, name):
        self.calls.append(("getDouble", name)); return self.values[name]

    def getDoubleArray(self, name):
        self.calls.append(("getDoubleArray", name)); return list(self.values[name])

    def set(self, name, typed):
        self.calls.append(("set", name, typed)); self.values[name] = typed["data"]


class _Client:
    def __init__(self, node): self.node = node
    def model(self, _tag): return self.node


class _Worker:
    def __init__(self, node): self.node = node
    def client(self): return _Client(self.node)


def _write(node, name, value):
    return property_set(_Worker(node), "m", PATH, [{"name": name, "value": value}])


def _sets(node):
    return [call for call in node.calls if call[0] == "set"]


def test_int32_scalar_round_trip_uses_get_int_and_exact_rule():
    node = _Node()
    result = _write(node, "nint", {"kind": "int32", "shape": [], "data": 7})
    assert result["success"] is True, result
    assert _sets(node)[0][2]["java_signature"] == "int"
    assert node.calls[1] == ("getInt", "nint")
    row = result["data"]["applied"][0]
    assert row["readback"]["kind"] == "int32" and row["readback"]["shape"] == []
    assert row["readback"]["data"] == 7
    assert row["comparison"]["rule"] == "exact"


def test_float64_scalar_round_trip_uses_get_double_and_exact_rule():
    node = _Node()
    result = _write(node, "ndbl", {"kind": "float64", "shape": [], "data": 2.5})
    assert result["success"] is True, result
    assert _sets(node)[0][2]["java_signature"] == "double"
    assert node.calls[1] == ("getDouble", "ndbl")
    row = result["data"]["applied"][0]
    assert row["readback"]["kind"] == "float64" and row["readback"]["data"] == 2.5
    assert row["comparison"]["rule"] == "exact"


def test_int32_into_double_property_is_a_recorded_explicit_widening():
    """The one documented numeric widening stays numeric and keeps its shape."""
    node = _Node()
    result = _write(node, "ndbl", {"kind": "int32", "shape": [], "data": 3})
    assert result["success"] is True, result
    assert _sets(node)[0][2]["kind"] == "float64" and _sets(node)[0][2]["data"] == 3.0
    assert node.calls[1] == ("getDouble", "ndbl")
    row = result["data"]["applied"][0]
    assert row["value"]["kind"] == "float64" and row["value"]["data"] == 3.0
    assert row["readback"]["kind"] == "float64" and row["readback"]["data"] == 3.0
    assert row["comparison"]["matched"] is True


def test_empty_double_vector_round_trip_preserves_the_zero_length_shape():
    node = _Node()
    result = _write(node, "dvec", {"kind": "float64", "shape": [0], "data": []})
    assert result["success"] is True, result
    assert _sets(node)[0][2]["java_signature"] == "double[]"
    assert node.calls[1] == ("getDoubleArray", "dvec")
    row = result["data"]["applied"][0]
    assert row["value"]["shape"] == [0] and row["value"]["data"] == []
    assert row["readback"]["kind"] == "float64" and row["readback"]["shape"] == [0]
    assert row["readback"]["data"] == []
    assert row["comparison"]["rule"] == "exact"
    again = property_get(_Worker(node), "m", PATH, ["dvec"])["properties"][0]["value"]
    assert again["kind"] == "float64" and again["shape"] == [0] and again["data"] == []


def test_empty_array_into_scalar_rank_property_refused_before_set():
    node = _Node()
    with pytest.raises(ExecutionContractError) as exc:
        _write(node, "ndbl", {"kind": "float64", "shape": [0], "data": []})
    assert exc.value.code == "PROPERTY_TYPE_MISMATCH"
    assert not _sets(node)


def test_declared_scalar_shape_with_list_data_refused_before_set():
    node = _Node()
    with pytest.raises(ExecutionContractError) as exc:
        _write(node, "dvec", {"kind": "float64", "shape": [], "data": []})
    assert exc.value.code == "PROPERTY_TYPE_MISMATCH"
    assert not _sets(node)


def test_singleton_int_vector_get_keeps_rank_one_and_its_getter():
    node = _Node()
    row = property_get(_Worker(node), "m", PATH, ["avec"])["properties"][0]["value"]
    assert node.calls == [("getIntArray", "avec")]
    assert row["kind"] == "int32" and row["shape"] == [1] and row["data"] == [7]
    assert row["java_signature"] == "int[]"


def test_singleton_vector_into_scalar_and_scalar_into_vector_are_refused_before_set():
    node = _Node()
    with pytest.raises(ExecutionContractError) as exc:
        _write(node, "nint", {"kind": "int32", "shape": [1], "data": [7]})
    assert exc.value.code == "PROPERTY_TYPE_MISMATCH"
    with pytest.raises(ExecutionContractError) as exc:
        _write(node, "dvec", {"kind": "float64", "shape": [], "data": 1.0})
    assert exc.value.code == "PROPERTY_TYPE_MISMATCH"
    assert not _sets(node)


def test_out_of_range_int32_scalar_refused_before_set():
    node = _Node()
    with pytest.raises(ExecutionContractError) as exc:
        _write(node, "nint", {"kind": "int32", "shape": [], "data": 2**31})
    assert exc.value.code == "PROPERTY_TYPE_MISMATCH"
    assert not _sets(node)
