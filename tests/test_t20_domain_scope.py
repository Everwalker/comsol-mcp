"""T20: component-scope isolation and interpolation-data acceptance pins.

These tests pin the T20 acceptance dimensions that the existing W13 suite left
uncovered: arbitrary-but-reasonable names under *two different* components
(the same group/function tag in both components must never leak), the
component-local function lifecycle, and interpolation data import/reload
scoped to a component.  They use a small self-contained fake engine (no import
from tests/test_g3_w13.py, which another task may be editing) and exercise the
same production modules the managed backend dispatches through _g3_ops.DISPATCH.

Software-layer evidence only: the fake reports getValueType metadata the way
COMSOL 6.4 does for the properties exercised here, but nothing in this file is
native COMSOL evidence.
"""

from __future__ import annotations

from typing import Any, Mapping

import pytest

from comsol_mcp import _g3_w13 as w13
from comsol_mcp._g2_contract import ExecutionContractError

_METADATA_KINDS = {
    "String": "string", "StringArray": "string", "StringMatrix": "string",
    "Boolean": "boolean", "BooleanArray": "boolean", "BooleanMatrix": "boolean",
    "Int": "int32", "IntArray": "int32", "IntMatrix": "int32",
    "Double": "float64", "DoubleArray": "float64", "DoubleMatrix": "float64",
    "DoubleRowMatrix": "float64",
}


class FakeEngineError(RuntimeError):
    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


class FNode:
    """Reduced stand-in for a COMSOL model/component/function/variable node."""

    def __init__(self, *, tag: str, type_id: str = "Fake",
                 collections: Mapping[str, Any] | None = None,
                 value_types: Mapping[str, str] | None = None,
                 allowed: Mapping[str, list[str]] | None = None) -> None:
        self.tag_ = tag
        self.type_id = type_id
        self.collections = dict(collections or {})
        self.value_types = dict(value_types or {})
        self.allowed = dict(allowed or {})
        self.expressions: dict[str, dict[str, Any]] = {}
        self.values: dict[str, Any] = {}
        self.numbers: dict[str, float] = {}
        self.integers: dict[str, int] = {}
        self.booleans: dict[str, bool] = {}
        self.arrays: dict[str, list[Any]] = {}
        self.matrices: dict[str, list[list[Any]]] = {}
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.label_ = tag
        self.model_ref: str | None = None
        self.import_datasets = 0
        self.refreshes = 0

    # -- identity ----------------------------------------------------------
    def tag(self) -> str:
        return self.tag_

    def getType(self) -> str:
        self.calls.append(("getType", ()))
        return self.type_id

    def label(self, *args: Any) -> Any:
        if args:
            self.label_ = str(args[0])
            return None
        return self.label_

    # -- expression collections -------------------------------------------
    def varnames(self) -> list[str]:
        return list(self.expressions)

    def get(self, name: str) -> Any:
        return self.expressions[name]["expression"]

    def descr(self, *args: Any) -> Any:
        name = args[0]
        if len(args) > 1:
            self.expressions[name]["description"] = args[1]
            return None
        return self.expressions[name].get("description")

    def evaluateUnit(self, name: str) -> str | None:
        row = self.expressions[name]
        expression = str(row.get("expression") or "")
        if expression.endswith("]") and "[" in expression:
            return expression[expression.rfind("[") + 1:-1]
        return None

    def remove(self, name: str) -> None:
        if name not in self.expressions:
            raise FakeEngineError(f"no such name {name}")
        del self.expressions[name]

    # -- property metadata and typed values -------------------------------
    def properties(self) -> list[str]:
        names = set(self.value_types) | set(self.values) | set(self.numbers) | set(self.integers)
        names |= set(self.booleans) | set(self.arrays) | set(self.matrices)
        return sorted(names)

    def getValueType(self, name: str) -> str | None:
        return self.value_types.get(name)

    def getAllowedPropertyValues(self, name: str) -> list[str] | None:
        return self.allowed.get(name)

    def hasProperty(self, name: str) -> bool:
        return name in self.properties()

    def _read(self, name: str) -> Any:
        value_type = self.value_types.get(name)
        stores = {
            "String": ("values",), "StringArray": ("arrays", "values"),
            "StringMatrix": ("matrices", "values"), "Double": ("numbers",),
            "Int": ("integers",), "Boolean": ("booleans",),
        }.get(value_type, ("values", "numbers", "integers", "booleans", "arrays", "matrices"))
        for store in stores:
            holder = getattr(self, store)
            if name in holder:
                return holder[name]
        return None

    def getString(self, name: str) -> Any:
        return self._read(name)

    def getStringArray(self, name: str) -> Any:
        return self._read(name)

    def getStringMatrix(self, name: str) -> Any:
        return self._read(name)

    def getDouble(self, name: str) -> Any:
        return self._read(name)

    def getInt(self, name: str) -> Any:
        return self._read(name)

    def getBoolean(self, name: str) -> Any:
        return self._read(name)

    def set(self, *args: Any) -> None:
        if len(args) == 2 and isinstance(args[1], Mapping):
            name, typed = args
            self._store_typed(name, typed.get("kind"), typed.get("data"))
            return
        if len(args) == 2:
            name, value = args
            metadata = self.value_types.get(name)
            if metadata is None and isinstance(value, str):
                self._store_typed(name, "expression", value)
                return
            self._store_typed(name, _METADATA_KINDS.get(metadata) if isinstance(metadata, str) else None, value)
            return
        if len(args) >= 3:
            name, value, description = args[:3]
            self._store_typed(name, "expression", value)
            self.expressions.setdefault(name, {})["description"] = description
            return
        raise TypeError("set() received an unsupported argument count")

    def _store_typed(self, name: str, kind: str | None, data: Any) -> None:
        if isinstance(data, str) and (kind in {None, "string", "expression"}):
            self.values[name] = data
            if kind == "expression":
                self.expressions.setdefault(name, {})["expression"] = data
            return
        if isinstance(data, (list, tuple)):
            rows = list(data)
            if rows and isinstance(rows[0], (list, tuple)):
                self.matrices[name] = [list(row) for row in rows]
            else:
                self.arrays[name] = list(rows)
            return
        if isinstance(data, bool):
            self.booleans[name] = data
        elif isinstance(data, int):
            self.integers[name] = data
        elif isinstance(data, float):
            self.numbers[name] = data
        else:
            self.values[name] = data

    # -- function data lifecycle ------------------------------------------
    def functionNames(self) -> list[str]:
        return [str(item) for item in self.arrays.get("function_names", [])]

    def importData(self, *args: Any) -> None:
        self.calls.append(("importData", args))
        self.import_datasets += 1

    def refresh(self) -> None:
        self.calls.append(("refresh", ()))
        self.refreshes += 1

    # -- variable-group scope accessors ------------------------------------
    def model(self, *args: Any) -> Any:
        if args:
            self.model_ref = str(args[0])
            return None
        return self.model_ref

    def scope(self) -> str:
        return self.model_ref or "global"

    def selection(self, *args: Any) -> Any:
        return self

    def named(self, *args: Any) -> Any:
        return None

    def isInheriting(self) -> bool:
        return True

    def entities(self, *args: Any) -> list[int]:
        return []

    # -- dynamic child collections -----------------------------------------
    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        collections = self.__dict__.get("collections") or {}
        if name in collections:
            target = collections[name]

            def accessor(tag: Any = None, *rest: Any) -> Any:
                if tag is None:
                    return target
                return target.get(tag)

            return accessor
        raise AttributeError(name)


class FList:
    def __init__(self, *, node_type: str = "Fake", node_factory: Any = None) -> None:
        self.items: dict[str, FNode] = {}
        self.node_type = node_type
        self.node_factory = node_factory

    def tags(self) -> list[str]:
        return list(self.items)

    def get(self, tag: str) -> FNode:
        return self.items[tag]

    def hasTag(self, tag: str) -> bool:
        return tag in self.items

    def create(self, tag: str, *type_id: Any) -> FNode:
        if tag in self.items:
            raise FakeEngineError(f"tag {tag} already exists")
        if self.node_factory is not None:
            node = self.node_factory(tag, str(type_id[0]) if type_id else self.node_type)
        else:
            node = FNode(tag=tag, type_id=(str(type_id[0]) if type_id else self.node_type))
        self.items[tag] = node
        return node

    def remove(self, tag: str) -> None:
        if tag not in self.items:
            raise FakeEngineError(f"no such tag {tag}")
        del self.items[tag]


INTERPOLATION_META = {
    "filename": "String", "struct": "String", "nargs": "Int", "dseparator": "String",
    "source": "String", "argunit": "String", "fununit": "String", "funcname": "String",
    "funcs": "StringMatrix", "interp": "String", "extrap": "String", "table": "StringMatrix",
}
INTERPOLATION_ALLOWED = {
    "struct": ["grid", "sectionwise", "spreadsheet"], "dseparator": ["point", "comma"],
    "source": ["table", "file", "resultTable", "function"],
    "interp": ["neighbor", "linear", "piecewisecubic", "cubicspline"],
    "extrap": ["none", "const", "interior", "linear", "value"],
}
ANALYTIC_META = {"expr": "String", "args": "StringArray", "fununit": "String", "argunit": "String"}


def function_factory(tag: str, type_id: str) -> FNode:
    metadata, allowed = {
        "Interpolation": (INTERPOLATION_META, INTERPOLATION_ALLOWED),
        "Analytic": (ANALYTIC_META, {}),
    }.get(type_id, ({}, {}))
    return FNode(tag=tag, type_id=type_id, value_types=dict(metadata), allowed=dict(allowed))


def component(tag: str) -> FNode:
    return FNode(tag=tag, type_id="Component", collections={
        "geom": FList(node_type="GeomSequence"),
        "selection": FList(node_type="SelectionFeature"),
        "variable": FList(node_type="Expr"),
        "func": FList(node_type="FunctionFeature", node_factory=function_factory),
    })


def two_component_model() -> tuple[FNode, dict[str, FNode]]:
    core = component("core")
    clad2 = component("cladX_2")
    model = FNode(tag="Model", type_id="Model", collections={
        "component": FList(node_type="Component"),
        "param": FList(node_type="ModelParam"),
        "variable": FList(node_type="Expr"),
        "func": FList(node_type="FunctionFeature", node_factory=function_factory),
        "selection": FList(node_type="SelectionFeature"),
    })
    model.collections["component"].items["core"] = core
    model.collections["component"].items["cladX_2"] = clad2
    return model, {"core": core, "cladX_2": clad2}


class FClient:
    def __init__(self, model: FNode) -> None:
        self._model = model

    def model(self, tag: str) -> FNode:
        return self._model


class FWorker:
    def __init__(self, model: FNode) -> None:
        self.generation = 1
        self._client = FClient(model)

    def client(self) -> FClient:
        return self._client


def worker_for(model: FNode) -> FWorker:
    return FWorker(model)


def path(*segments: tuple[str, str]) -> dict[str, Any]:
    return {"segments": [{"collection": c, "tag": t} for c, t in segments]}


def variable_group(comp: str, tag: str) -> dict[str, Any]:
    return path(("component", comp), ("variable", tag))


# ---------------------------------------------------------------------------
# variables: same group tag and same variable names in two components
# ---------------------------------------------------------------------------


def test_variable_group_same_tag_two_components_stay_independent():
    model, _comps = two_component_model()
    worker = worker_for(model)
    created_a = w13.variable_group_create(worker, "Model", {"tag": "defs", "component": "core"})
    created_b = w13.variable_group_create(worker, "Model", {"tag": "defs", "component": "cladX_2"})
    assert created_a["scope"] == "core" and created_b["scope"] == "cladX_2"

    set_a = w13.variable_set(worker, "Model", {
        "group": variable_group("core", "defs"),
        "variables": [{"name": "eps_r", "expression": "8.85e-12[F/m]", "description": "core permittivity"},
                      {"name": "LossTan_9", "expression": "0.002"}]})
    set_b = w13.variable_set(worker, "Model", {
        "group": variable_group("cladX_2", "defs"),
        "variables": [{"name": "eps_r", "expression": "2.1e-11[F/m]", "description": "cladding permittivity"}]})
    assert set_a["ok"] and set_b["ok"]
    assert set_a["component"] == "core" and set_b["component"] == "cladX_2"
    assert set_a["applied"][0]["description_readback"] == "core permittivity"

    read_a = w13.variable_get(worker, "Model", {"group": variable_group("core", "defs")})
    read_b = w13.variable_get(worker, "Model", {"group": variable_group("cladX_2", "defs")})
    assert read_a["varnames"] == ["eps_r", "LossTan_9"] and read_b["varnames"] == ["eps_r"]
    assert read_a["variables"][0]["expression"] == "8.85e-12[F/m]"
    assert read_b["variables"][0]["expression"] == "2.1e-11[F/m]"
    assert read_b["variables"][0]["description"] == "cladding permittivity"
    assert read_b["variables"][0]["unit"] == "F/m"

    listed = w13.variable_list(worker, "Model", {"component": "cladX_2"})
    assert listed["scope"] == "cladX_2"
    assert [g["group"] for g in listed["groups"]] == ["defs"]
    assert listed["groups"][0]["variables"][0]["expression"] == "2.1e-11[F/m]"

    removed = w13.variable_remove(worker, "Model", {
        "group": variable_group("cladX_2", "defs"), "names": ["eps_r"]})
    assert removed["ok"] and removed["varnames"] == []
    survivors = w13.variable_get(worker, "Model", {"group": variable_group("core", "defs")})
    assert survivors["varnames"] == ["eps_r", "LossTan_9"]


# ---------------------------------------------------------------------------
# functions: same tag in two components, independent lifecycle
# ---------------------------------------------------------------------------


def test_function_same_tag_two_components_lifecycle_is_independent():
    model, comps = two_component_model()
    worker = worker_for(model)
    created_a = w13.function_create(worker, "Model", {
        "scope": path(("component", "core")), "tag": "Ramp", "type_id": "Analytic",
        "definition": {"args": ["t"], "expr": "t*50[K]", "fununit": "K"}})
    created_b = w13.function_create(worker, "Model", {
        "scope": path(("component", "cladX_2")), "tag": "Ramp", "type_id": "Analytic",
        "definition": {"args": ["t"], "expr": "t*80[K]", "fununit": "K"}})
    assert created_a["ok"] and created_b["ok"]
    assert created_a["scope"] == "core" and created_b["scope"] == "cladX_2"
    assert created_a["path"]["segments"][-1] == {"collection": "func", "tag": "Ramp"}

    updated = w13.function_update(worker, "Model", {
        "path": path(("component", "cladX_2"), ("func", "Ramp")),
        "definition": {"expr": "t*95[K]"}})
    assert updated["ok"]

    inspect_a = w13.function_inspect(worker, "Model", {"path": path(("component", "core"), ("func", "Ramp"))})
    inspect_b = w13.function_inspect(worker, "Model", {"path": path(("component", "cladX_2"), ("func", "Ramp"))})
    assert inspect_a["properties"]["expr"]["value"] == "t*50[K]"
    assert inspect_b["properties"]["expr"]["value"] == "t*95[K]"

    listed_a = w13.function_list(worker, "Model", {"component": "core"})
    listed_b = w13.function_list(worker, "Model", {"component": "cladX_2"})
    assert [row["tag"] for row in listed_a["functions"]] == ["Ramp"]
    assert [row["tag"] for row in listed_b["functions"]] == ["Ramp"]
    assert comps["core"].collections["func"].items["Ramp"] is not comps["cladX_2"].collections["func"].items["Ramp"]

    removed = w13.function_remove(worker, "Model", {"path": path(("component", "core"), ("func", "Ramp"))})
    assert removed["removed"] is True
    assert w13.function_list(worker, "Model", {"component": "cladX_2"})["count"] == 1


# ---------------------------------------------------------------------------
# interpolation data scoped to a component
# ---------------------------------------------------------------------------


def _create_interp(worker: FWorker, comp: str, tag: str) -> dict[str, Any]:
    return w13.function_create(worker, "Model", {
        "scope": path(("component", comp)), "tag": tag, "type_id": "Interpolation",
        "definition": {"source": "file", "struct": "spreadsheet", "nargs": 1,
                       "dseparator": "comma", "interp": "linear", "extrap": "linear",
                       "argunit": "m", "fununit": "K"}})


def test_interpolation_data_import_and_reload_are_component_scoped(tmp_path):
    model, comps = two_component_model()
    worker = worker_for(model)
    # The SAME function tag also exists at global scope; the component node
    # must be the only one touched by the data operations.
    _create_interp(worker, "cladX_2", "lossCurve")
    w13.function_create(worker, "Model", {
        "tag": "lossCurve", "type_id": "Interpolation",
        "definition": {"source": "table", "struct": "spreadsheet", "nargs": 1,
                       "dseparator": "comma", "interp": "linear", "extrap": "linear"}})
    data = tmp_path / "loss profile.dat"
    data.write_text("0 0\n1 2.5\n2 5\n", encoding="utf-8")

    imported = w13.function_data_import(worker, "Model", {
        "path": path(("component", "cladX_2"), ("func", "lossCurve")),
        "artifact_id": "loss-profile",
        "layout": {"file_path": str(data), "struct": "spreadsheet", "nargs": 1,
                   "dseparator": "comma", "interp": "linear", "extrap": "linear"},
        "units": {"argunit": "m", "fununit": "K"}})
    assert imported["ok"] is True and imported["status"] == "APPLIED"
    assert imported["import_data"]["ok"] is True
    assert imported["artifact"]["sha256"]["status"] == "verified"

    comp_node = comps["cladX_2"].collections["func"].items["lossCurve"]
    global_node = model.collections["func"].items["lossCurve"]
    assert comp_node.import_datasets == 1
    assert global_node.import_datasets == 0
    assert comp_node.values["filename"] == str(data)
    assert comp_node.integers["nargs"] == 1
    assert global_node.values.get("filename") is None

    reloaded = w13.function_data_reload(worker, "Model", {
        "path": path(("component", "cladX_2"), ("func", "lossCurve"))})
    assert reloaded["status"] == "APPLIED" and comp_node.refreshes == 1
    assert global_node.refreshes == 0


def test_data_import_writes_documented_funcs_matrix(tmp_path):
    """funcs is a documented 6.4 Interpolation setting (StringMatrix): a
    multi-function spreadsheet layout must import, gated by the engine's own
    value type, instead of being refused by the property-name table."""
    model, comps = two_component_model()
    worker = worker_for(model)
    _create_interp(worker, "core", "multiFunc")
    data = tmp_path / "multi.dat"
    data.write_text("0 0\n1 1\n", encoding="utf-8")

    imported = w13.function_data_import(worker, "Model", {
        "path": path(("component", "core"), ("func", "multiFunc")),
        "artifact_id": "multi",
        "layout": {"file_path": str(data), "struct": "spreadsheet", "nargs": 1,
                   "dseparator": "comma", "funcs": [["multiFunc", "1"]]},
        "units": {}})
    assert imported["ok"] is True
    applied = {row["name"] for row in imported["applied"]}
    assert "funcs" in applied
    node = comps["core"].collections["func"].items["multiFunc"]
    assert node.matrices["funcs"] == [["multiFunc", "1"]]
    assert node.import_datasets == 1


def test_funcs_matrix_kind_is_still_gated_by_engine_metadata(tmp_path):
    """The table entry must not widen what is written: a scalar for a property
    the engine declares StringMatrix is refused before the first write."""
    model, comps = two_component_model()
    worker = worker_for(model)
    _create_interp(worker, "core", "badFuncs")
    data = tmp_path / "bad.dat"
    data.write_text("0 0\n", encoding="utf-8")
    with pytest.raises(ExecutionContractError) as info:
        w13.function_data_import(worker, "Model", {
            "path": path(("component", "core"), ("func", "badFuncs")),
            "artifact_id": "bad", "layout": {"file_path": str(data), "funcs": "multiFunc"},
            "units": {}})
    assert info.value.code == "PROPERTY_TYPE_MISMATCH"
    node = comps["core"].collections["func"].items["badFuncs"]
    assert node.import_datasets == 0
    assert "funcs" not in node.matrices
