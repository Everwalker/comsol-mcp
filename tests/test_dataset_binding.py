"""Independent read-only tests for native dataset binding resolution."""
from __future__ import annotations

from typing import Any

import pytest

from comsol_mcp._dataset_binding import resolve_dataset_binding
from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._g3_results import _coordinate_context


class Geometry:
    def __init__(self, tags: list[str]) -> None:
        self._tags = list(tags)

    def tags(self) -> list[str]:
        return list(self._tags)

    def get(self, tag: str) -> object:
        if tag not in self._tags:
            raise KeyError(tag)
        return object()


class Component:
    def __init__(self, geometries: list[str]) -> None:
        self._geometry = Geometry(geometries)

    def geom(self, tag: str | None = None) -> Any:
        if tag is None:
            return self._geometry
        return self._geometry.get(tag)


class ModelNode:
    def __init__(self, tags: list[str]) -> None:
        self._tags = list(tags)

    def tags(self) -> list[str]:
        return list(self._tags)


class Node:
    def __init__(self, type_id: str, properties: dict[str, str], *, unreadable: set[str] | None = None) -> None:
        self.type_id = type_id
        self.values = dict(properties)
        self.unreadable = set(unreadable or ())
        self.mutations: list[str] = []

    def getType(self) -> str:
        return self.type_id

    def properties(self) -> list[str]:
        return list(self.values)

    def getString(self, name: str) -> str:
        if name in self.unreadable:
            raise RuntimeError(f"unreadable {name}")
        if name not in self.values:
            raise KeyError(name)
        return self.values[name]


class Tagged:
    def __init__(self, entries: dict[str, Any]) -> None:
        self.entries = entries

    def tags(self) -> list[str]:
        return list(self.entries)

    def get(self, tag: str) -> Any:
        return self.entries[tag]


class Results:
    def __init__(self, datasets: Tagged) -> None:
        self._datasets = datasets

    def dataset(self) -> Tagged:
        return self._datasets


class Model:
    def __init__(self, datasets: dict[str, Node], solutions: list[str],
                 components: dict[str, Component]) -> None:
        self.datasets = Tagged(datasets)
        self.solutions = Tagged({tag: object() for tag in solutions})
        self.components = components
        self.calls: list[str] = []

    def result(self) -> Results:
        return Results(self.datasets)

    def sol(self) -> Tagged:
        return self.solutions

    def modelNode(self) -> ModelNode:
        return ModelNode(list(self.components))

    def component(self, tag: str) -> Component:
        return self.components[tag]


def _model(*, extra: dict[str, Node] | None = None, solutions: list[str] | None = None,
           components: dict[str, Component] | None = None) -> Model:
    datasets = {
        "dset1": Node("Solution", {"solution": "sol1", "comp": "comp1", "geom": "geom1"}),
    }
    if extra:
        datasets.update(extra)
    return Model(datasets, solutions or ["sol1"], components or {"comp1": Component(["geom1"])})


def test_solution_binding_reads_real_solution_component_geometry_and_source() -> None:
    model = _model()
    result = resolve_dataset_binding(model, "dset1", requested_solution="sol1")
    assert result["binding_complete"] is True
    assert result["solution"] == "sol1"
    assert result["component"] == "comp1"
    assert result["geometry"] == "geom1"
    assert result["dataset_chain"] == ["dset1"]
    assert "dataset.solution" in result["sources"]["solution"]
    assert result["provenance"]["read_only"] is True
    assert result["provenance"]["mutation_methods_called"] == []


def test_cutpoint_resolves_upstream_solution_and_unique_chain() -> None:
    model = _model(extra={
        "cut1": Node("CutPoint2D", {"data": "dset1", "pointx": "0.025", "pointy": "0.005"}),
    })
    result = resolve_dataset_binding(model, "cut1")
    assert result["binding_complete"] is True
    assert result["solution"] == "sol1"
    assert result["component"] == "comp1"
    assert result["geometry"] == "geom1"
    assert result["dataset_chain"] == ["dset1", "cut1"]
    assert result["dataset_edges"] == [{"property": "data", "from": "cut1", "to": "dset1"}]


def test_join_same_solution_is_compatible_and_preserves_both_edges() -> None:
    model = _model(extra={
        "join1": Node("Join", {"data": "dset1", "data2": "dset1"}),
    })
    result = resolve_dataset_binding(model, "join1")
    assert result["binding_complete"] is True
    assert result["solution"] == "sol1"
    assert {row["property"] for row in result["dataset_edges"]} == {"data", "data2"}
    assert result["dataset_chain"] == ["dset1", "join1"]


def test_join_with_incompatible_solution_is_explicitly_rejected() -> None:
    model = _model(
        extra={
            "dset2": Node("Solution", {"solution": "sol2", "comp": "comp1", "geom": "geom1"}),
            "join1": Node("Join", {"data": "dset1", "data2": "dset2"}),
        },
        solutions=["sol1", "sol2"],
    )
    result = resolve_dataset_binding(model, "join1")
    assert result["binding_complete"] is False
    assert result["error"]["code"] == "INCOMPATIBLE_DATASET_BINDING"


def test_cycle_missing_reference_and_wrong_component_or_geometry_fail_closed() -> None:
    cycle = _model(extra={
        "a": Node("CutLine2D", {"data": "b"}),
        "b": Node("CutLine2D", {"data": "a"}),
        "missing": Node("CutPoint2D", {"data": "no_such_dataset"}),
        "badcomp": Node("CutPoint2D", {"data": "dset1", "comp": "comp_missing"}),
        "badgeom": Node("CutPoint2D", {"data": "dset1", "comp": "comp1", "geom": "geom_missing"}),
    })
    assert resolve_dataset_binding(cycle, "a")["error"]["code"] == "DATASET_CYCLE_DETECTED"
    assert resolve_dataset_binding(cycle, "missing")["error"]["code"] == "DATASET_REFERENCE_MISSING"
    assert resolve_dataset_binding(cycle, "badcomp")["error"]["code"] == "COMPONENT_NOT_FOUND"
    assert resolve_dataset_binding(cycle, "badgeom")["error"]["code"] == "GEOMETRY_NOT_FOUND"


def test_solution_wrong_component_or_geometry_never_falls_back_to_direct_properties() -> None:
    model = _model(extra={
        "bad_solution_comp": Node("Solution", {"solution": "sol1", "comp": "comp_missing", "geom": "geom1"}),
        "bad_solution_geom": Node("Solution", {"solution": "sol1", "comp": "comp1", "geom": "geom_missing"}),
    })
    bad_comp = resolve_dataset_binding(model, "bad_solution_comp")
    bad_geom = resolve_dataset_binding(model, "bad_solution_geom")
    assert bad_comp["binding_complete"] is False
    assert bad_comp["error"]["code"] == "COMPONENT_NOT_FOUND"
    assert bad_geom["binding_complete"] is False
    assert bad_geom["error"]["code"] == "GEOMETRY_NOT_FOUND"


def test_coordinate_context_rejects_incomplete_solution_binding_without_legacy_fallback() -> None:
    class NoReadModel:
        def component(self, *_args: object) -> object:
            raise AssertionError("incomplete binding must not inspect a guessed component")

    dataset_node = object()
    with pytest.raises(ExecutionContractError) as exc_info:
        _coordinate_context(
            NoReadModel(),
            dataset_node,
            [],
            dataset_binding={
                "dataset": "dset1",
                "dataset_type": "Solution",
                "binding_complete": False,
                "error": {"code": "ENGINE_READ_FAILED", "message": "solution resolver failed"},
                "read_errors": [{"code": "ENGINE_READ_FAILED", "message": "solution resolver failed"}],
            },
        )
    assert exc_info.value.code == "DATASET_BINDING_INCOMPLETE"


def test_coordinate_context_rejects_missing_binding_witness() -> None:
    with pytest.raises(ExecutionContractError) as exc_info:
        _coordinate_context(object(), object(), [], dataset_binding=None)
    assert exc_info.value.code == "DATASET_BINDING_UNAVAILABLE"


def test_unique_single_component_geometry_derivation_is_the_only_default() -> None:
    model = _model()
    model.datasets.entries["defaulted"] = Node("Solution", {"solution": "sol1"})
    result = resolve_dataset_binding(model, "defaulted")
    assert result["binding_complete"] is True
    assert result["component"] == "comp1"
    assert result["geometry"] == "geom1"
    assert result["sources"]["component"].startswith("unique")
    assert result["sources"]["geometry"].startswith("unique")


def test_unreadable_required_property_never_becomes_complete() -> None:
    model = _model()
    model.datasets.entries["unreadable"] = Node(
        "Solution", {"solution": "sol1", "comp": "comp1", "geom": "geom1"}, unreadable={"solution"}
    )
    result = resolve_dataset_binding(model, "unreadable")
    assert result["binding_complete"] is False
    assert result["error"]["code"] in {"ENGINE_READ_FAILED", "SOLUTION_NOT_FOUND"}
    assert result["read_errors"]


def test_requested_solution_mismatch_is_explicit() -> None:
    result = resolve_dataset_binding(_model(), "dset1", requested_solution="sol2")
    assert result["binding_complete"] is False
    assert result["error"]["code"] == "SOLUTION_MISMATCH"


def test_requested_solution_cannot_fill_missing_dataset_solution_property() -> None:
    model = _model(solutions=["sol1", "sol2"])
    model.datasets.entries["unbound"] = Node("Solution", {})

    result = resolve_dataset_binding(model, "unbound", requested_solution="sol2")

    assert result["binding_complete"] is False
    assert result["solution"] is None
    assert result["error"]["code"] == "SOLUTION_NOT_FOUND"
