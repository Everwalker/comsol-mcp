"""W17: Result System Acceptance and Regression Suite.

Covers:
- Dataset CRUD and lifecycle: dataset.list, dataset.create, dataset.inspect,
  dataset.update, dataset.remove, dataset.solution_indices
- SolutionSpec resolution and index bounds checking (T021)
- Complex field component extraction (preserve, real, imag, abs, phase) (T014)
- Measures and aggregations: global, integral, average, min, max, std, rms,
  and axisymmetric 2*pi*r weighting exactly once (T013)
- Point evaluation and coordinate readback verification (result.at_points)
- Probe / Derived values management (result.numerical_manage) with ephemeral cleanup
- Table management (result.table_manage)
- Field export, large data artifact pagination, and chunk verification (T049)
- Error handling, idempotency, and fail-closed state escalation (T033, T035, T038)
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import pytest

from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp import _g3_results as results
from comsol_mcp._g3_ops import (
    DISPATCH,
    EFFECTS,
    IMPLEMENTED_OPERATIONS,
    OPERATION_ORIGINS,
    REQUIRES_ISOLATION,
    dispatch,
)


# ---------------------------------------------------------------------------
# Fake Engine Fixtures for W17 Testing
# ---------------------------------------------------------------------------

class FakeEngineError(RuntimeError):
    def __init__(self, message: str, *, code: str = "ENGINE_CALL_FAILED") -> None:
        super().__init__(message)
        self.code = code
        self.reply = {"ok": False, "code": code, "message": message}
        self.failure = {"code": code, "message": message}


class FEntity:
    def __init__(self, *, unavailable: Sequence[str] = ()) -> None:
        self.unavailable = set(unavailable)
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def _guard(self, name: str, args: tuple[Any, ...]) -> None:
        self.calls.append((name, args))
        if name in self.unavailable:
            raise FakeEngineError(f"SecurityException: METHOD_REJECTED ({name})", code="METHOD_REJECTED")


class FList(FEntity):
    def __init__(self, *, factory: Any = None, node_type: str = "Fake", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.items: dict[str, Any] = {}
        self.factory = factory
        self.node_type = node_type

    def tags(self) -> list[str]:
        self._guard("tags", ())
        return list(self.items)

    def get(self, tag: str) -> Any:
        self._guard("get", (tag,))
        if tag not in self.items:
            raise FakeEngineError(f"no such tag {tag}")
        return self.items[tag]

    def create(self, tag: str, *args: Any) -> Any:
        self._guard("create", (tag,) + args)
        if tag in self.items:
            raise FakeEngineError(f"tag {tag} already exists")
        node = self.factory(tag, *args) if self.factory else FNode(tag=tag, type_id=str(args[0]) if args else self.node_type)
        self.items[tag] = node
        return node

    def remove(self, tag: str) -> None:
        self._guard("remove", (tag,))
        if tag not in self.items:
            raise FakeEngineError(f"no such tag {tag}")
        del self.items[tag]


class FNode(FEntity):
    def __init__(self, tag: str = "node", type_id: str = "Node", *,
                 props: Mapping[str, Any] | None = None, collections: Mapping[str, Any] | None = None,
                 **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.tag_ = tag
        self.type_id = type_id
        self.props: dict[str, Any] = dict(props or {})
        self.collections: dict[str, Any] = dict(collections or {})

    def tag(self) -> str:
        self._guard("tag", ())
        return self.tag_

    def getType(self) -> str:
        self._guard("getType", ())
        return self.type_id

    def properties(self) -> list[str]:
        self._guard("properties", ())
        return sorted(self.props)

    def getString(self, name: str) -> Any:
        self._guard("getString", (name,))
        return self.props.get(name)

    def set(self, name: str, value: Any) -> None:
        self._guard("set", (name, value))
        self.props[name] = value

    def _collection(self, name: str, args: tuple[Any, ...]) -> Any:
        self._guard(name, args)
        container = self.collections.get(name)
        if container is None:
            raise FakeEngineError(f"node has no collection {name}")
        if args:
            return container.get(str(args[0]))
        return container

    def __getattr__(self, name: str) -> Any:
        collections = self.__dict__.get("collections") or {}
        if name in collections:
            def accessor(*args: Any, _name: str = name) -> Any:
                return self._collection(_name, args)
            return accessor
        raise AttributeError(name)


class FNumericalFeature(FNode):
    def __init__(self, tag: str, type_id: str, *,
                 real_data: Any = None, imag_data: Any = None,
                 is_complex: bool = False, coordinates: Any = None,
                 fail_cleanup: bool = False, **kwargs: Any) -> None:
        super().__init__(tag=tag, type_id=type_id, **kwargs)
        self.real_data = real_data
        self.imag_data = imag_data
        self.is_complex_flag = is_complex
        self.coordinates = coordinates
        self.fail_cleanup = fail_cleanup

    def run(self) -> None:
        self._guard("run", ())

    def isComplex(self) -> bool:
        self._guard("isComplex", ())
        return self.is_complex_flag

    def getData(self) -> Any:
        self._guard("getData", ())
        return self.real_data

    def getReal(self) -> Any:
        self._guard("getReal", ())
        return self.real_data

    def getImag(self) -> Any:
        self._guard("getImag", ())
        return self.imag_data

    def getImagData(self) -> Any:
        self._guard("getImagData", ())
        return self.imag_data

    def getCoordinates(self) -> Any:
        self._guard("getCoordinates", ())
        return self.coordinates

    def setInterpolationCoordinates(self, coords: Any) -> None:
        self._guard("setInterpolationCoordinates", (coords,))
        self.props["coord"] = coords


class FTableFeature(FNode):
    def __init__(self, tag: str, type_id: str = "Table", **kwargs: Any) -> None:
        super().__init__(tag=tag, type_id=type_id, **kwargs)
        self.table_data: list[list[float]] = []
        self.headers: list[str] = []

    def getColumnHeaders(self) -> list[str]:
        self._guard("getColumnHeaders", ())
        return self.headers

    def getTableData(self) -> list[list[float]]:
        self._guard("getTableData", ())
        return self.table_data

    def setTableData(self, data: list[list[float]]) -> None:
        self._guard("setTableData", (data,))
        self.table_data = list(data)

    def clearTableData(self) -> None:
        self._guard("clearTableData", ())
        self.table_data.clear()


class FWiredTree:
    """Configurable fake model environment for W17 acceptance verification."""

    def __init__(self, *, sdim: int = 3, is_axisymmetric: bool = False,
                 numerical_real: Any = 42.0, numerical_imag: Any = None,
                 is_complex: bool = False, fail_numerical_remove: bool = False) -> None:
        self.fail_numerical_remove = fail_numerical_remove
        
        # Geometry
        self.geom = FNode(tag="geom1", type_id="GeomSequence",
                          props={"lengthUnit": "m", "getSDim": sdim, "axisymmetric": is_axisymmetric})
        self.geom.getSDim = lambda *args: self.geom._guard("getSDim", args) or sdim
        self.geom.lengthUnit = lambda *args: self.geom._guard("lengthUnit", args) or "m"
        self.geom.isAxisymmetric = lambda *args: self.geom._guard("isAxisymmetric", args) or is_axisymmetric

        # Component
        self.component = FNode(tag="comp1", type_id="Component",
                               collections={"geom": FList(node_type="GeomSequence")})
        self.component.collections["geom"].items["geom1"] = self.geom
        self.component.geom = lambda *args: self.component._collection("geom", args)

        # Solver and Study
        self.solver = FNode(tag="sol1", type_id="SolverSequence",
                            props={"study": "std1", "getPVals": [0.0, 0.1, 0.2],
                                   "getParamNames": ["p1", "p2"], "getParamVals": [[1.0, 2.0], [10.0, 20.0]]})
        self.solver.getPVals = lambda *args: self.solver._guard("getPVals", args) or self.solver.props["getPVals"]
        self.solver.getParamNames = lambda *args: self.solver._guard("getParamNames", args) or self.solver.props["getParamNames"]
        self.solver.getParamVals = lambda *args: self.solver._guard("getParamVals", args) or self.solver.props["getParamVals"]

        # SolutionInfo route (§4/F04): dataset.solution_indices reads the real
        # outer/inner axes through SolverSequence.getSolutioninfo(), so the fake
        # exposes the same four accessors the live worker now allow-lists.
        self.solution_info = FNode(tag="solutioninfo", type_id="SolutionInfo",
                                   props={"getOuterSolnum": [1], "getMaxInner": 3,
                                          "getLevelNames": ["outer", "inner"]})
        self.solution_info.getOuterSolnum = lambda *args: self.solution_info._guard("getOuterSolnum", args) or self.solution_info.props["getOuterSolnum"]
        self.solution_info.getMaxInner = lambda *args: self.solution_info._guard("getMaxInner", args) or self.solution_info.props["getMaxInner"]
        self.solution_info.getLevelNames = lambda *args: self.solution_info._guard("getLevelNames", args) or self.solution_info.props["getLevelNames"]
        self.solver.getSolutioninfo = lambda *args: self.solver._guard("getSolutioninfo", args) or self.solution_info

        self.study_step = FNode(tag="time", type_id="Transient", props={"tlist": "range(0,0.1,0.2)", "tunit": "s"})
        self.study_features = FList(node_type="StudyFeature")
        self.study_features.items["time"] = self.study_step

        self.study = FNode(tag="std1", type_id="Study", collections={"feature": self.study_features})
        self.study.feature = lambda *args: self.study._collection("feature", args)

        # Datasets
        self.dataset_list = FList(node_type="Dataset")
        self.dset1 = FNode(tag="dset1", type_id="Solution",
                           props={"solution": "sol1", "data": "sol1", "comp": "comp1", "geom": "geom1"})
        self.dataset_list.items["dset1"] = self.dset1

        # Numerical Features
        def numerical_factory(tag: str, *args: Any) -> Any:
            feat_type = str(args[0]) if args else "EvalGlobal"
            return FNumericalFeature(
                tag=tag, type_id=feat_type,
                real_data=numerical_real, imag_data=numerical_imag,
                is_complex=is_complex, coordinates=[[0.0, 0.01], [0.0, 0.01], [0.0, 0.01]],
                fail_cleanup=self.fail_numerical_remove,
            )

        self.numerical_list = FList(factory=numerical_factory, node_type="Numerical")
        
        # Override remove on numerical_list to test cleanup failure
        orig_num_remove = self.numerical_list.remove
        def num_remove(tag: str) -> None:
            if self.fail_numerical_remove:
                raise FakeEngineError("Failed to remove numerical feature from server", code="CLEANUP_FAILED")
            orig_num_remove(tag)
        self.numerical_list.remove = num_remove

        # Tables
        self.table_list = FList(factory=lambda tag, *args: FTableFeature(tag=tag), node_type="Table")

        # Results Root
        self.results = FNode(
            tag="result", type_id="Results",
            collections={
                "dataset": self.dataset_list,
                "numerical": self.numerical_list,
                "table": self.table_list,
            }
        )
        self.results.dataset = lambda *args: self.results._collection("dataset", args)
        self.results.numerical = lambda *args: self.results._collection("numerical", args)
        self.results.table = lambda *args: self.results._collection("table", args)

        # Model Root
        self.model = FNode(
            tag="Model", type_id="ModelNode",
            collections={
                "result": self.results,
                "component": FList(node_type="Component"),
                "sol": FList(node_type="SolverSequence"),
                "study": FList(node_type="Study"),
            }
        )
        self.model.result = lambda *args: self.results
        self.model.collections["component"].items["comp1"] = self.component
        self.model.component = lambda *args: self.model._collection("component", args)
        self.model.collections["sol"].items["sol1"] = self.solver
        self.model.sol = lambda *args: self.model._collection("sol", args)
        self.model.collections["study"].items["std1"] = self.study
        self.model.study = lambda *args: self.model._collection("study", args)

    @property
    def worker(self) -> Any:
        class FClient:
            def __init__(self, m: FNode) -> None:
                self.m = m
            def model(self, tag: str) -> FNode:
                return self.m

        class FWorker:
            def __init__(self, m: FNode) -> None:
                self._client = FClient(m)
            def client(self) -> FClient:
                return self._client

        return FWorker(self.model)


# ---------------------------------------------------------------------------
# Test Cases
# ---------------------------------------------------------------------------

def test_w17_operations_catalog_effects_and_dispatch() -> None:
    """Check that all 12 W17 operations are registered with the catalog effects."""
    expected_effects = {
        "dataset.list": "READ",
        "dataset.create": "WRITE",
        "dataset.inspect": "READ",
        "dataset.update": "WRITE",
        "dataset.remove": "WRITE",
        "dataset.solution_indices": "READ",
        "result.evaluate": "EVALUATE",
        "result.at_points": "EVALUATE",
        "result.sample_path": "EVALUATE",
        "result.numerical_manage": "DYNAMIC",
        "result.table_manage": "DYNAMIC",
        "result.field_export": "FILE_WRITE",
    }
    for op_id, effect in expected_effects.items():
        assert op_id in results.OPERATIONS
        assert op_id in IMPLEMENTED_OPERATIONS
        assert OPERATION_ORIGINS[op_id] == "_g3_results"
        assert EFFECTS[op_id] == effect
        assert DISPATCH[op_id] is results.OPERATIONS[op_id]


def test_dataset_crud_lifecycle() -> None:
    """Test full CRUD lifecycle on dataset nodes."""
    tree = FWiredTree()
    worker = tree.worker

    # 1. list
    data = dispatch("dataset.list", worker, "Model", {})
    assert data["count"] == 1
    assert "dset1" in data["tags"]
    assert data["datasets"][0]["type_id"] == "Solution"
    assert data["datasets"][0]["solution"] == "sol1"

    # 2. create CutPoint3D
    create_res = dispatch("dataset.create", worker, "Model", {
        "tag": "cpt1",
        "type_id": "CutPoint3D",
        "definition": {"data": "dset1", "pointx": 0.005, "pointy": 0.005, "pointz": 0.0025},
    })
    assert create_res["created"] is True
    assert create_res["tag"] == "cpt1"
    assert "cpt1" in tree.dataset_list.tags()

    # 3. create with duplicate tag -> TAG_CONFLICT
    with pytest.raises(ExecutionContractError) as exc_info:
        dispatch("dataset.create", worker, "Model", {
            "tag": "cpt1",
            "type_id": "CutPoint3D",
            "definition": {},
        })
    assert exc_info.value.code == "TAG_CONFLICT"

    # 4. create with unsupported type -> API_UNSUPPORTED
    with pytest.raises(ExecutionContractError) as exc_info:
        dispatch("dataset.create", worker, "Model", {
            "tag": "invalid1",
            "type_id": "NonExistentDatasetType",
            "definition": {},
        })
    assert exc_info.value.code == "API_UNSUPPORTED"

    # 5. inspect
    inspect_res = dispatch("dataset.inspect", worker, "Model", {
        "path": {"segments": [{"accessor": "result"}, {"collection": "dataset", "tag": "cpt1"}]},
    })
    assert inspect_res["tag"] == "cpt1"
    assert inspect_res["type_id"] == "CutPoint3D"
    assert inspect_res["properties"]["pointx"] == 0.005

    # 6. inspect non-existent dataset -> NODE_NOT_FOUND
    with pytest.raises(ExecutionContractError) as exc_info:
        dispatch("dataset.inspect", worker, "Model", {"path": "nonexistent_dset"})
    assert exc_info.value.code == "NODE_NOT_FOUND"

    # 7. update
    update_res = dispatch("dataset.update", worker, "Model", {
        "path": "cpt1",
        "definition": {"pointx": 0.008},
    })
    assert "set(pointx)" in update_res["applied"]
    assert update_res["readback"]["pointx"] == 0.008

    # 8. remove
    remove_res = dispatch("dataset.remove", worker, "Model", {"path": "cpt1"})
    assert remove_res["removed"] is True
    assert remove_res["verified_removed"] is True
    assert "cpt1" not in tree.dataset_list.tags()

    # 9. remove non-existent dataset -> NODE_NOT_FOUND
    with pytest.raises(ExecutionContractError) as exc_info:
        dispatch("dataset.remove", worker, "Model", {"path": "cpt1"})
    assert exc_info.value.code == "NODE_NOT_FOUND"


def test_dataset_solution_indices() -> None:
    """Test reading inner/outer parameter indices and time values."""
    tree = FWiredTree()
    worker = tree.worker

    data = dispatch("dataset.solution_indices", worker, "Model", {"path": "dset1"})
    assert data["binding_complete"] is True
    assert data["solution"] == "sol1"
    assert data["time_values"] == [0.0, 0.1, 0.2]
    assert data["inner_indices"] == [1, 2, 3]
    assert "p1" in data["parameters"]

    # Unbound dataset
    unbound_dset = FNode(tag="dset_unbound", type_id="Solution", props={})
    tree.dataset_list.items["dset_unbound"] = unbound_dset
    unbound_res = dispatch("dataset.solution_indices", worker, "Model", {"path": "dset_unbound"})
    assert unbound_res["binding_complete"] is True or unbound_res["solution"] == "sol1"


def test_solution_indices_never_invents_an_axis_when_the_engine_refuses_it() -> None:
    """§4/F04: a refused SolutionInfo read must not become ``outer_indices=[1]``.

    The pre-fix code returned ``binding_complete: True`` with a hardcoded
    ``outer_indices = [1]`` while both SolutionInfo calls were refused by the
    worker allow-list and the exception was swallowed, so the response claimed
    metadata it had never read.
    """
    tree = FWiredTree()
    tree.solver.unavailable.add("getSolutioninfo")

    data = dispatch("dataset.solution_indices", tree.worker, "Model", {"path": "dset1"})

    assert data["outer_indices"] == []
    assert data["inner_indices"] == []
    assert data["axis_metadata_complete"] is False
    refused = {entry["method"]: entry for entry in data["read_errors"]}
    assert "getSolutioninfo" in refused
    # The refusal is reported as an allow-list gap, never converted into a value.
    assert refused["getSolutioninfo"]["allowlist_entry_required"] == "getSolutioninfo"
    assert data["parameters_complete"] is False


def test_solution_indices_reads_the_outer_axis_from_the_engine() -> None:
    """The outer axis is engine-driven: two stored outer solutions appear as [1, 2]."""
    tree = FWiredTree()
    tree.solution_info.props["getOuterSolnum"] = [1, 2]
    tree.solution_info.props["getMaxInner"] = 2

    data = dispatch("dataset.solution_indices", tree.worker, "Model", {"path": "dset1"})

    assert data["outer_indices"] == [1, 2]
    assert data["inner_indices"] == [1, 2]
    assert data["axis_metadata_complete"] is True
    assert data["level_names"] == ["outer", "inner"]
    assert data["read_errors"] == []


def test_result_evaluate_complex_field_modes() -> None:
    """T014: Complex field evaluation with preserve, real, imag, abs, phase."""
    # z = 3.0 + 4.0i: abs = 5.0, phase = atan2(4, 3) ~ 0.927295 rad
    real_field = [[3.0, 6.0], [9.0, 12.0]]
    imag_field = [[4.0, 8.0], [12.0, 16.0]]
    tree = FWiredTree(numerical_real=real_field, numerical_imag=imag_field, is_complex=True)
    worker = tree.worker

    base_spec = {
        "expressions": ["Ez"],
        "solution": {"dataset": "dset1"},
        "aggregate": "none",
    }

    # 1. preserve mode (default for complex field)
    pres_res = dispatch("result.evaluate", worker, "Model", {
        "spec": {**base_spec, "complex_mode": "preserve"},
    })
    assert pres_res["is_complex"] is True
    assert pres_res["complex_mode"] == "preserve"
    assert pres_res["values"][0][0] == {"real": 3.0, "imag": 4.0}
    assert pres_res["values"][0][1] == {"real": 6.0, "imag": 8.0}

    # 2. real mode
    real_res = dispatch("result.evaluate", worker, "Model", {
        "spec": {**base_spec, "complex_mode": "real"},
    })
    assert real_res["values"][0][0] == 3.0

    # 3. imag mode
    imag_res = dispatch("result.evaluate", worker, "Model", {
        "spec": {**base_spec, "complex_mode": "imag"},
    })
    assert imag_res["values"][0][0] == 4.0

    # 4. abs mode
    abs_res = dispatch("result.evaluate", worker, "Model", {
        "spec": {**base_spec, "complex_mode": "abs"},
    })
    assert abs_res["values"][0][0] == 5.0
    assert abs_res["values"][0][1] == 10.0

    # 5. phase mode
    phase_res = dispatch("result.evaluate", worker, "Model", {
        "spec": {**base_spec, "complex_mode": "phase"},
    })
    expected_phase = math.atan2(4.0, 3.0)
    assert math.isclose(phase_res["values"][0][0], expected_phase, rel_tol=1e-7)

    # 6. Mathematical Consistency Verification
    r00 = real_res["values"][0][0]
    i00 = imag_res["values"][0][0]
    a00 = abs_res["values"][0][0]
    p00 = phase_res["values"][0][0]
    # abs^2 == real^2 + imag^2
    assert math.isclose(a00 ** 2, r00 ** 2 + i00 ** 2, rel_tol=1e-7)
    # real == abs * cos(phase)
    assert math.isclose(r00, a00 * math.cos(p00), rel_tol=1e-7)
    # imag == abs * sin(phase)
    assert math.isclose(i00, a00 * math.sin(p00), rel_tol=1e-7)


def test_result_evaluate_measures_and_axisymmetric() -> None:
    """T013: 1D/2D/3D/axisymmetric measures, average denominator and exactly once 2*pi*r."""
    # 1. 3D Standard Volume Integral
    tree_3d = FWiredTree(sdim=3, is_axisymmetric=False, numerical_real=150.0)
    res_3d = dispatch("result.evaluate", tree_3d.worker, "Model", {
        "spec": {
            "expressions": ["T"],
            "solution": {"dataset": "dset1"},
            "aggregate": "integral",
            "complex_mode": "real",
        }
    })
    assert res_3d["axisymmetric"] is False
    assert res_3d["axisymmetric_factor_applied"] is False
    assert res_3d["values"] == 150.0

    # 2. Axisymmetric Volume Integral (2*pi*r factor applied exactly once)
    tree_axi = FWiredTree(sdim=2, is_axisymmetric=True, numerical_real=300.0)
    res_axi = dispatch("result.evaluate", tree_axi.worker, "Model", {
        "spec": {
            "expressions": ["T"],
            "solution": {"dataset": "dset1"},
            "aggregate": "integral",
            "complex_mode": "real",
        }
    })
    assert res_axi["axisymmetric"] is True
    assert res_axi["axisymmetric_factor_applied"] is True
    assert res_axi["axisymmetric_applied_count"] == 1

    # 3. Average with Denominator Measure
    res_avg = dispatch("result.evaluate", tree_3d.worker, "Model", {
        "spec": {
            "expressions": ["T"],
            "solution": {"dataset": "dset1"},
            "aggregate": "average",
            "complex_mode": "real",
        }
    })
    assert res_avg["denominator_measure"] is not None
    assert res_avg["aggregate"] == "average"

    # 4. Extrema (min / max)
    res_max = dispatch("result.evaluate", tree_3d.worker, "Model", {
        "spec": {
            "expressions": ["T"],
            "solution": {"dataset": "dset1"},
            "aggregate": "maximum",
            "complex_mode": "real",
        }
    })
    assert res_max["cleanup"]["type_id"] == "MaxVolume"


def test_pinned_selection_without_entities_is_refused() -> None:
    """C05/§3: a selection that matches no entity must not come back as a vacuous 0.

    COMSOL answers an out-of-range entity index with a healthy status and an empty
    aggregate, so an integral over domain 99 of a two-interval geometry used to arrive as
    ``0.0`` with ``ok: true`` -- a number that looks like a measurement but is not.  The
    adapter now reads the measure of the pinned selection and fails the call; the same
    read is recorded when the selection is real, so the receipt carries the measure.
    """
    tree = FWiredTree(sdim=1, is_axisymmetric=False, numerical_real=150.0)

    def factory(tag: str, *args: Any) -> Any:
        empty_guard = tag.endswith("_selmeasure")
        return FNumericalFeature(
            tag=tag,
            type_id=str(args[0]) if args else "EvalGlobal",
            real_data=0.0 if empty_guard else 150.0,
        )

    tree.numerical_list.factory = factory
    spec = {
        "expressions": ["1"],
        "solution": {"dataset": "dset1"},
        "aggregate": "integral",
        "complex_mode": "real",
        "selection": [99],
    }
    with pytest.raises(ExecutionContractError) as info:
        dispatch("result.evaluate", tree.worker, "Model", {"spec": dict(spec)})
    assert info.value.code == "SELECTION_MATCHED_NO_ENTITIES"
    assert info.value.details["selection"] == [99]
    assert info.value.details["aggregate"] == "integral"

    # A real selection is not refused, and its engine measure is part of the receipt.
    tree_ok = FWiredTree(sdim=1, is_axisymmetric=False, numerical_real=150.0)
    res = dispatch("result.evaluate", tree_ok.worker, "Model", {
        "spec": {
            "expressions": ["1"],
            "solution": {"dataset": "dset1"},
            "aggregate": "integral",
            "complex_mode": "real",
            "selection": [1],
        }
    })
    assert res["selection_measure"] == 150.0
    assert res["selection_measure_source"].startswith("engine integral of 1 over the pinned selection")


def test_result_evaluate_solution_spec_indices() -> None:
    """T021: SolutionSpec inner/outer index selection and error bounds checking."""
    # Multi-solution array: 3 solutions
    multi_sol_data = [10.0, 20.0, 30.0]
    tree = FWiredTree(numerical_real=multi_sol_data)
    worker = tree.worker

    base_spec = {
        "expressions": ["T"],
        "aggregate": "none",
        "complex_mode": "real",
    }

    # 1. inner = "first"
    res_first = dispatch("result.evaluate", worker, "Model", {
        "spec": {**base_spec, "solution": {"dataset": "dset1", "inner": "first"}},
    })
    assert res_first["values"] == 10.0

    # 2. inner = "last"
    res_last = dispatch("result.evaluate", worker, "Model", {
        "spec": {**base_spec, "solution": {"dataset": "dset1", "inner": "last"}},
    })
    assert res_last["values"] == 30.0

    # 3. inner = 2 (explicit 1-based index)
    res_idx2 = dispatch("result.evaluate", worker, "Model", {
        "spec": {**base_spec, "solution": {"dataset": "dset1", "inner": 2}},
    })
    assert res_idx2["values"] == 20.0

    # 4. inner = [1, 3] (subset selection)
    res_sub = dispatch("result.evaluate", worker, "Model", {
        "spec": {**base_spec, "solution": {"dataset": "dset1", "inner": [1, 3]}},
    })
    assert res_sub["values"] == [10.0, 30.0]

    # 5. Out of bounds index (inner = 0 or 99) -> INVALID_REQUEST
    with pytest.raises(ExecutionContractError) as exc_info:
        dispatch("result.evaluate", worker, "Model", {
            "spec": {**base_spec, "solution": {"dataset": "dset1", "inner": 0}},
        })
    assert exc_info.value.code == "INVALID_REQUEST"

    with pytest.raises(ExecutionContractError) as exc_info:
        dispatch("result.evaluate", worker, "Model", {
            "spec": {**base_spec, "solution": {"dataset": "dset1", "inner": 99}},
        })
    assert exc_info.value.code == "INVALID_REQUEST"


def test_result_at_points_and_coordinate_readback() -> None:
    """Point sampling with coordinate readback and verification status."""
    tree = FWiredTree(numerical_real=[300.0, 350.0])
    worker = tree.worker

    pts = [[0.0, 0.0, 0.0], [0.01, 0.01, 0.01]]
    res = dispatch("result.at_points", worker, "Model", {
        "spec": {
            "expressions": ["T"],
            "solution": {"dataset": "dset1"},
            "complex_mode": "real",
        },
        "points": pts,
        "coordinate_unit": "m",
        "frame": "spatial",
    })
    assert res["point_count"] == 2
    assert res["coordinate_readback"]["status"] == "VERIFIED"
    assert res["cleanup"]["type_id"] == "Interp"
    assert res["cleanup"]["removed"] is True


def test_result_numerical_and_table_management(tmp_path: Path) -> None:
    """Manage Numerical/Probe features and Table features with cleanup escalation."""
    tree = FWiredTree()
    worker = tree.worker

    # 1. Numerical manage: create, list, run, remove
    num_create = dispatch("result.numerical_manage", worker, "Model", {
        "action": "create",
        "path": "num1",
        "definition": {"type_id": "EvalGlobal", "expr": ["T"]},
    })
    assert num_create["created"] is True
    assert "num1" in tree.numerical_list.tags()

    num_list = dispatch("result.numerical_manage", worker, "Model", {"action": "list"})
    assert num_list["count"] == 1
    assert num_list["features"][0]["tag"] == "num1"

    num_rem = dispatch("result.numerical_manage", worker, "Model", {
        "action": "remove",
        "path": "num1",
    })
    assert num_rem["removed"] is True

    # 2. Table manage: create, set, get, clear, remove
    tbl_create = dispatch("result.table_manage", worker, "Model", {
        "action": "create",
        "path": "tbl1",
        "definition": {"type_id": "Table"},
    })
    assert tbl_create["created"] is True
    assert "tbl1" in tree.table_list.tags()

    dispatch("result.table_manage", worker, "Model", {
        "action": "set",
        "path": "tbl1",
        "definition": {"data": [[1.0, 2.0], [3.0, 4.0]]},
    })
    tbl_get = dispatch("result.table_manage", worker, "Model", {
        "action": "get",
        "path": "tbl1",
    })
    assert tbl_get["data"] == [[1.0, 2.0], [3.0, 4.0]]

    dispatch("result.table_manage", worker, "Model", {"action": "clear", "path": "tbl1"})
    tbl_cleared = dispatch("result.table_manage", worker, "Model", {"action": "get", "path": "tbl1"})
    assert tbl_cleared["data"] == []

    dispatch("result.table_manage", worker, "Model", {"action": "remove", "path": "tbl1"})
    assert "tbl1" not in tree.table_list.tags()


def test_ephemeral_cleanup_failure_escalates_to_unknown() -> None:
    """T033 / F05: Ephemeral node cleanup failure escalates status to UNKNOWN."""
    tree = FWiredTree(fail_numerical_remove=True)
    worker = tree.worker

    eval_res = dispatch("result.evaluate", worker, "Model", {
        "spec": {
            "expressions": ["T"],
            "solution": {"dataset": "dset1"},
            "aggregate": "global",
            "complex_mode": "real",
        }
    })
    assert eval_res["cleanup"]["cleanup_failed"] is True
    assert eval_res["status"]["cleanup_failed"] is True
    assert eval_res["status"]["execution_state_unknown"] is True
    assert eval_res["status"]["ok"] is False


def test_field_export_large_data_and_chunk_verification(tmp_path: Path) -> None:
    """T049: Export large array, compute SHA256, verify chunk pagination byte-for-byte."""
    large_data = [float(i) * 1.5 for i in range(1500)]
    tree = FWiredTree(numerical_real=large_data)
    worker = tree.worker

    dest_file = tmp_path / "exported_field.json"
    export_res = dispatch("result.field_export", worker, "Model", {
        "spec": {
            "expressions": ["T"],
            "solution": {"dataset": "dset1"},
            "aggregate": "none",
            "complex_mode": "real",
        },
        "format": "json",
        "destination": str(dest_file),
    })

    assert dest_file.is_file()
    assert export_res["total_elements"] == 1500
    assert export_res["sha256"] == hashlib.sha256(dest_file.read_bytes()).hexdigest()

    # Verify chunk pagination reproduces full file byte-for-byte with identical SHA256
    valid, actual_hash = results.verify_artifact_chunks(str(dest_file), chunk_size=512)
    assert valid is True
    assert actual_hash == export_res["sha256"]

    # Verify JSON content sort_keys
    content = json.loads(dest_file.read_text(encoding="utf-8"))
    assert content["values"] == large_data
    assert content["metadata"]["dataset"] == "dset1"


# ---------------------------------------------------------------------------
# §3 measure and §4 selection contracts (G3.3 remediation)
# ---------------------------------------------------------------------------


def test_the_measure_is_engine_read_and_never_substituted() -> None:
    """§3: a failed ``M = ∫w dμ`` read must fail the operation.

    The pre-fix code fell back to ``spec.denominator_measure`` and then to a
    hardcoded ``1.0``, so an average over a domain whose measure could not be
    read was still published as a successful number.
    """
    tree = FWiredTree(numerical_real=150.0)
    original = tree.numerical_list.factory

    def refusing_factory(tag: str, *args: Any) -> Any:
        if tag.endswith("_meas"):
            raise FakeEngineError("measure feature could not be created", code="ENGINE_CALL_FAILED")
        return original(tag, *args)

    tree.numerical_list.factory = refusing_factory

    with pytest.raises(ExecutionContractError) as exc_info:
        dispatch("result.evaluate", tree.worker, "Model", {
            "spec": {"expressions": ["T"], "solution": {"dataset": "dset1"},
                     "aggregate": "average", "complex_mode": "real"},
        })
    assert exc_info.value.code == "ZERO_OR_INVALID_MEASURE"
    assert "denominator" in str(exc_info.value)


def test_a_caller_supplied_denominator_is_refused() -> None:
    """§3: the denominator comes from the engine, so ``denominator_measure`` is refused."""
    tree = FWiredTree()
    with pytest.raises(ExecutionContractError) as exc_info:
        dispatch("result.evaluate", tree.worker, "Model", {
            "spec": {"expressions": ["T"], "solution": {"dataset": "dset1"},
                     "aggregate": "average", "denominator_measure": 1.0},
        })
    assert exc_info.value.code == "API_UNSUPPORTED"
    assert "denominator_measure" in str(exc_info.value)


def test_unimplemented_solution_selections_are_refused_not_ignored() -> None:
    """§4: unsupported per-solution selection keys raise instead of being ignored."""
    tree = FWiredTree()
    for key in ("outer", "time", "frequency", "parameters"):
        with pytest.raises(ExecutionContractError) as exc_info:
            dispatch("result.evaluate", tree.worker, "Model", {
                "spec": {"expressions": ["T"], "solution": {"dataset": "dset1", key: 1}},
            })
        assert exc_info.value.code == "API_UNSUPPORTED", key
        assert key in str(exc_info.value)


def test_std_and_rms_fail_instead_of_degrading_to_zero_or_abs_mean() -> None:
    """§3: no silent ``std = 0.0`` / ``rms = |mean|`` when the integral cannot be read."""
    for aggregate in ("std", "rms"):
        tree = FWiredTree(numerical_real=150.0)
        original = tree.numerical_list.factory

        class _RefusingSecondRead(FNumericalFeature):
            def _refuse(self) -> None:
                expr = str(self.props.get("expr"))
                if "^2" in expr or "- (" in expr:
                    raise FakeEngineError("variance/square integral failed", code="ENGINE_CALL_FAILED")

            def getData(self) -> Any:
                self._refuse()
                return super().getData()

            def getReal(self) -> Any:
                self._refuse()
                return super().getReal()

        def factory(tag: str, *args: Any, _original: Any = original) -> Any:
            feat = _original(tag, *args)
            if tag.endswith("_meas"):
                feat.__class__ = _RefusingSecondRead
            return feat

        tree.numerical_list.factory = factory

        with pytest.raises(ExecutionContractError) as exc_info:
            dispatch("result.evaluate", tree.worker, "Model", {
                "spec": {"expressions": ["T"], "solution": {"dataset": "dset1"},
                         "aggregate": aggregate, "complex_mode": "real"},
            })
        assert exc_info.value.code in {"ENGINE_CALL_FAILED", "INVALID_RESULT"}, (aggregate, exc_info.value)


def test_weighted_average_uses_the_weighted_measure_and_numerator() -> None:
    """§3: ``M = ∫w dμ`` and ``average = ∫w·f dμ / M`` are both read from the engine."""
    tree = FWiredTree(numerical_real=150.0)
    created: list[tuple[str, Any]] = []
    original = tree.numerical_list.factory

    def factory(tag: str, *args: Any) -> Any:
        feat = original(tag, *args)
        created.append((tag, feat))
        return feat

    tree.numerical_list.factory = factory

    data = dispatch("result.evaluate", tree.worker, "Model", {
        "spec": {"expressions": ["T"], "solution": {"dataset": "dset1"},
                 "aggregate": "average", "complex_mode": "real",
                 "weight_expression": "2"},
    })

    measure = next(feat for tag, feat in created if tag.endswith("_meas"))
    numerator = next(feat for tag, feat in created if tag.endswith("_num"))
    # The measure integrates w (not 1) and the numerator integrates w*f.
    assert measure.props["expr"] == ["2"]
    assert numerator.props["expr"] == ["(2)*(T)"]
    # The fake returns 150.0 for both integrals, so ∫w·f/∫w = 1.0.
    assert math.isclose(data["values"], 1.0, rel_tol=1e-12)
    assert data["denominator_measure"] == 150.0
    assert "w='2'" in (data["denominator_source"] or "")


def test_weighted_average_differs_from_the_unweighted_one() -> None:
    """The weight changes the result, so a silent no-op would be visible."""
    unweighted = FWiredTree(numerical_real=150.0)
    unweighted_avg = dispatch("result.evaluate", unweighted.worker, "Model", {
        "spec": {"expressions": ["T"], "solution": {"dataset": "dset1"},
                 "aggregate": "average", "complex_mode": "real"},
    })
    weighted = FWiredTree(numerical_real=150.0)
    weighted_avg = dispatch("result.evaluate", weighted.worker, "Model", {
        "spec": {"expressions": ["T"], "solution": {"dataset": "dset1"},
                 "aggregate": "average", "complex_mode": "real",
                 "weight_expression": "2"},
    })
    # Unweighted: the Av* feature already returns the mean (150.0).  Weighted:
    # ∫w·f/∫w = 1.0 with this fixture, so the two paths are distinguishable.
    assert unweighted_avg["values"] == 150.0
    assert weighted_avg["values"] == 1.0
    assert unweighted_avg["denominator_source"] == "engine integral of 1 over the selection"

