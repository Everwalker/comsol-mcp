"""Portable standard-library tests for the three T21 readback fences."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator
import unittest
from unittest.mock import patch

from comsol_mcp import _g3_w13 as w13
from comsol_mcp import _g3_w14 as w14
from comsol_mcp._g2_contract import ExecutionContractError


def _unknown_read() -> ExecutionContractError:
    return ExecutionContractError(
        "EXECUTION_STATE_UNKNOWN",
        "synthetic unresolved readback",
        stage="post_dispatch",
        details={"execution_state_unknown": True},
    )


class FakeTags:
    def __init__(self, tags: list[str]) -> None:
        self._tags = tags
        self.looked_up: list[str] = []

    def tags(self) -> list[str]:
        return list(self._tags)


class FakeSelection:
    def __init__(self, *, unknown_at: str | None = None,
                 ordinary_error_at: str | None = None) -> None:
        self.unknown_at = unknown_at
        self.ordinary_error_at = ordinary_error_at
        self.calls: list[str] = []

    def _read(self, method: str, value: Any) -> Any:
        self.calls.append(method)
        if method == self.unknown_at:
            raise _unknown_read()
        if method == self.ordinary_error_at:
            raise RuntimeError("synthetic ordinary getter error")
        return value

    def entities(self) -> list[int]:
        return self._read("entities", [2, 5])

    def getType(self) -> str:
        return self._read("getType", "ExplicitSelection")

    def dim(self) -> int:
        return self._read("dim", 2)

    def dimension(self) -> int:
        return self._read("dimension", 2)

    def isInheriting(self) -> bool:
        return self._read("isInheriting", False)

    def geom(self) -> str:
        return self._read("geom", "geom1")

    def named(self) -> None:
        return self._read("named", None)


@contextmanager
def _patched_selection(nodes: dict[str, FakeSelection]) -> Iterator[FakeTags]:
    tags = FakeTags(["sel1", "sel2"])

    def child_node(worker: Any, model_tag: str, parent: Any,
                   collection: str, tag: str) -> FakeSelection:
        tags.looked_up.append(tag)
        return nodes[tag]

    with patch.object(w13, "_scope_container", return_value=({}, tags, "comp1")), \
            patch.object(w13, "child_node", side_effect=child_node):
        yield tags


class T21ReadbackFenceTests(unittest.TestCase):
    def test_selection_unknown_stops_later_getters_and_entries(self) -> None:
        first = FakeSelection(unknown_at="dim")
        second = FakeSelection()
        with _patched_selection({"sel1": first, "sel2": second}) as tags:
            with self.assertRaises(ExecutionContractError) as caught:
                w13.selection_list(None, "model1", {"component": "comp1"})

        self.assertEqual(caught.exception.code, "EXECUTION_STATE_UNKNOWN")
        self.assertEqual(first.calls, ["entities", "getType", "dim"])
        self.assertEqual(tags.looked_up, ["sel1"])
        self.assertEqual(second.calls, [])

    def test_selection_ordinary_getter_error_keeps_optional_list_readback(self) -> None:
        first = FakeSelection(ordinary_error_at="entities")
        second = FakeSelection()
        with _patched_selection({"sel1": first, "sel2": second}) as tags:
            result = w13.selection_list(None, "model1", {"component": "comp1"})

        getters = ["entities", "getType", "dim", "dimension", "isInheriting", "geom", "named"]
        self.assertEqual(tags.looked_up, ["sel1", "sel2"])
        self.assertEqual(first.calls, getters)
        self.assertEqual(second.calls, getters)
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["selections"][0]["entities_error"]["code"], "ENGINE_CALL_FAILED")


class FakeCoordinate:
    def __init__(self, tag: str, *, unknown_at: str | None = None,
                 ordinary_error_at: str | None = None) -> None:
        self.tag = tag
        self.unknown_at = unknown_at
        self.ordinary_error_at = ordinary_error_at
        self.calls: list[str] = []

    def label(self) -> str:
        self.calls.append("label")
        if self.unknown_at == "label":
            raise _unknown_read()
        if self.ordinary_error_at == "label":
            raise RuntimeError("synthetic ordinary label error")
        return f"Coordinate {self.tag}"

    def getType(self) -> str:
        self.calls.append("getType")
        return "Cartesian"


class FakeCoordinateList(FakeTags):
    def __init__(self, nodes: dict[str, FakeCoordinate]) -> None:
        super().__init__(list(nodes))
        self.nodes = nodes

    def get(self, tag: str) -> FakeCoordinate:
        self.looked_up.append(tag)
        return self.nodes[tag]


@contextmanager
def _patched_coordinate_list(container: FakeCoordinateList) -> Iterator[None]:
    with patch.object(w14, "_definition_list", return_value=container):
        yield


class T21CoordinateReadbackFenceTests(unittest.TestCase):
    def test_unknown_label_stops_type_probe_and_later_entries(self) -> None:
        first = FakeCoordinate("cs1", unknown_at="label")
        second = FakeCoordinate("cs2")
        container = FakeCoordinateList({"cs1": first, "cs2": second})
        with _patched_coordinate_list(container):
            with self.assertRaises(ExecutionContractError) as caught:
                w14.definition_coordinate_manage(
                    None, "model1", {"action": "list", "component": "comp1"},
                )

        self.assertEqual(caught.exception.code, "EXECUTION_STATE_UNKNOWN")
        self.assertEqual(container.looked_up, ["cs1"])
        self.assertEqual(first.calls, ["label"])
        self.assertEqual(second.calls, [])

    def test_ordinary_label_error_keeps_optional_list_readback(self) -> None:
        first = FakeCoordinate("cs1", ordinary_error_at="label")
        second = FakeCoordinate("cs2")
        container = FakeCoordinateList({"cs1": first, "cs2": second})
        with _patched_coordinate_list(container):
            result = w14.definition_coordinate_manage(
                None, "model1", {"action": "list", "component": "comp1"},
            )

        self.assertEqual(container.looked_up, ["cs1", "cs2"])
        self.assertEqual(first.calls, ["label", "getType"])
        self.assertEqual(second.calls, ["label", "getType"])
        self.assertIsNone(result["coordinate_systems"][0]["label"])
        self.assertEqual(result["coordinate_systems"][1]["label"], "Coordinate cs2")


class FakeGeometrySequence:
    def __init__(self, *, unknown_dimension: bool = False,
                 ordinary_dimension_error: bool = False) -> None:
        self.unknown_dimension = unknown_dimension
        self.ordinary_dimension_error = ordinary_dimension_error
        self.calls: list[str] = []

    def getSDim(self) -> int:
        self.calls.append("getSDim")
        if self.unknown_dimension:
            raise _unknown_read()
        if self.ordinary_dimension_error:
            raise RuntimeError("synthetic ordinary dimension error")
        return 2

    def lengthUnit(self) -> str:
        self.calls.append("lengthUnit")
        return "m"

    def isAxisymmetric(self) -> bool:
        self.calls.append("isAxisymmetric")
        return False

    def feature(self) -> FakeTags:
        self.calls.append("feature")
        return FakeTags([])


class FakeGeometryList(FakeTags):
    def __init__(self, *, unknown_dimension: bool = False,
                 ordinary_dimension_error: bool = False) -> None:
        super().__init__([])
        self.creates: list[tuple[str, int]] = []
        self.sequence = FakeGeometrySequence(
            unknown_dimension=unknown_dimension,
            ordinary_dimension_error=ordinary_dimension_error,
        )

    def create(self, tag: str, dimension: int) -> None:
        self.creates.append((tag, dimension))
        self._tags.append(tag)


class FakeComponent:
    def __init__(self, geometry: FakeGeometryList) -> None:
        self.geometry = geometry

    def geom(self, *args: Any) -> Any:
        return self.geometry.sequence if args else self.geometry


@contextmanager
def _patched_component(geometry: FakeGeometryList) -> Iterator[None]:
    component = FakeComponent(geometry)
    with patch.object(w14, "_require_component", return_value=component):
        yield


class T21GeometryReadbackFenceTests(unittest.TestCase):
    def _create(self) -> dict[str, Any]:
        return w14.geometry_sequence_create(
            None, "model1", {"component": "comp1", "tag": "geom1", "dimension": 2},
        )

    def test_unknown_dimension_retains_created_partial_fact_and_stops(self) -> None:
        geometry = FakeGeometryList(unknown_dimension=True)
        with _patched_component(geometry):
            result = self._create()

        self.assertEqual(geometry.creates, [("geom1", 2)])
        self.assertEqual(geometry.sequence.calls, ["getSDim"])
        self.assertTrue(result["created"])
        self.assertEqual(result["readback"]["geometries"], ["geom1"])
        self.assertEqual(result["applied"], [{"stage": "geometry_sequence_create", "tag": "geom1"}])
        self.assertEqual(result["failed"][0]["code"], "EXECUTION_STATE_UNKNOWN")
        self.assertTrue(result["failed"][0]["partial_change"])
        self.assertTrue(result["execution_state_unknown"])
        self.assertTrue(result["partial_change"])

    def test_ordinary_dimension_error_reports_partial_without_unknown(self) -> None:
        geometry = FakeGeometryList(ordinary_dimension_error=True)
        with _patched_component(geometry):
            result = self._create()

        self.assertEqual(geometry.creates, [("geom1", 2)])
        self.assertEqual(geometry.sequence.calls, ["getSDim"])
        self.assertTrue(result["created"])
        self.assertEqual(result["failed"][0]["code"], "ENGINE_CALL_FAILED")
        self.assertFalse(result["execution_state_unknown"])
        self.assertTrue(result["partial_change"])


if __name__ == "__main__":
    unittest.main()
