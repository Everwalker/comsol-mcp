"""Compatibility controls for unconfigured property metadata inspection."""
import pytest

from comsol_mcp._g2_contract import property_schema_from_engine


class MetadataNode:
    def __init__(self, fail=None, present=True):
        self.fail = fail
        self.present = present
        self.events = []

    def _call(self, method, value):
        self.events.append(method)
        if self.fail == method:
            raise RuntimeError("synthetic ordinary unsupported getter")
        return value

    def getValueType(self, name):
        return self._call("getValueType", "String")

    def getAllowedPropertyValues(self, name):
        return self._call("getAllowedPropertyValues", ["a", "b"])

    def getType(self):
        return self._call("getType", "FakeType")

    def hasProperty(self, name):
        return self._call("hasProperty", self.present)

    def properties(self):
        return self._call("properties", ["p"])


def test_named_metadata_default_healthy():
    node = MetadataNode()
    row = property_schema_from_engine(node, "p")
    assert node.events == ["getValueType", "getAllowedPropertyValues", "getType"]
    assert row["value_type"] == "String"
    assert row["allowed_values"] == ["a", "b"]
    assert row["kind"] == "string"
    assert row["shape_rank"] == 0
    assert row["type_id"] == "FakeType"


@pytest.mark.parametrize("method", ["getValueType", "getAllowedPropertyValues", "getType"])
def test_named_metadata_default_ordinary_optional_miss_continues(method):
    node = MetadataNode(fail=method)
    row = property_schema_from_engine(node, "p")
    assert node.events == ["getValueType", "getAllowedPropertyValues", "getType"]
    field = {"getValueType": "value_type", "getAllowedPropertyValues": "allowed_values", "getType": "type_id"}[method]
    assert row[field] is None


def test_named_metadata_absent_property_skips_value_getters():
    node = MetadataNode(present=False)
    row = property_schema_from_engine(node, "p", probe_has_property=True)
    assert node.events == ["hasProperty", "getType"]
    assert row["has_property"] is False
    assert row["metadata_status"] == "ABSENT"
    assert row["type_id"] == "FakeType"


@pytest.mark.parametrize("method", ["getValueType", "getAllowedPropertyValues", "getType"])
def test_error_callback_can_leave_ordinary_optional_miss_unchanged(method):
    node = MetadataNode(fail=method)
    calls = []

    def observe(exc, probe):
        calls.append((type(exc), probe))

    row = property_schema_from_engine(node, "p", on_probe_error=observe)
    assert node.events == ["getValueType", "getAllowedPropertyValues", "getType"]
    assert calls == [(RuntimeError, method)]
    field = {"getValueType": "value_type", "getAllowedPropertyValues": "allowed_values", "getType": "type_id"}[method]
    assert row[field] is None


class AbortProbe(Exception):
    pass


@pytest.mark.parametrize(
    "method,name,check_presence,present,expected",
    [
        ("properties", None, False, True, ["properties"]),
        ("hasProperty", "p", True, True, ["hasProperty"]),
        ("getType", "p", True, False, ["hasProperty", "getType"]),
        ("getValueType", "p", False, True, ["getValueType"]),
        ("getAllowedPropertyValues", "p", False, True, ["getValueType", "getAllowedPropertyValues"]),
        ("getType", "p", False, True, ["getValueType", "getAllowedPropertyValues", "getType"]),
    ],
)
def test_error_callback_can_stop_at_the_failing_probe(method, name, check_presence, present, expected):
    node = MetadataNode(fail=method, present=present)
    calls = []

    def stop(exc, probe):
        calls.append((type(exc), probe))
        raise AbortProbe(probe) from exc

    with pytest.raises(AbortProbe, match=method):
        property_schema_from_engine(node, name, probe_has_property=check_presence, on_probe_error=stop)
    assert node.events == expected
    assert calls == [(RuntimeError, method)]
