"""Matrix signatures emitted by the public API remain valid property inputs."""

import pytest

from comsol_mcp._execution_contract import PreWriteRefusal
from comsol_mcp._g2_contract import signature_spec
from comsol_mcp._g2_engine import property_set
from comsol_mcp._g2_public_api import CAPABILITIES, _typed_result


class FakeNode:
    def __init__(self, value_type, value):
        self.value_type = value_type
        self.value = value
        self.calls = []

    def getValueType(self, name):
        return self.value_type

    def getAllowedPropertyValues(self, name):
        return None

    def getType(self):
        return "FakeFeature"

    def set(self, name, value):
        self.calls.append(("set", name))
        self.value = value["data"]

    def __getattr__(self, name):
        getter = "get" + self.value_type
        if name != getter:
            raise AttributeError(name)

        def read(prop):
            self.calls.append((getter, prop))
            return self.value

        return read


class FakeClient:
    def __init__(self, node):
        self.node = node

    def model(self, tag):
        return self.node


class FakeWorker:
    def __init__(self, node):
        self.node = node

    def client(self):
        return FakeClient(self.node)


def set_property(node, value):
    return property_set(FakeWorker(node), "m", {"segments": []},
                        [{"name": "p", "value": value}])


@pytest.mark.parametrize(("family", "data"), [
    ("Boolean", [[True, False]]),
    ("Int", [[1, 2]]),
    ("Double", [[1.0, 2.0]]),
    ("String", [["a", "b"]]),
])
def test_public_matrix_return_can_be_written_with_authoritative_metadata(family, data):
    cap = next(cap for cap in CAPABILITIES if cap.method == "get" + family + "Matrix")
    value = _typed_result(data, cap)["value"]
    node = FakeNode(family + "Matrix", data)
    result = set_property(node, value)
    assert result["success"]
    assert result["applied"][0]["readback"]["data"] == data
    assert result["applied"][0]["readback"]["shape"] == value["shape"]
    assert result["applied"][0]["readback"]["kind"] == value["kind"]
    assert node.calls == [("set", "p"), ("get" + family + "Matrix", "p")]


@pytest.mark.parametrize(("descriptor", "source_name"), [
    ("[[Z", "boolean[][]"),
    ("[[I", "int[][]"),
    ("[[J", "long[][]"),
    ("[[D", "double[][]"),
    ("[[Ljava.lang.String;", "java.lang.String[][]"),
])
def test_jvm_matrix_descriptor_is_equivalent_to_existing_java_type(descriptor, source_name):
    assert signature_spec(source_name) is not None
    assert signature_spec(descriptor) == signature_spec(source_name)


def test_int64_matrix_setter_remains_unsupported():
    node = FakeNode("FutureType", None)
    value = {"kind": "int64", "shape": [1, 1], "data": [[1]], "java_signature": "[[J"}
    with pytest.raises(PreWriteRefusal) as exc:
        set_property(node, value)
    assert exc.value.code == "API_UNSUPPORTED"
    assert "int64 setter" in str(exc.value)
    assert node.calls == []


@pytest.mark.parametrize("value", [
    pytest.param({"kind": "int32", "shape": [1, 1], "data": [[1]], "java_signature": "[[F"}, id="unknown-signature"),
    pytest.param({"kind": "int32", "shape": [1, 1], "data": [[1]], "java_signature": "[[D"}, id="wrong-type"),
    pytest.param({"kind": "int32", "shape": [1, 1], "data": [[2**31]], "java_signature": "[[I"}, id="out-of-range"),
    pytest.param({"kind": "int32", "shape": [2, 2], "data": [[1], [2, 3]], "java_signature": "[[I"}, id="ragged"),
    pytest.param({"kind": "int32", "shape": [1], "data": [1], "java_signature": "[I"}, id="wrong-rank"),
])
def test_invalid_matrix_inputs_are_refused_before_set(value):
    node = FakeNode("IntMatrix", [[1]])
    with pytest.raises(PreWriteRefusal):
        set_property(node, value)
    assert node.calls == []
