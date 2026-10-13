"""Focused typed CRUD checks for W17 Dataset/Numerical/Table adapters."""
from __future__ import annotations

from typing import Any

import pytest

from comsol_mcp._domain_outcome import classify
from comsol_mcp._execution_contract import ExecutionContractError, PreWriteRefusal
from comsol_mcp._g3_results import (
    dataset_create,
    dataset_inspect,
    dataset_solution_indices,
    dataset_update,
    result_evaluate,
    result_numerical_manage,
    result_table_manage,
    sample_path,
)

from tests.test_g3_w17 import FNode, FNumericalFeature, FTableFeature, FWiredTree, FakeEngineError


def _path(collection: str, tag: str) -> dict[str, list[dict[str, str]]]:
    return {"segments": [{"accessor": "result"}, {"collection": collection, "tag": tag}]}


def test_dataset_definition_is_prevalidated_and_typed_path_rejects_geometry() -> None:
    tree = FWiredTree()
    with pytest.raises(ExecutionContractError) as unknown:
        dataset_create(tree.worker, "Model", {
            "tag": "bad1", "type_id": "CutPoint3D", "definition": {"typo_property": 1},
        })
    assert unknown.value.code == "INVALID_REQUEST"
    assert isinstance(unknown.value, PreWriteRefusal)
    assert unknown.value.stage == "validation"
    assert "bad1" not in tree.dataset_list.tags()

    with pytest.raises(ExecutionContractError) as geometry:
        dataset_update(tree.worker, "Model", {
            "path": {"segments": [{"accessor": "result"}, {"collection": "geometry", "tag": "geom1"}]},
            "definition": {"pointx": 1.0},
        })
    assert geometry.value.code == "INVALID_NODE_PATH"
    assert isinstance(geometry.value, PreWriteRefusal)
    assert geometry.value.stage == "validation"


def test_dataset_binding_refs_and_cycles_refuse_before_mutation() -> None:
    tree = FWiredTree()

    with pytest.raises(PreWriteRefusal) as bad_component:
        dataset_create(tree.worker, "Model", {
            "tag": "bad_component", "type_id": "CutPoint3D",
            "definition": {"data": "dset1", "comp": "missing_comp",
                            "pointx": 0.01, "pointy": 0.01, "pointz": 0.01},
        })
    assert bad_component.value.code == "NODE_NOT_FOUND"
    assert bad_component.value.stage == "validation"
    assert "bad_component" not in tree.dataset_list.tags()

    for tag in ("cycle_a", "cycle_b"):
        created = dataset_create(tree.worker, "Model", {
            "tag": tag, "type_id": "Join", "definition": {"data": "dset1", "data2": "dset1"},
        })
        assert created["created"] is True
    updated = dataset_update(tree.worker, "Model", {
        "path": _path("dataset", "cycle_a"), "definition": {"data": "cycle_b"},
    })
    assert updated["updated"] is True
    cycle_node = tree.dataset_list.items["cycle_b"]
    set_count = len([name for name, _args in cycle_node.calls if name == "set"])
    with pytest.raises(PreWriteRefusal) as cycle:
        dataset_update(tree.worker, "Model", {
            "path": _path("dataset", "cycle_b"), "definition": {"data": "cycle_a"},
        })
    assert cycle.value.code == "DATASET_CYCLE_DETECTED"
    assert cycle.value.stage == "validation"
    assert len([name for name, _args in cycle_node.calls if name == "set"]) == set_count


def test_cutplane_rejects_component_geometry_setters_before_create() -> None:
    tree = FWiredTree()
    with pytest.raises(PreWriteRefusal) as refused:
        dataset_create(tree.worker, "Model", {
            "tag": "bad_cutplane", "type_id": "CutPlane",
            "definition": {"data": "dset1", "comp": "comp1", "geom": "geom1",
                            "planetype": "quick", "quickplane": "xy"},
        })
    assert refused.value.code == "INVALID_REQUEST"
    assert refused.value.stage == "validation"
    assert "bad_cutplane" not in tree.dataset_list.tags()


def test_numerical_update_stops_on_first_engine_set_failure() -> None:
    tree = FWiredTree()
    node_path = _path("numerical", "n1")
    created = result_numerical_manage(tree.worker, "Model", {
        "action": "create", "path": node_path,
        "definition": {"type_id": "EvalGlobal", "expr": ["T"]},
    })
    assert created["status"] == "APPLIED"
    node = tree.numerical_list.items["n1"]
    before_set_calls = len([name for name, _args in node.calls if name == "set"])
    node.unavailable.add("set")
    result = result_numerical_manage(tree.worker, "Model", {
        "action": "update", "path": node_path,
        "definition": {"expr": ["U"], "data": "dset1"},
    })
    assert result["failed_count"] == 1
    assert result["not_executed"] == [{"step": "property", "property": "data"}]
    assert len([name for name, _args in node.calls if name == "set"]) == before_set_calls + 1


def test_table_set_does_not_fallback_after_set_error() -> None:
    tree = FWiredTree()
    table_path = _path("table", "tbl1")
    created = result_table_manage(tree.worker, "Model", {
        "action": "create", "path": table_path, "definition": {},
    })
    assert created["status"] == "APPLIED"
    node = tree.table_list.items["tbl1"]
    node.unavailable.add("setTableData")
    result = result_table_manage(tree.worker, "Model", {
        "action": "set", "path": table_path, "definition": {"data": [[1.0, 2.0]]},
    })
    assert result["failed_count"] == 1
    assert [name for name, _args in node.calls if name == "setTableData"] == ["setTableData"]


def test_table_write_requires_real_readback_and_stops_before_headers() -> None:
    """A silently ignored data setter is a known mismatch, never an applied write."""
    tree = FWiredTree()

    class _IgnoredDataTable(FTableFeature):
        def __init__(self, tag: str, **kwargs: Any) -> None:
            super().__init__(tag, **kwargs)
            # Keep a real native value so 0 vs 1e-13 exercises exact
            # readback rather than only a shape mismatch.
            self.table_data = [[0.0]]

        def setTableData(self, data: list[list[float]], *imaginary: Any) -> None:
            self._guard("setTableData", (data,) + imaginary)

        def setColumnHeaders(self, headers: list[str]) -> None:
            self._guard("setColumnHeaders", (headers,))
            self.headers = list(headers)

    tree.table_list.factory = lambda tag, *args: _IgnoredDataTable(tag)
    result = result_table_manage(tree.worker, "Model", {
        "action": "create",
        "path": _path("table", "ignored"),
        "definition": {"data": [[1e-13]], "headers": ["T"]},
    })

    assert result["status"] == "PARTIAL_FAILURE"
    assert result["failed"][0]["error"]["code"] == "READBACK_MISMATCH"
    assert result["not_executed"] == [{"step": "headers"}]
    node = tree.table_list.items["ignored"]
    assert [name for name, _args in node.calls if name == "setColumnHeaders"] == []

    inspected = result_table_manage(tree.worker, "Model", {
        "action": "get", "path": _path("table", "ignored"),
    })
    assert inspected["readback"]["match"] is None


def test_table_imaginary_shape_is_refused_before_table_creation() -> None:
    tree = FWiredTree()
    with pytest.raises(ExecutionContractError) as refused:
        result_table_manage(tree.worker, "Model", {
            "action": "create", "path": _path("table", "bad_shape"),
            "definition": {"data": [[1.0, 2.0]], "imaginary": [[1.0]]},
        })
    assert refused.value.code == "INVALID_REQUEST"
    assert "bad_shape" not in tree.table_list.tags()


def test_table_write_verifies_imaginary_and_header_readback_and_rejects_stale_imaginary() -> None:
    class _ComplexTable(FTableFeature):
        def __init__(self, tag: str, **kwargs: Any) -> None:
            super().__init__(tag, **kwargs)
            self.imaginary: list[list[float]] | None = None

        def getImag(self) -> Any:
            self._guard("getImag", ())
            return self.imaginary

        def isComplex(self) -> bool:
            self._guard("isComplex", ())
            return self.imaginary is not None

        def setTableData(self, data: list[list[float]], *imaginary: Any) -> None:
            self._guard("setTableData", (data,) + imaginary)
            self.table_data = list(data)
            if imaginary:
                self.imaginary = list(imaginary[0])

        def setColumnHeaders(self, headers: list[str]) -> None:
            self._guard("setColumnHeaders", (headers,))
            self.headers = list(headers)

    class _IgnoredHeadersTable(_ComplexTable):
        def setColumnHeaders(self, headers: list[str]) -> None:
            self._guard("setColumnHeaders", (headers,))

    class _StaleImaginaryTable(_ComplexTable):
        def setTableData(self, data: list[list[float]], *imaginary: Any) -> None:
            self._guard("setTableData", (data,) + imaginary)
            self.table_data = list(data)
            if imaginary:
                self.imaginary = list(imaginary[0])
            # A real-only rewrite intentionally leaves the old imaginary
            # matrix in place so the production readback must catch it.

    tree = FWiredTree()
    tree.table_list.factory = lambda tag, *args: _ComplexTable(tag)
    created = result_table_manage(tree.worker, "Model", {
        "action": "create", "path": _path("table", "complex"),
        "definition": {"data": [[1.0, 2.0]], "imaginary": [[3.0, 4.0]]},
    })
    assert created["status"] == "APPLIED"
    data_step = next(step for step in created["applied"] if step.get("step") == "data")
    assert data_step["readback"]["match_details"]["imaginary"] is True

    mismatch_tree = FWiredTree()
    mismatch_tree.table_list.factory = lambda tag, *args: _IgnoredHeadersTable(tag)
    mismatch_tree.table_list.items["complex"] = _IgnoredHeadersTable("complex")
    mismatch_tree.table_list.items["complex"].table_data = [[1.0]]
    header_result = result_table_manage(mismatch_tree.worker, "Model", {
        "action": "set", "path": _path("table", "complex"),
        "definition": {"headers": ["T"]},
    })
    assert header_result["failed"][0]["error"]["code"] == "READBACK_MISMATCH"

    stale_tree = FWiredTree()
    stale_tree.table_list.factory = lambda tag, *args: _StaleImaginaryTable(tag)
    initial = result_table_manage(stale_tree.worker, "Model", {
        "action": "create", "path": _path("table", "complex"),
        "definition": {"data": [[1.0]], "imaginary": [[9.0]]},
    })
    assert initial["status"] == "APPLIED"
    stale = result_table_manage(stale_tree.worker, "Model", {
        "action": "set", "path": _path("table", "complex"),
        "definition": {"data": [[2.0]]},
    })
    assert stale["failed"][0]["error"]["code"] == "READBACK_MISMATCH"


def test_table_successful_mutations_survive_public_outcome_classification() -> None:
    class _HeadersTable(FTableFeature):
        def setColumnHeaders(self, headers: list[str]) -> None:
            self._guard("setColumnHeaders", (headers,))
            self.headers = list(headers)

    tree = FWiredTree()
    tree.table_list.factory = lambda tag, *args: _HeadersTable(tag)
    path = _path("table", "roundtrip")
    for arguments in (
        {"action": "create", "path": path, "definition": {"data": [[1.0]], "headers": ["T"]}},
        {"action": "set", "path": path, "definition": {"data": [[2.0]], "headers": ["U"]}},
    ):
        result = result_table_manage(tree.worker, "Model", arguments)
        outcome = classify("result.table_manage", result, witness={"mutation_issued": True})
        assert result["ok"] is True
        assert result["readback_match"] is True
        assert outcome.success is True, outcome


def test_table_final_readback_catches_late_header_data_corruption_and_empty_identity() -> None:
    class _CorruptingHeadersTable(FTableFeature):
        def setColumnHeaders(self, headers: list[str]) -> None:
            self._guard("setColumnHeaders", (headers,))
            self.headers = list(headers)
            # The header setter silently changes an earlier data value.  The
            # final combined proof must catch this after the per-step header
            # readback has already succeeded.
            self.table_data = [[99.0]]

    tree = FWiredTree()
    tree.table_list.factory = lambda tag, *args: _CorruptingHeadersTable(tag)
    result = result_table_manage(tree.worker, "Model", {
        "action": "create", "path": _path("table", "corrupt"),
        "definition": {"data": [[1.0]], "headers": ["T"]},
    })
    assert result["status"] == "PARTIAL_FAILURE"
    assert result["failed"][-1]["step"] == "final_readback"
    assert result["failed"][-1]["error"]["code"] == "READBACK_MISMATCH"
    assert result["readback"]["match_details"]["data"] is False
    assert classify("result.table_manage", result, witness={"mutation_issued": True}).success is False

    empty = result_table_manage(tree.worker, "Model", {
        "action": "create", "path": _path("table", "empty"), "definition": {},
    })
    assert empty["status"] == "APPLIED"
    assert empty["readback"]["match_details"] == {"tag": True, "type_id": True}


def test_table_get_and_list_publish_unreadable_complex_readback() -> None:
    class _UnreadableImaginaryTable(FTableFeature):
        def getImag(self) -> Any:
            self._guard("getImag", ())
            raise FakeEngineError("imaginary getter refused", code="METHOD_REJECTED")

        def isComplex(self) -> bool:
            self._guard("isComplex", ())
            return True

    tree = FWiredTree()
    tree.table_list.factory = lambda tag, *args: _UnreadableImaginaryTable(tag)
    node = _UnreadableImaginaryTable("bad_complex")
    node.table_data = [[1.0]]
    node.headers = ["T"]
    tree.table_list.items["bad_complex"] = node

    inspected = result_table_manage(tree.worker, "Model", {
        "action": "get", "path": _path("table", "bad_complex"),
    })
    assert inspected["readback"]["readable"] is False
    assert inspected["readback"]["imaginary_readback"]["code"] == "METHOD_REJECTED"
    assert inspected["execution_state_unknown"] is True
    assert inspected["status"] == "FAILED"

    listed = result_table_manage(tree.worker, "Model", {"action": "list"})
    assert listed["readback"]["readable"] is False
    assert listed["readback"]["unreadable"] == ["bad_complex"]
    assert listed["execution_state_unknown"] is True


def test_table_inspect_cannot_infer_real_from_null_imaginary_and_missing_complex_flag() -> None:
    class _UnknownTable(FTableFeature):
        def getImag(self) -> Any:
            return None

        def isComplex(self) -> bool:
            raise FakeEngineError("complex flag unavailable", code="METHOD_REJECTED")

    tree = FWiredTree()
    node = _UnknownTable("unknown")
    node.table_data = [[1.0]]
    tree.table_list.items["unknown"] = node
    result = result_table_manage(tree.worker, "Model", {
        "action": "get", "path": _path("table", "unknown"),
    })
    assert result["readback"]["readable"] is False
    assert result["readback"]["imaginary_readback"]["code"] == "TABLE_COMPLEX_STATUS_UNREADABLE"
    assert classify("result.table_manage", result).success is False


def test_table_real_only_accepts_exact_zero_imaginary_when_complex_flag_is_unavailable() -> None:
    class _ZeroImaginaryNoFlag(FTableFeature):
        def getImag(self) -> Any:
            self._guard("getImag", ())
            return [[0.0]]

        def isComplex(self) -> bool:
            self._guard("isComplex", ())
            raise FakeEngineError("complex flag unavailable", code="METHOD_REJECTED")

    tree = FWiredTree()
    tree.table_list.factory = lambda tag, *args: _ZeroImaginaryNoFlag(tag)
    result = result_table_manage(tree.worker, "Model", {
        "action": "create", "path": _path("table", "real_zero"),
        "definition": {"data": [[2.0]]},
    })
    assert result["status"] == "APPLIED"
    assert result["readback"]["match_details"]["real_only_zero_imaginary"] is True


def test_dataset_inspect_uses_derived_graph_solution_and_exposes_upstream() -> None:
    tree = FWiredTree()
    tree.dataset_list.items["cut1"] = FNode(
        tag="cut1", type_id="CutPoint3D",
        props={"data": "dset1", "pointx": 0.25},
    )
    inspected = dataset_inspect(tree.worker, "Model", {
        "path": _path("dataset", "cut1"),
    })
    assert inspected["solution"] == "sol1"
    assert inspected["solution"] != inspected["properties"]["data"]
    assert inspected["upstream_reference"] == "dset1"
    assert inspected["upstream_references"] == [
        {"property": "data", "from": "cut1", "to": "dset1"}
    ]
    assert inspected["binding_complete"] is True
    assert inspected["property_inventory"]["complete"] is True
    assert inspected["incomplete"] is False


def test_dataset_inspect_reports_property_getter_failure_without_raising() -> None:
    class _GetterFailure(FNode):
        def getString(self, name: str) -> Any:
            self._guard("getString", (name,))
            if name == "pointx":
                raise FakeEngineError("pointx getter refused", code="METHOD_REJECTED")
            return super().getString(name)

    tree = FWiredTree()
    tree.dataset_list.items["cut_failed"] = _GetterFailure(
        tag="cut_failed", type_id="CutPoint3D",
        props={"data": "dset1", "pointx": 0.25},
    )
    inspected = dataset_inspect(tree.worker, "Model", {
        "path": _path("dataset", "cut_failed"),
    })
    assert inspected["status"]["status"] == "APPLIED"
    assert inspected["status"]["execution_state_unknown"] is False
    assert inspected["incomplete"] is True
    assert inspected["properties"]["data"] == "dset1"
    failed = [row for row in inspected["property_readback"] if row["property"] == "pointx"][0]
    assert failed["readable"] is False
    assert any(error["code"] == "METHOD_REJECTED" for error in inspected["read_errors"])


def test_dataset_inspect_keeps_unsolved_dataset_usable_without_inventing_solution() -> None:
    tree = FWiredTree()
    tree.dataset_list.items["unsolved"] = FNode(
        tag="unsolved", type_id="Solution", props={},
    )
    inspected = dataset_inspect(tree.worker, "Model", {
        "path": _path("dataset", "unsolved"),
    })
    assert inspected["solution"] is None
    assert inspected["binding_complete"] is False
    assert inspected["inspection_complete"] is False
    assert inspected["status"]["execution_state_unknown"] is False
    assert any(error["code"] == "SOLUTION_NOT_FOUND" for error in inspected["read_errors"])


def test_dataset_graph_scopes_edges_by_type_and_fails_closed_on_required_reads() -> None:
    tree = FWiredTree()

    class _SolutionWithNoData2(FNode):
        def getString(self, name: str) -> Any:
            self._guard("getString", (name,))
            if name == "data2":
                raise FakeEngineError("data2 is not a Solution property", code="METHOD_REJECTED")
            return self.props.get(name)

    tree.dataset_list.items["sol_no_data2"] = _SolutionWithNoData2(
        tag="sol_no_data2", type_id="Solution", props={"solution": "sol1", "data": "sol1"}
    )
    created = dataset_create(tree.worker, "Model", {
        "tag": "point_from_solution", "type_id": "CutPoint3D",
        "definition": {"data": "dset1", "pointx": 0.0, "pointy": 0.0, "pointz": 0.0},
    })
    assert created["created"] is True
    assert not any(name == "getString" and args == ("data2",)
                   for name, args in tree.dataset_list.items["sol_no_data2"].calls)

    strict_tree = FWiredTree()

    class _JoinWithUnreadableData2(FNode):
        def getString(self, name: str) -> Any:
            self._guard("getString", (name,))
            if name == "data2":
                raise FakeEngineError("data2 getter refused", code="METHOD_REJECTED")
            return self.props.get(name)

    strict_tree.dataset_list.items["join_unreadable"] = _JoinWithUnreadableData2(
        tag="join_unreadable", type_id="Join", props={"data": "dset1", "data2": "dset1"}
    )
    with pytest.raises(PreWriteRefusal) as unreadable:
        dataset_create(strict_tree.worker, "Model", {
            "tag": "will_not_create", "type_id": "CutPoint3D", "definition": {"data": "dset1"},
        })
    assert unreadable.value.code == "DATASET_REFERENCE_UNREADABLE"
    assert "will_not_create" not in strict_tree.dataset_list.tags()

    missing_tree = FWiredTree()
    missing_tree.dataset_list.items["join_missing_data2"] = FNode(
        tag="join_missing_data2", type_id="Join", props={"data": "dset1"}
    )
    with pytest.raises(PreWriteRefusal) as missing:
        dataset_create(missing_tree.worker, "Model", {
            "tag": "will_not_create", "type_id": "CutPoint3D", "definition": {"data": "dset1"},
        })
    assert missing.value.code == "DATASET_REFERENCE_MISSING"


def test_legacy_solution_indices_never_claim_complete_pair_binding() -> None:
    tree = FWiredTree()
    del tree.solution_info.getSolnum
    result = dataset_solution_indices(tree.worker, "Model", {"path": "dset1"})
    assert result["axis_metadata_complete"] is True
    assert result["pair_mapping_complete"] is False
    assert result["binding_complete"] is False


def test_sample_run_failure_does_not_retry_coordinate_setter_or_read_data() -> None:
    tree = FWiredTree()
    original = tree.numerical_list.factory
    created_features: list[Any] = []

    class _RunFailsFeature(FNumericalFeature):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self.get_data_calls = 0
            self.interpolation_calls = 0

        def setIndex(self, name: str, value: Any, index: int) -> None:
            self._guard("setIndex", (name, value, index))
            values = list(self.props.get(name, []))
            while len(values) <= index:
                values.append(None)
            values[index] = value
            self.props[name] = values

        def setInterpolationCoordinates(self, coords: Any) -> None:
            self._guard("setInterpolationCoordinates", (coords,))
            self.interpolation_calls += 1
            self.props["coord"] = coords

        def run(self) -> None:
            self._guard("run", ())
            raise FakeEngineError("run refused", code="ENGINE_CALL_FAILED")

        def getData(self) -> Any:
            self.get_data_calls += 1
            raise AssertionError("getData must not run after a failed feature.run")

    def factory(tag: str, *args: Any) -> Any:
        base = original(tag, *args)
        feature = _RunFailsFeature(
            tag=base.tag_, type_id=base.type_id, real_data=base.real_data,
            imag_data=base.imag_data, is_complex=base.is_complex_flag,
            coordinates=base.coordinates, props=base.props,
        )
        created_features.append(feature)
        return feature

    tree.numerical_list.factory = factory
    result = sample_path(tree.worker, "Model", {
        "spec": {"expressions": ["T"], "dataset": "dset1"},
        "path_definition": {"kind": "line", "start": [0.0, 0.0, 0.0],
                             "end": [0.01, 0.01, 0.01], "samples": 2},
    })
    assert result["engine_error"]["code"] == "ENGINE_CALL_FAILED"
    assert result["status"]["status"] == "FAILED"
    assert result["read_errors"] == []
    assert tree.numerical_list.tags() == []
    assert len(created_features) == 1
    assert created_features[0].interpolation_calls == 0
    assert created_features[0].get_data_calls == 0
    assert result["status"]["not_executed"] == ["result.numerical.getData"]


def test_axisymmetric_cross_section_restore_failure_is_not_swallowed() -> None:
    tree = FWiredTree(sdim=2, is_axisymmetric=True, numerical_real=300.0)
    original = tree.numerical_list.factory

    class _RestoreFailsFeature(FNumericalFeature):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self.on_calls = 0

        def set(self, name: str, value: Any) -> None:
            if name == "intvolume" and value == "on":
                self.on_calls += 1
                if self.on_calls >= 2:
                    self._guard("set", (name, value))
                    raise FakeEngineError("restore refused", code="METHOD_REJECTED")
            super().set(name, value)

    def factory(tag: str, *args: Any) -> Any:
        base = original(tag, *args)
        if not tag.endswith("_meas"):
            return base
        return _RestoreFailsFeature(
            tag=base.tag_, type_id=base.type_id, real_data=base.real_data,
            imag_data=base.imag_data, is_complex=base.is_complex_flag,
            coordinates=base.coordinates, props=base.props,
        )

    tree.numerical_list.factory = factory
    with pytest.raises(ExecutionContractError) as restored:
        result_evaluate(tree.worker, "Model", {
            "spec": {"expressions": ["T"], "solution": {"dataset": "dset1"},
                     "aggregate": "average", "complex_mode": "real"},
        })
    assert restored.value.code == "AXISYMMETRY_RESTORE_FAILED"
