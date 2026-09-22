"""Independent semantic tests for the model-definition probe adapter.

These fakes expose the narrow COMSOL calls used by ``_probe_manage``.  They
record mutations so a test can distinguish a real readback/stop condition
from an echoed request.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from comsol_mcp import _probe_manage as probes
from comsol_mcp._execution_contract import ExecutionContractError, PreWriteRefusal


class FakeSelection:
    def __init__(self) -> None:
        self.named_value: str | None = None
        self.inherited = False
        self.entities_value: list[int] = []
        self.geometry: str | None = None
        self.dimension: int | None = None

    def named(self, value: str | None = None) -> str | None:
        if value is not None:
            self.named_value = value
        return self.named_value

    def isInheriting(self) -> bool:
        return self.inherited

    def entities(self) -> list[int]:
        return list(self.entities_value)

    def geom(self, geometry: str | int, dimension: int | None = None) -> None:
        if dimension is None:
            self.dimension = int(geometry)
        else:
            self.geometry = str(geometry)
            self.dimension = int(dimension)

    def set(self, entities: list[int]) -> None:
        self.entities_value = list(entities)

    def all(self) -> None:
        self.entities_value = [1, 2]

    def inherit(self, value: bool) -> None:
        self.inherited = value

    def dim(self) -> int | None:
        return self.dimension


class FakeProbe:
    def __init__(self, native_type: str, *, fail_on: str | None = None, values: dict[str, Any] | None = None) -> None:
        self.native_type = native_type
        self.values = dict(values or {})
        self.fail_on = fail_on
        self.set_calls: list[str] = []
        self.gen_calls: list[Any] = []
        self.selection_node = FakeSelection()
        self.model_owner: str | None = None

    def getType(self) -> str:
        return self.native_type

    def set(self, name: str, value: Any) -> None:
        self.set_calls.append(name)
        if name == self.fail_on:
            raise RuntimeError(f"rejected {name}")
        self.values[name] = value

    def getString(self, name: str) -> str:
        value = self.values.get(name, "")
        return str(value)

    def getDouble(self, name: str) -> float:
        return float(self.values[name])

    def getBoolean(self, name: str) -> bool:
        return bool(self.values[name])

    def getStringArray(self, name: str) -> list[str]:
        return list(self.values[name])

    def selection(self) -> FakeSelection:
        return self.selection_node

    def genResult(self, solver: Any) -> None:
        self.gen_calls.append(solver)

    def model(self, component: str | None = None) -> str | None:
        if component is not None:
            self.model_owner = component
        return self.model_owner


class FakeProbeContainer:
    def __init__(self) -> None:
        self.entries: dict[str, FakeProbe] = {}
        self.next_fail_on: str | None = None

    def tags(self) -> list[str]:
        return list(self.entries)

    def get(self, tag: str) -> FakeProbe:
        return self.entries[tag]

    def __call__(self, tag: str) -> FakeProbe:
        return self.entries[tag]

    def create(self, tag: str, native_type: str) -> FakeProbe:
        node = FakeProbe(native_type, fail_on=self.next_fail_on)
        self.entries[tag] = node
        self.next_fail_on = None
        return node

    def remove(self, tag: str) -> None:
        del self.entries[tag]


class FakeComponent:
    def __init__(self, probes: FakeProbeContainer) -> None:
        self._probes = probes

    def probe(self, tag: str | None = None) -> Any:
        return self._probes if tag is None else self._probes.get(tag)


class FakeModelNode:
    def __init__(self, components: list[str]) -> None:
        self.components = components

    def tags(self) -> list[str]:
        return list(self.components)


class FakeTable:
    def __init__(self, *, headers: list[str], rows: list[list[float]], imag: list[list[float]] | None = None) -> None:
        self.headers = headers
        self.rows = rows
        self.imag = imag

    def getColumnHeaders(self) -> list[str]:
        return list(self.headers)

    def getRowHeaders(self) -> list[str]:
        return ["t0", "t1", "t2"][: len(self.rows)]

    def getReal(self) -> list[list[float]]:
        return [list(row) for row in self.rows]

    def getImag(self) -> list[list[float]]:
        if self.imag is None:
            raise AttributeError("real table")
        return [list(row) for row in self.imag]

    def getTimes(self) -> list[float]:
        return [0.0, 1.0, 2.0][: len(self.rows)]

    def getPVals(self) -> dict[str, str]:
        return {"p": "1"}


class FakeTableContainer:
    def __init__(self, entries: dict[str, FakeTable] | None = None) -> None:
        self.entries = dict(entries or {})

    def tags(self) -> list[str]:
        return list(self.entries)

    def get(self, tag: str) -> FakeTable:
        return self.entries[tag]


class FakeResults:
    def __init__(self, table: FakeTableContainer) -> None:
        self._table = table

    def table(self) -> FakeTableContainer:
        return self._table


class FakeModel:
    def __init__(self, *, components: bool = False, table: FakeTableContainer | None = None) -> None:
        self.root = FakeProbeContainer()
        self.component_containers: dict[str, FakeProbeContainer] = {}
        if components:
            self.component_containers["comp1"] = FakeProbeContainer()
        self._table = table or FakeTableContainer()

    def probe(self, tag: str | None = None) -> Any:
        return self.root if tag is None else self.root.get(tag)

    def modelNode(self) -> FakeModelNode:
        return FakeModelNode(list(self.component_containers))

    def component(self, tag: str) -> FakeComponent:
        return FakeComponent(self.component_containers[tag])

    def result(self) -> FakeResults:
        return FakeResults(self._table)


class FakeWorker:
    def __init__(self, model: FakeModel) -> None:
        self.model = model

    def client(self) -> Any:
        model = self.model
        return type("Client", (), {"model": lambda _self, _tag: model})()


def _path(*segments: dict[str, str]) -> dict[str, Any]:
    return {"segments": list(segments)}


def test_global_probe_uses_native_global_variable_and_stops_after_first_failure() -> None:
    model = FakeModel(components=True)
    model.root.next_fail_on = "unit"
    worker = FakeWorker(model)

    result = probes.probe_create(worker, "m", {
        "tag": "gp1",
        "type_id": "GlobalProbe",
        "definition": {"expr": "T", "unit": "K", "descr": "later"},
    })

    assert result["created"] is True
    assert result["native_type"] == "GlobalVariable"
    assert result["component"] == "comp1"
    assert model.root.entries["gp1"].model_owner == "comp1"
    assert model.root.entries["gp1"].native_type == "GlobalVariable"
    assert model.root.entries["gp1"].set_calls == ["expr", "unit"]
    assert result["not_executed"] == [{"step": "property", "property": "descr"}]
    assert result["failed"][0]["property"] == "unit"
    assert result["domain_outcome"]["state"] in {"PARTIAL", "UNKNOWN"}


def test_create_prevalidates_table_and_can_explicitly_prepare_result() -> None:
    table = FakeTable(headers=["T"], rows=[[1.0]], imag=[[0.5]])
    model = FakeModel(components=True, table=FakeTableContainer({"tbl1": table}))
    worker = FakeWorker(model)
    result = probes.probe_create(worker, "m", {
        "tag": "gp2",
        "type_id": "GlobalProbe",
        "definition": {
            "expr": "T",
            "unit": "K",
            "table": "tbl1",
            "gen_result": {"solution": "sol1"},
        },
    })

    assert result["ok"] is True
    assert result["table"] == "tbl1"
    assert model.root.entries["gp2"].gen_calls == ["sol1"]
    assert any(step["step"] == "genResult" for step in result["applied"])


def test_global_probe_explicit_component_is_bound_and_typed_path_resolves() -> None:
    model = FakeModel(components=True)
    worker = FakeWorker(model)
    created = probes.probe_create(worker, "m", {
        "tag": "bound_global",
        "type_id": "GlobalProbe",
        "component": "comp1",
        "definition": {"expr": "2"},
    })

    assert created["ok"] is True
    assert created["component"] == "comp1"
    assert created["readback"]["model"]["match"] is True
    listed = probes.probe_list(worker, "m")
    item = next(item for item in listed["probes"] if item["tag"] == "bound_global")
    assert item["component"] == "comp1"
    updated = probes.probe_update(worker, "m", {
        "path": _path({"collection": "component", "tag": "comp1"},
                       {"collection": "probe", "tag": "bound_global"}),
        "definition": {"expr": "3"},
    })
    assert updated["ok"] is True
    assert model.root.entries["bound_global"].values["expr"] == "3"


def test_global_probe_without_component_refuses_multiple_native_owners() -> None:
    model = FakeModel(components=True)
    model.component_containers["comp2"] = FakeProbeContainer()
    with pytest.raises(PreWriteRefusal) as refused:
        probes.probe_create(FakeWorker(model), "m", {
            "tag": "ambiguous_global",
            "type_id": "GlobalProbe",
            "definition": {"expr": "2"},
        })
    assert refused.value.code == "AMBIGUOUS_NODE_PATH"
    assert "ambiguous_global" not in model.root.entries


def test_update_requires_typed_path_and_rejects_ambiguous_legacy_tag() -> None:
    model = FakeModel(components=True)
    model.root.entries["same"] = FakeProbe("GlobalVariable", values={"expr": "old"})
    model.component_containers["comp1"].entries["same"] = FakeProbe("Domain", values={"expr": "old"})
    worker = FakeWorker(model)

    with pytest.raises(ExecutionContractError) as ambiguous:
        probes.probe_update(worker, "m", {"tag": "same", "definition": {"expr": "new"}})
    assert ambiguous.value.code == "AMBIGUOUS_NODE_PATH"

    result = probes.probe_update(worker, "m", {
        "path": _path({"collection": "component", "tag": "comp1"}, {"collection": "probe", "tag": "same"}),
        "definition": {"expr": "new"},
    })
    assert result["ok"] is True
    assert model.component_containers["comp1"].entries["same"].values["expr"] == "new"

    with pytest.raises(ExecutionContractError) as invalid:
        probes.probe_update(worker, "m", {
            "path": _path({"collection": "geometry", "tag": "geom1"}),
            "definition": {"expr": "bad"},
        })
    assert invalid.value.code == "INVALID_NODE_PATH"


def test_update_rejects_unknown_property_as_pre_dispatch_validation() -> None:
    """A typo cannot become an UNKNOWN result when no setter was issued."""
    model = FakeModel()
    node = FakeProbe("GlobalVariable", values={"expr": "old"})
    model.root.entries["guarded"] = node
    worker = FakeWorker(model)

    with pytest.raises(PreWriteRefusal) as refused:
        probes.probe_update(worker, "m", {
            "path": _path({"collection": "probe", "tag": "guarded"}),
            "definition": {"definitely_not_a_probe_property": "bad"},
        })

    assert refused.value.code == "INVALID_REQUEST"
    assert refused.value.stage == "validation"
    assert node.set_calls == []
    assert node.values["expr"] == "old"


def test_history_reads_real_complex_table_and_paginates_without_gen_result() -> None:
    table = FakeTable(
        headers=["T", "u"],
        rows=[[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]],
        imag=[[0.1, 0.2], [0.3, 0.4], [0.5, 0.6]],
    )
    model = FakeModel(table=FakeTableContainer({"tbl1": table}))
    node = FakeProbe("GlobalVariable", values={"table": "tbl1", "unit": "K"})
    model.root.entries["hist"] = node
    worker = FakeWorker(model)
    path = _path({"collection": "probe", "tag": "hist"})

    first = probes.probe_history(worker, "m", {
        "path": path,
        "limit": 2,
        "solution": {"dataset": "dset1", "time": [{"value": 1, "unit": "s"}], "parameters": {"p": "1"}},
    })
    assert first["headers"] == ["T", "u"]
    assert first["unit"] == "K"
    assert first["rows"][0][0] == {"real": 1.0, "imag": 0.1}
    assert first["time"] == [0.0, 1.0, 2.0]
    assert first["parameters"] == {"p": "1"}
    assert first["has_more"] is True
    assert first["source"]["gen_result_called"] is False
    assert node.gen_calls == []

    second = probes.probe_history(worker, "m", {"path": path, "limit": 2, "cursor": first["next_cursor"]})
    assert second["offset"] == 2
    assert len(second["rows"]) == 1
    assert second["next_cursor"] is None


def test_history_without_real_linked_table_is_explicit_no_history() -> None:
    model = FakeModel()
    model.root.entries["empty"] = FakeProbe("GlobalVariable", values={"table": ""})
    with pytest.raises(ExecutionContractError) as error:
        probes.probe_history(FakeWorker(model), "m", {"path": _path({"collection": "probe", "tag": "empty"})})
    assert error.value.code == "NO_HISTORY"


def test_probe_create_component_is_published_in_both_catalogs() -> None:
    """The ``component`` selector must be part of the published wire schema.

    COMSOL 6.4 (Programming Reference p181) binds a root ``ProbeFeature`` to
    its owning component through ``model.probe(tag).model(component)``, so the
    C14 transient case sends ``component``.  The registry validator rejects any
    argument the published schema does not name, so both the runtime catalog
    and its design copy have to carry it.
    """
    root = Path(__file__).resolve().parents[1]
    for catalog_path in (
        root / "comsol_mcp" / "data" / "g2" / "02_ACTION_CATALOG.json",
        root / "docs" / "comsol_mcp_design_v1" / "02_ACTION_CATALOG.json",
    ):
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        entry = next(item for item in catalog["operations"] if item["operation_id"] == "probe.create")
        properties = entry["input_schema"]["properties"]
        assert properties["component"] == {"type": "string", "minLength": 1}
        assert "component" not in entry["input_schema"]["required"], "the component stays optional"
        assert entry["input_schema"]["additionalProperties"] is False
        assert "component:str?" in entry["arguments_signature"], entry["arguments_signature"]


def test_registry_validator_accepts_component_and_still_refuses_unknown_arguments() -> None:
    """``validate_call`` accepts the published probe body and stays fail-closed."""
    from comsol_mcp import _g2_registry

    body = {
        "project_id": "p",
        "session_id": "s",
        "model_ref": {"schema_version": 1, "session_id": "s", "server_instance_id": "i",
                      "model_tag": "mcp1", "generation": 1},
        "expected_revision": 1,
        "idempotency_key": "k",
        "tag": "g33probe",
        "type_id": "GlobalProbe",
        "component": "comp1",
        "definition": {"expr": "2"},
    }
    entry = _g2_registry.validate_call("probe.create", dict(body))
    assert entry.operation_id == "probe.create"
    with pytest.raises(ExecutionContractError) as refused:
        _g2_registry.validate_call("probe.create", {**body, "component_tag": "comp1"})
    assert "unsupported arguments: component_tag" in str(refused.value)


def test_component_owned_root_probe_is_listed_once_and_resolves_a_typed_path() -> None:
    """COMSOL shows a model-bound root probe in both probe collections.

    The engine stores that probe once (observed live: a root ``GlobalVariable``
    bound through ``model.probe(tag).model(component)`` is returned by both
    ``model.probe()`` and ``model.component(<owner>).probe()``).  The adapter
    must not turn the two views into a duplicated list entry or an
    ``AMBIGUOUS_NODE_PATH`` refusal for the canonical typed path.
    """
    model = FakeModel(components=True)
    probe = FakeProbe("GlobalVariable", values={"expr": "2", "table": ""})
    probe.model_owner = "comp1"
    model.root.entries["mirror"] = probe
    model.component_containers["comp1"].entries["mirror"] = probe
    worker = FakeWorker(model)

    listed = probes.probe_list(worker, "m")
    assert listed["count"] == 1, listed
    assert [item["tag"] for item in listed["probes"]] == ["mirror"]
    assert listed["probes"][0]["component"] == "comp1"

    updated = probes.probe_update(worker, "m", {
        "path": _path({"collection": "component", "tag": "comp1"},
                      {"collection": "probe", "tag": "mirror"}),
        "definition": {"properties": {"expr": "3"}},
    })
    assert updated["ok"] is True
    assert probe.values["expr"] == "3"


def test_same_tag_in_two_components_stays_ambiguous() -> None:
    """The merge only folds the root/component mirror, never real owners."""
    model = FakeModel(components=True)
    model.component_containers["comp2"] = type(model.component_containers["comp1"])()
    first = FakeProbe("GlobalVariable", values={"expr": "2"})
    first.model_owner = "comp1"
    second = FakeProbe("GlobalVariable", values={"expr": "2"})
    second.model_owner = "comp2"
    model.component_containers["comp1"].entries["shared_tag"] = first
    model.component_containers["comp2"].entries["shared_tag"] = second
    worker = FakeWorker(model)

    listed = probes.probe_list(worker, "m", {"filter": {"tag": "shared_tag"}})
    assert listed["count"] == 2, "two distinct native probes stay two list entries"
    with pytest.raises(ExecutionContractError) as refused:
        probes.probe_update(worker, "m", {
            "tag": "shared_tag", "definition": {"properties": {"expr": "4"}},
        })
    assert refused.value.code == "AMBIGUOUS_NODE_PATH"
    # the typed path picks exactly one of them
    resolved = probes.probe_update(worker, "m", {
        "path": _path({"collection": "component", "tag": "comp2"},
                      {"collection": "probe", "tag": "shared_tag"}),
        "definition": {"properties": {"expr": "4"}},
    })
    assert resolved["ok"] is True
    assert second.values["expr"] == "4" and first.values["expr"] == "2"
