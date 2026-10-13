"""G3 R01: expression/string readback semantics.

The G2 review found that ``_g2_contract.validate_typed_value`` accepts an
``expression`` value for a String property, while
``_g2_engine._typed_readback_matches`` required the requested and returned
``kind`` to be identical.  A byte-identical expression text therefore became
UNKNOWN after a confirmed write.  These tests expose the old behaviour first
and pin the recorded comparison rule afterwards.
"""
from __future__ import annotations

import pytest

from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._g2_engine import property_set


class _Node:
    def __init__(self):
        self.values = {
            "expr": "a+b",
            "exprs": ["q1", "q2"],
            "qw": "600[kW/m^2]",
            "k": 10.0,
        }
        self.calls = []

    def properties(self):
        return list(self.values)

    def getValueType(self, name):
        return {"expr": "String", "exprs": "StringArray", "qw": "String", "k": "Double"}[name]

    def getAllowedPropertyValues(self, _name):
        return None

    def getType(self):
        return "FakeFeature"

    def getString(self, name):
        self.calls.append(("getString", name))
        return self.values[name] if isinstance(self.values[name], str) else None

    def getStringArray(self, name):
        self.calls.append(("getStringArray", name))
        return list(self.values[name]) if isinstance(self.values[name], list) else None

    def getDouble(self, name):
        self.calls.append(("getDouble", name))
        return self.values[name]

    def set(self, name, typed):
        self.calls.append(("set", name, typed))
        self.values[name] = typed["data"]


class _Client:
    def __init__(self, node): self.node = node
    def model(self, _tag): return self.node


class _Worker:
    def __init__(self, node): self.node = node
    def client(self): return _Client(self.node)


PATH = {"segments": []}


def _write(node, name, value):
    return property_set(_Worker(node), "m", PATH, [{"name": name, "value": value}])


def test_expression_text_into_string_property_is_verified():
    """Byte-identical expression text on a String property must verify.

    Old behaviour: requested kind ``expression`` vs String readback kind
    ``string`` failed the identity check and produced UNKNOWN despite the
    confirmed write.
    """
    node = _Node()
    result = _write(node, "expr", {"kind": "expression", "shape": [], "data": "a+b"})
    assert result["success"] is True, result
    assert result["execution_state_unknown"] is False
    applied = result["data"]["applied"]
    assert [row["name"] for row in applied] == ["expr"]
    row = applied[0]
    # The original expression text is preserved; the readback is the stored
    # string; the comparison rule documents the expression<->string mapping.
    assert row["value"]["kind"] == "expression" and row["value"]["data"] == "a+b"
    assert row["readback"]["kind"] == "string" and row["readback"]["data"] == "a+b"
    assert row["readback_match"] is True
    assert row["comparison"]["rule"] == "exact_text_expression_string_mapping"
    assert ("set", "expr", {"kind": "expression", "shape": [], "data": "a+b", "java_signature": "java.lang.String"}) in node.calls


def test_expression_array_into_string_array_property_is_verified():
    node = _Node()
    result = _write(node, "exprs", {"kind": "expression", "shape": [2], "data": ["q1", "q2"]})
    assert result["success"] is True, result
    row = result["data"]["applied"][0]
    assert row["readback"]["kind"] == "string" and row["readback"]["shape"] == [2]
    assert row["readback"]["data"] == ["q1", "q2"]
    assert row["comparison"]["rule"] == "exact_text_expression_string_mapping"


def test_unit_expression_text_is_preserved_and_verified():
    node = _Node()
    value = {"kind": "expression", "shape": [], "data": "600[kW/m^2]", "unit": "kW/m^2"}
    result = _write(node, "qw", value)
    assert result["success"] is True, result
    row = result["data"]["applied"][0]
    assert row["value"]["data"] == "600[kW/m^2]" and row["value"]["unit"] == "kW/m^2"
    assert row["readback"]["data"] == "600[kW/m^2]"
    assert row["comparison"]["rule"] == "exact_text_expression_string_mapping"


def test_engine_normalized_text_is_not_silently_accepted():
    """A readback that differs in any byte stays a conservative mismatch.

    The engine is simulated as storing a whitespace-normalized form, which is
    exactly the version-specific normalization the G2 review warned about.
    """

    class _NormalizingNode(_Node):
        def set(self, name, typed):
            self.calls.append(("set", name, typed))
            self.values[name] = str(typed["data"]).replace(" ", "")

    node = _NormalizingNode()
    result = _write(node, "expr", {"kind": "expression", "shape": [], "data": "a + b"})
    assert result["success"] is False
    assert result["execution_state_unknown"] is True
    failed = result["data"]["failed"]
    assert len(failed) == 1 and failed[0]["partial_change"] is True
    assert failed[0]["readback"]["data"] == "a+b"
    assert failed[0]["comparison"]["matched"] is False
    assert failed[0]["comparison"]["rule"] == "exact_text_expression_string_mapping_data_mismatch"


def test_expression_matrix_into_string_matrix_property_is_verified():
    class _MatrixNode(_Node):
        def __init__(self):
            super().__init__()
            self.values["m2"] = [["a", "b"], ["c", "d"]]

        def getValueType(self, name):
            return "StringMatrix" if name == "m2" else super().getValueType(name)

        def getStringMatrix(self, name):
            self.calls.append(("getStringMatrix", name))
            return [list(row) for row in self.values[name]]

    node = _MatrixNode()
    result = _write(node, "m2", {"kind": "expression", "shape": [2, 2], "data": [["a", "b"], ["c", "d"]]})
    assert result["success"] is True, result
    row = result["data"]["applied"][0]
    assert row["readback"]["kind"] == "string" and row["readback"]["shape"] == [2, 2]
    assert row["comparison"]["rule"] == "exact_text_expression_string_mapping"


def test_expression_into_numeric_property_rejected_before_write():
    """No expression view exists for a numeric getter; reject before the setter."""
    node = _Node()
    with pytest.raises(ExecutionContractError) as exc:
        _write(node, "k", {"kind": "expression", "shape": [], "data": "2*k0"})
    assert exc.value.code == "PROPERTY_TYPE_MISMATCH"
    assert not [call for call in node.calls if call[0] == "set"]


def test_numeric_unit_field_is_rejected_without_conversion():
    """A unit on a numeric value would imply a conversion this layer never performs."""
    node = _Node()
    with pytest.raises(ExecutionContractError) as exc:
        _write(node, "k", {"kind": "float64", "shape": [], "data": 100.0, "unit": "W/m^2"})
    assert exc.value.code == "PROPERTY_TYPE_MISMATCH"
    assert "unit" in str(exc.value)
    assert not [call for call in node.calls if call[0] == "set"]


def test_exact_string_write_records_exact_rule():
    node = _Node()
    result = _write(node, "expr", {"kind": "string", "shape": [], "data": "a+b"})
    assert result["success"] is True
    row = result["data"]["applied"][0]
    assert row["comparison"]["rule"] == "exact"


def test_numeric_tolerance_must_be_explicit():
    """The comparator supports an explicit tolerance; the default is exact."""
    from comsol_mcp import _g2_engine

    comparison = getattr(_g2_engine, "_typed_readback_comparison", None)
    assert comparison is not None, "recorded comparison helper is missing"
    requested = {"kind": "float64", "shape": [], "data": 1.0}
    returned = {"kind": "float64", "shape": [], "data": 1.0 + 1e-12}
    assert comparison(requested, returned)["matched"] is False
    tolerated = comparison(requested, returned, numeric_tolerance=1e-9)
    assert tolerated["matched"] is True
    assert tolerated["rule"] == "explicit_numeric_tolerance"


def test_expression_shape_mismatch_still_rejected():
    node = _Node()
    with pytest.raises(ExecutionContractError) as exc:
        _write(node, "expr", {"kind": "expression", "shape": [1], "data": ["a+b"]})
    assert exc.value.code == "PROPERTY_TYPE_MISMATCH"
    assert not [call for call in node.calls if call[0] == "set"]
