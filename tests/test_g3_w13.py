"""W13: parameter / variable / function / selection domain operations.

Every test drives the real operation code against a fake COMSOL node tree.  The
fake mirrors the *verified* API surface used by the implementation: a
``ModelEntityList``-like container (``tags/get/create/remove``), ``PropFeature``
property metadata (``properties/getValueType/getAllowedPropertyValues`` and the
typed getters), ``ExpressionBase`` (``varnames/get/set/descr/evaluate``), the
local ``Selection`` surface (``named/geom/set/all/inherit/entities``) and the
``GeomMeasureBase`` getters.  A method the fake does not implement surfaces as
``AttributeError`` and a method listed in ``unavailable`` raises the worker
allow-list refusal (``METHOD_REJECTED``), so both "the API does not exist" and
"the worker refuses the method" paths are exercised.
"""
from __future__ import annotations

from typing import Any, Mapping

import pytest

from comsol_mcp._execution_contract import ExecutionContractError

from comsol_mcp import _g3_w13 as w13
from comsol_mcp._g3_ops import DISPATCH, EFFECTS, IMPLEMENTED_OPERATIONS, REQUIRES_ISOLATION

# ---------------------------------------------------------------------------
# fake engine
# ---------------------------------------------------------------------------

_METADATA_KINDS = {
    "String": "string", "StringArray": "string", "StringMatrix": "string",
    "Boolean": "boolean", "BooleanArray": "boolean", "BooleanMatrix": "boolean",
    "Int": "int32", "IntArray": "int32", "IntMatrix": "int32",
    "Double": "float64", "DoubleArray": "float64", "DoubleMatrix": "float64",
    "DoubleRowMatrix": "float64",
}
_GETTERS = {
    "String": "getString", "StringArray": "getStringArray", "StringMatrix": "getStringMatrix",
    "Boolean": "getBoolean", "BooleanArray": "getBooleanArray", "BooleanMatrix": "getBooleanMatrix",
    "Int": "getInt", "IntArray": "getIntArray", "IntMatrix": "getIntMatrix",
    "Double": "getDouble", "DoubleArray": "getDoubleArray", "DoubleMatrix": "getDoubleMatrix",
    "DoubleRowMatrix": "getDoubleMatrix",
}


class FakeEngineError(RuntimeError):
    """Structured worker failure; ``code`` feeds ``_worker_failure_code``."""

    def __init__(self, message: str, *, code: str = "ENGINE_CALL_FAILED") -> None:
        super().__init__(message)
        self.reply = {"ok": False, "code": code, "message": message}
        self.failure = {"code": code, "message": message}


class FList:
    """Stand-in for a COMSOL ``ModelEntityList``."""

    def __init__(self, *, arity: int = 1, unavailable: tuple[str, ...] = (), node_type: str = "Fake",
                 node_factory: Any = None) -> None:
        self.arity = arity
        self.items: dict[str, "FNode"] = {}
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.unavailable = set(unavailable)
        self.node_type = node_type
        self.node_factory = node_factory

    def tags(self) -> list[str]:
        self.calls.append(("tags", ()))
        return list(self.items)

    def get(self, tag: str) -> "FNode":
        self.calls.append(("get", (tag,)))
        return self.items[tag]

    def hasTag(self, tag: str) -> bool:
        return tag in self.items

    def size(self) -> int:
        return len(self.items)

    def index(self, tag: str) -> int:
        return list(self.items).index(tag)

    def create(self, tag: str, *type_id: Any) -> "FNode":
        self.calls.append(("create", (tag,) + type_id))
        if "create" in self.unavailable:
            raise FakeEngineError("SecurityException: METHOD_REJECTED", code="METHOD_REJECTED")
        if tag in self.items:
            raise FakeEngineError(f"tag {tag} already exists")
        if self.node_factory is not None:
            node = self.node_factory(tag, str(type_id[0]) if type_id else self.node_type)
        else:
            node = FNode(tag=tag, type_id=(str(type_id[0]) if type_id else self.node_type))
        self.items[tag] = node
        return node

    def remove(self, tag: str) -> None:
        self.calls.append(("remove", (tag,)))
        if "remove" in self.unavailable:
            raise FakeEngineError("SecurityException: METHOD_REJECTED", code="METHOD_REJECTED")
        if tag not in self.items:
            raise FakeEngineError(f"tag {tag} does not exist")
        del self.items[tag]

    def __getattr__(self, name: str) -> Any:
        raise AttributeError(name)


class FNode:
    """Generic fake COMSOL node covering the W13 API surface."""

    def __init__(self, tag: str = "n1", *, type_id: str = "Fake", label: str | None = None,
                 expressions: Mapping[str, Mapping[str, Any]] | None = None,
                 evaluations: Mapping[str, float] | None = None,
                 values: Mapping[str, Any] | None = None,
                 value_types: Mapping[str, str] | None = None,
                 allowed: Mapping[str, list[str]] | None = None,
                 collections: Mapping[str, FList] | None = None,
                 unavailable: tuple[str, ...] = (),
                 errors: Mapping[str, Exception] | None = None,
                 numbers: Mapping[str, float] | None = None, integers: Mapping[str, int] | None = None,
                 booleans: Mapping[str, bool] | None = None,
                 readback_overrides: Mapping[str, Any] | None = None,
                 expression_overrides: Mapping[str, str] | None = None,
                 entities_: list[int] | None = None, dim_: int | None = None,
                 geometry_tag: str | None = None, named_ref: str | None = None,
                 inheriting_: bool = False) -> None:
        self.tag_ = tag
        self.type_id = type_id
        self.label_ = label if label is not None else f"{tag} label"
        self.expressions = {name: dict(row) for name, row in (expressions or {}).items()}
        self.evaluations = dict(evaluations or {})
        self.values = dict(values or {})
        self.value_types = dict(value_types or {})
        self.allowed = dict(allowed or {})
        self.collections = dict(collections or {})
        self.unavailable = set(unavailable)
        self.errors = dict(errors or {})
        self.numbers = dict(numbers or {})
        self.integers = dict(integers or {})
        self.booleans = dict(booleans or {})
        self.arrays: dict[str, list[Any]] = {}
        self.matrices: dict[str, list[list[Any]]] = {}
        self.readback_overrides = dict(readback_overrides or {})
        self.expression_overrides = dict(expression_overrides or {})
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        # selection state
        self.named_ref: str | None = named_ref
        self.entities_: list[int] | None = list(entities_) if entities_ is not None else None
        self.dim_: int | None = dim_
        self.dimension_: list[int] = [dim_] if dim_ is not None else []
        self.geometry_tag: str | None = geometry_tag
        self.inheriting_ = bool(inheriting_)
        self.model_ref: str | None = None
        self.measure_entities: dict[int, list[int]] = {}
        self.measure_metrics: dict[str, Any] = {}
        self.import_datasets = 0
        self.refreshes = 0
        self.removed = False
        self.objects_: list[str] = []
        self.readback_tags_hook: Any = None

    # -- identity -----------------------------------------------------------
    def tag(self) -> str:
        self.calls.append(("tag", ()))
        return self.tag_

    def label(self, *args: Any) -> Any:
        self.calls.append(("label", args))
        if args:
            self.label_ = args[0]
            return None
        return self.label_

    def getType(self) -> str:
        self.calls.append(("getType", ()))
        return self.type_id

    def active(self) -> bool:
        return True

    # -- expression collections --------------------------------------------
    def varnames(self) -> list[str]:
        self.calls.append(("varnames", ()))
        if "varnames" in self.unavailable:
            raise FakeEngineError("SecurityException: METHOD_REJECTED", code="METHOD_REJECTED")
        return list(self.expressions)

    def get(self, name: str) -> str:
        self.calls.append(("get", (name,)))
        if name in self.expression_overrides:
            return self.expression_overrides[name]
        return self.expressions[name]["expression"]

    def descr(self, *args: Any) -> Any:
        self.calls.append(("descr", args))
        name = args[0]
        if len(args) > 1:
            self.expressions[name]["description"] = args[1]
            return None
        return self.expressions[name].get("description")

    def evaluate(self, name: str) -> float:
        self.calls.append(("evaluate", (name,)))
        return float(self.evaluations[name])

    def evaluateComplex(self, name: str) -> list[float]:
        self.calls.append(("evaluateComplex", (name,)))
        value = float(self.evaluations[name])
        return [value, 0.0]

    def evaluateUnit(self, name: str) -> str | None:
        self.calls.append(("evaluateUnit", (name,)))
        if "evaluateUnit" in self.unavailable:
            raise FakeEngineError("SecurityException: METHOD_REJECTED", code="METHOD_REJECTED")
        if name not in self.expressions:
            raise FakeEngineError(f"no such name {name}")
        unit = self.expressions[name].get("unit")
        if unit is None:
            # COMSOL derives the unit from the expression; the fake parses the
            # trailing [unit] annotation so a unit-bearing expression verifies.
            expression = str(self.expressions[name].get("expression") or "")
            if expression.endswith("]") and "[" in expression:
                unit = expression[expression.rfind("[") + 1:-1]
        return unit

    def rename(self, old: str, new: str) -> None:
        self.calls.append(("rename", (old, new)))
        row = self.expressions.pop(old)
        self.expressions[new] = row

    def move(self, *args: Any) -> None:
        self.calls.append(("move", args))

    def clear(self) -> None:
        self.expressions.clear()

    def remove(self, name: str) -> None:
        self.calls.append(("remove", (name,)))
        if "remove" in self.unavailable:
            raise FakeEngineError("SecurityException: METHOD_REJECTED", code="METHOD_REJECTED")
        if name not in self.expressions:
            raise FakeEngineError(f"no such name {name}")
        del self.expressions[name]

    # -- property metadata and typed values --------------------------------
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
        if name in self.readback_overrides:
            return self.readback_overrides[name]
        value_type = self.value_types.get(name)
        getters = {
            "String": ("values",), "StringArray": ("arrays", "values"), "StringMatrix": ("matrices", "values"),
            "Double": ("numbers",), "DoubleArray": ("arrays", "numbers"),
            "DoubleMatrix": ("matrices", "numbers"), "DoubleRowMatrix": ("matrices", "numbers"),
            "Int": ("integers",), "IntArray": ("arrays", "integers"), "IntMatrix": ("matrices", "integers"),
            "Boolean": ("booleans",), "BooleanArray": ("arrays", "booleans"),
            "BooleanMatrix": ("matrices", "booleans"),
        }
        stores = getters.get(value_type, ("values", "numbers", "integers", "booleans", "arrays", "matrices"))
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

    def getDoubleArray(self, name: str) -> Any:
        return self._read(name)

    def getDoubleMatrix(self, name: str) -> Any:
        return self._read(name)

    def getInt(self, name: str) -> Any:
        return self._read(name)

    def getIntArray(self, name: str) -> Any:
        return self._read(name)

    def getIntMatrix(self, name: str) -> Any:
        return self._read(name)

    def getBoolean(self, name: str) -> Any:
        return self._read(name)

    def getBooleanArray(self, name: str) -> Any:
        return self._read(name)

    def getBooleanMatrix(self, name: str) -> Any:
        return self._read(name)

    def set(self, *args: Any) -> None:
        self.calls.append(("set", args))
        if len(args) == 1 and isinstance(args[0], (list, tuple)):
            # Selection.set(int...): the worker converts a JSON list to int[].
            self.set_entities([int(item) for item in args[0]])
            return
        if len(args) == 2 and isinstance(args[1], Mapping):
            name, typed = args
            self._store_typed(name, typed.get("kind"), typed.get("data"))
            return
        if len(args) == 2:
            name, value = args
            metadata = self.value_types.get(name)
            if metadata is None and isinstance(value, str):
                # No property metadata: this is an ExpressionEntity write.
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
        if isinstance(data, bool):
            self.booleans[name] = data
            return
        if isinstance(data, int):
            self.integers[name] = data
            return
        if isinstance(data, float):
            self.numbers[name] = data
            return
        if isinstance(data, (list, tuple)):
            rows = list(data)
            if rows and isinstance(rows[0], (list, tuple)):
                self.matrices[name] = [list(row) for row in rows]
            else:
                self.arrays[name] = list(rows)
            return
        self.values[name] = data

    # -- selections ---------------------------------------------------------
    def selection(self, *args: Any) -> Any:
        self.calls.append(("selection", args))
        if "selection" in self.unavailable:
            raise FakeEngineError("SecurityException: METHOD_REJECTED", code="METHOD_REJECTED")
        collections = self.collections
        if args:
            if "selection" in collections:
                return collections["selection"].get(args[0])
            raise AttributeError("selection")
        if "selection" in collections:
            return collections["selection"]
        return self

    def named(self, *args: Any) -> Any:
        self.calls.append(("named", args))
        if args:
            self.named_ref = args[0]
            return None
        return self.named_ref

    def geom(self, *args: Any) -> Any:
        self.calls.append(("geom", args))
        collections = self.collections
        if not args:
            if "geom" in collections:
                return collections["geom"]
            return self.geometry_tag
        if len(args) == 1 and isinstance(args[0], str) and "geom" in collections:
            return collections["geom"].get(args[0])
        if len(args) == 1:
            self.dim_ = int(args[0])
            return None
        self.geometry_tag = str(args[0])
        self.dim_ = int(args[1])
        return None

    def set_entities(self, entities: list[int]) -> None:
        self.entities_ = list(entities)

    def all(self) -> None:
        self.calls.append(("all", ()))
        self.entities_ = self.measure_entities.get(self.dim_ or 0, [])

    def entities(self, *args: Any) -> list[int]:
        self.calls.append(("entities", args))
        if args:
            return list(self.measure_entities.get(int(args[0]), []))
        if "entities" in self.unavailable:
            raise FakeEngineError("SecurityException: METHOD_REJECTED", code="METHOD_REJECTED")
        return list(self.entities_ or [])

    def inherit(self, *args: Any) -> Any:
        self.calls.append(("inherit", args))
        if args:
            self.inheriting_ = bool(args[0])
            return None
        return self.inheriting_

    def isInheriting(self) -> bool:
        return self.inheriting_

    def dim(self) -> int:
        return self.dim_ if self.dim_ is not None else 0

    def dimension(self) -> list[int]:
        return self.dimension_ or [self.dim()]

    def objects(self) -> list[str]:
        return list(self.objects_)

    def object(self) -> str:
        return self.objects_[0] if self.objects_ else ""

    # -- geometry -----------------------------------------------------------
    def lengthUnit(self) -> str:
        self.calls.append(("lengthUnit", ()))
        if "lengthUnit" in self.unavailable:
            raise FakeEngineError("SecurityException: METHOD_REJECTED", code="METHOD_REJECTED")
        return self.geometry_tag or "m"

    def getSDim(self) -> int:
        return int(self.integers.get("sdim", 3))

    def getAdj(self, from_dim: int, to_dim: int) -> list[list[int]]:
        self.calls.append(("getAdj", (from_dim, to_dim)))
        if "getAdj" in self.unavailable:
            raise FakeEngineError("SecurityException: METHOD_REJECTED", code="METHOD_REJECTED")
        table = self.matrices.get("adjacency")
        if table is None:
            raise FakeEngineError("no adjacency matrix configured")
        return [list(row) for row in table]

    # -- measure ------------------------------------------------------------
    def measure(self, *args: Any) -> Any:
        self.calls.append(("measure", args))
        if "measure" in self.unavailable:
            raise FakeEngineError("SecurityException: METHOD_REJECTED", code="METHOD_REJECTED")
        measure = self.__dict__.get("_measure")
        if measure is None:
            measure = FMeasure(self)
            self.__dict__["_measure"] = measure
        return measure

    def getArea(self) -> float:
        return float(self.measure_metrics["area"])

    def getVolume(self) -> float:
        return float(self.measure_metrics["volume"])

    def getLength(self) -> float:
        return float(self.measure_metrics["length"])

    def getPerimeter(self) -> float:
        return float(self.measure_metrics["perimeter"])

    def getBoundaryArea(self) -> float:
        return float(self.measure_metrics["boundary_area"])

    def getBoundaryVolume(self) -> float:
        return float(self.measure_metrics["boundary_volume"])

    def getBoundingBox(self) -> list[float]:
        return list(self.measure_metrics["bounding_box"])

    def getNEntities(self) -> list[int]:
        return list(self.measure_metrics["n_entities"])

    def getNFiniteVoids(self) -> int:
        return int(self.measure_metrics["finite_voids"])

    def getVtxCoord(self) -> list[float]:
        return list(self.measure_metrics["vtx_coord"])

    def getVtxDistance(self) -> float:
        return float(self.measure_metrics["vtx_distance"])

    def getEdgeAngle(self) -> float:
        return float(self.measure_metrics["edge_angle"])

    # -- functions ----------------------------------------------------------
    def functionNames(self) -> list[str]:
        self.calls.append(("functionNames", ()))
        if "functionNames" in self.unavailable:
            raise FakeEngineError("SecurityException: METHOD_REJECTED", code="METHOD_REJECTED")
        return [str(item) for item in self.arrays.get("function_names", [])]

    def importData(self, *args: Any) -> None:
        self.calls.append(("importData", args))
        if "importData" in self.unavailable:
            raise FakeEngineError("SecurityException: METHOD_REJECTED", code="METHOD_REJECTED")
        self.import_datasets += 1

    def refresh(self) -> None:
        self.calls.append(("refresh", ()))
        if "refresh" in self.unavailable:
            raise FakeEngineError("SecurityException: METHOD_REJECTED", code="METHOD_REJECTED")
        self.refreshes += 1

    def discardData(self) -> None:
        self.calls.append(("discardData", ()))

    # -- expression scope ---------------------------------------------------
    def model(self, *args: Any) -> Any:
        self.calls.append(("model", args))
        if args:
            self.model_ref = str(args[0])
            return None
        return self.model_ref

    def scope(self) -> str:
        return self.model_ref or "global"

    # -- dynamic accessors --------------------------------------------------
    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        collections = self.__dict__.get("collections") or {}
        if name in collections:
            target = collections[name]

            def accessor(tag: Any = None, *args: Any) -> Any:
                self.__dict__["calls"].append((name, (tag,) + args))
                if tag is None:
                    return target
                return target.get(tag)

            return accessor
        if name in (self.__dict__.get("unavailable") or set()):
            raise FakeEngineError(f"SecurityException: METHOD_REJECTED ({name})", code="METHOD_REJECTED")
        errors = self.__dict__.get("errors") or {}
        if name in errors:
            raise errors[name]
        raise AttributeError(name)


class FMeasureSelection:
    """Fake of the ``MeshSelection`` returned by ``component.measure().selection()``."""

    def __init__(self, owner: "FNode") -> None:
        self.owner = owner
        self.dim_value: int | None = None
        self.selected: list[int] = []

    def geom(self, *args: Any) -> Any:
        if args:
            self.dim_value = int(args[0])
            return None
        return self.dim_value

    def set(self, *args: Any) -> None:
        if len(args) == 1 and isinstance(args[0], (list, tuple)):
            self.selected = [int(item) for item in args[0]]
        else:
            self.selected = [int(item) for item in args]

    def all(self) -> None:
        self.selected = list(self.owner.measure_entities.get(self.dim_value or 0, []))

    def entities(self) -> list[int]:
        return list(self.selected)

    def dim(self) -> int:
        return self.dim_value if self.dim_value is not None else 0

    def dimension(self) -> list[int]:
        return [self.dim()]


class FMeasure:
    """Fake of ``GeomMeasureFinal``: a transient measurement tool."""

    def __init__(self, owner: "FNode") -> None:
        self.owner = owner
        self._selection = FMeasureSelection(owner)

    def selection(self, *args: Any) -> FMeasureSelection:
        return self._selection

    def getArea(self) -> float:
        return float(self.owner.measure_metrics["area"])

    def getVolume(self) -> float:
        return float(self.owner.measure_metrics["volume"])

    def getLength(self) -> float:
        return float(self.owner.measure_metrics["length"])

    def getPerimeter(self) -> float:
        return float(self.owner.measure_metrics["perimeter"])

    def getBoundaryArea(self) -> float:
        return float(self.owner.measure_metrics["boundary_area"])

    def getBoundaryVolume(self) -> float:
        return float(self.owner.measure_metrics["boundary_volume"])

    def getBoundingBox(self) -> list[float]:
        return list(self.owner.measure_metrics["bounding_box"])

    def getNEntities(self) -> list[int]:
        return list(self.owner.measure_metrics["n_entities"])

    def getNFiniteVoids(self) -> int:
        return int(self.owner.measure_metrics["finite_voids"])

    def getVtxCoord(self) -> list[float]:
        return list(self.owner.measure_metrics["vtx_coord"])

    def getVtxDistance(self) -> float:
        return float(self.owner.measure_metrics["vtx_distance"])

    def getEdgeAngle(self) -> float:
        return float(self.owner.measure_metrics["edge_angle"])


class FParamCollection(FNode):
    """``model.param()``: expressions of the default group plus the group list."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(tag="param", **kwargs)
        self.groups = FList(arity=1)
        self.groups.items["default"] = FNode(tag="default", type_id="ModelParamGroup")
        self.groups.items["default"].expressions = self.expressions
        self.groups.items["default"].evaluations = self.evaluations

    def group(self, tag: Any = None) -> Any:
        self.calls.append(("group", (tag,)))
        if "group" in self.unavailable:
            raise FakeEngineError("SecurityException: METHOD_REJECTED (group)", code="METHOD_REJECTED")
        return self.groups if tag is None else self.groups.items[tag]

    def get(self, name: str) -> str:
        """``model.param(<ptag>)`` (group node) or an expression value.

        The accessor role and the ExpressionEntity role share one method name in
        the real API (``Model.param(String)`` vs ``ExpressionBase.get(String)``);
        a group tag is therefore resolved through the group list first.
        """
        if name in self.groups.items and name not in self.expressions:
            self.calls.append(("get-group", (name,)))
            return self.groups.items[name]  # type: ignore[return-value]
        return super().get(name)

    def create(self, tag: str) -> FNode:
        self.calls.append(("create", (tag,)))
        if tag in self.groups.items:
            raise FakeEngineError(f"tag {tag} already exists")
        node = FNode(tag=tag, type_id="ModelParamGroup")
        self.groups.items[tag] = node
        return node

    def move(self, *args: Any) -> None:
        self.calls.append(("move", args))
        names, target = args[0], args[1]
        if isinstance(names, str):
            names = [names]
        for name in names:
            row = self.expressions.pop(name)
            self.groups.items[target].expressions[name] = row

    @property
    def tags_(self) -> list[str]:
        return list(self.groups.items)


class FModel(FNode):
    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("type_id", "Model")
        super().__init__(tag="Model", **kwargs)


class FClient:
    def __init__(self, models: Mapping[str, FNode]) -> None:
        self.models = dict(models)

    def model(self, tag: str) -> FNode:
        if tag not in self.models:
            raise KeyError(tag)
        return self.models[tag]


class FWorker:
    """Fake PersistentJavaWorker: only ``client().model(tag)`` is used."""

    def __init__(self, model: FNode, *, generation: int = 11, model_tag: str = "Model") -> None:
        self.generation = generation
        self._client = FClient({model_tag: model})

    def client(self) -> FClient:
        return self._client


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def worker_for(model: FNode, **kwargs: Any) -> FWorker:
    return FWorker(model, **kwargs)


def expect_error(code: str, function: Any, *args: Any, **kwargs: Any) -> ExecutionContractError:
    with pytest.raises(ExecutionContractError) as info:
        function(*args, **kwargs)
    assert info.value.code == code, f"expected {code}, got {info.value.code}: {info.value}"
    return info.value


def selection_node(**kwargs: Any) -> FNode:
    kwargs.setdefault("type_id", "Explicit")
    return FNode(**kwargs)


def component_with(*, selections: Mapping[str, FNode] | None = None, geometry: FNode | None = None,
                   variables: Mapping[str, FNode] | None = None,
                   functions: Mapping[str, FNode] | None = None) -> tuple[FModel, FNode]:
    geom_list = FList(arity=2, node_type="GeomSequence")
    if geometry is not None:
        geom_list.items[geometry.tag_] = geometry
    selection_list = FList(arity=2, node_type="SelectionFeature")
    for tag, node in (selections or {}).items():
        selection_list.items[tag] = node
    variable_list = FList(arity=1, node_type="Expr")
    for tag, node in (variables or {}).items():
        variable_list.items[tag] = node
    function_list = FList(arity=2, node_type="FunctionFeature")
    for tag, node in (functions or {}).items():
        function_list.items[tag] = node
    component = FNode(tag="comp1", type_id="Component", collections={
        "geom": geom_list, "selection": selection_list, "variable": variable_list, "func": function_list,
    })
    component.measure_metrics = {
        "area": 0.25, "volume": 1.0e-6, "length": 0.5, "perimeter": 2.0, "boundary_area": 0.75,
        "boundary_volume": 2.5e-3, "bounding_box": [0.0, 1.0, 0.0, 1.0, 0.0, 1.0],
        "n_entities": [8, 12, 6, 1], "finite_voids": 0, "vtx_coord": [0.5, 0.5, 0.0],
        "vtx_distance": 0.25, "edge_angle": 1.5707963267948966,
    }
    component.measure_entities = {2: [1, 2, 3], 1: [1, 2, 3, 4], 0: [1, 2, 3, 4, 5]}
    component.geometry_tag = geom_list.items and list(geom_list.items)[0] or "geom1"
    model = FModel(collections={"component": FList(arity=1, node_type="Component"),
                                "param": None, "func": FList(arity=2, node_type="FunctionFeature"),
                                "selection": FList(arity=2, node_type="SelectionFeature"),
                                "variable": FList(arity=1, node_type="Expr")})
    model.collections["component"].items["comp1"] = component
    return model, component


# ---------------------------------------------------------------------------
# aggregator surface
# ---------------------------------------------------------------------------


class TestAggregator:
    def test_all_w13_operations_are_published(self):
        expected = {
            "parameter.list", "parameter.get", "parameter.set", "parameter.remove", "parameter.group_manage",
            "variable.list", "variable.get", "variable.set", "variable.remove", "variable.group_create",
            "variable.selection_set",
            "function.list", "function.create", "function.inspect", "function.update", "function.remove",
            "function.data_import", "function.data_reload", "function.evaluate",
            "selection.list", "selection.create", "selection.inspect", "selection.update", "selection.remove",
            "selection.entities", "selection.measure", "selection.query_spatial", "selection.adjacency",
            "selection.validate",
        }
        assert expected == set(w13.OPERATIONS)
        assert expected <= IMPLEMENTED_OPERATIONS
        assert expected <= set(DISPATCH)

    def test_isolation_set_is_exactly_the_non_read_effects(self):
        for operation_id, effect in EFFECTS.items():
            if effect == "READ":
                assert operation_id not in REQUIRES_ISOLATION
            else:
                assert operation_id in REQUIRES_ISOLATION

    def test_effects_come_from_the_design_catalog(self):
        assert EFFECTS["parameter.list"] == "READ"
        assert EFFECTS["variable.set"] == "WRITE"
        assert EFFECTS["function.evaluate"] == "EVALUATE"
        assert EFFECTS["selection.query_spatial"] == "EVALUATE"


# ---------------------------------------------------------------------------
# parameter domain
# ---------------------------------------------------------------------------


def param_model(*, unavailable: tuple[str, ...] = (), groups: Mapping[str, Mapping[str, Any]] | None = None,
                default: Mapping[str, Mapping[str, Any]] | None = None) -> tuple[FModel, FParamCollection]:
    collection = FParamCollection(expressions=default or {}, unavailable=unavailable,
                                  value_types={"entitydim": "Int"}, allowed={"entitydim": ["1", "2", "3"]})
    for tag, rows in (groups or {}).items():
        node = FNode(tag=tag, type_id="ModelParamGroup", expressions=rows)
        collection.groups.items[tag] = node
    model = FModel(collections={"component": FList(arity=1), "param": collection,
                                "func": FList(arity=2), "selection": FList(arity=2),
                                "variable": FList(arity=1)})
    model.collections["component"].items["comp1"] = FNode(tag="comp1", collections={
        "geom": FList(arity=2), "selection": FList(arity=2), "variable": FList(arity=1), "func": FList(arity=2)})
    return model, collection


def rows(**kwargs: Any) -> dict[str, dict[str, Any]]:
    return {name: {"expression": value, "description": None, "unit": None} for name, value in kwargs.items()}


class TestParameterList:
    def test_lists_groups_with_expressions_descriptions_and_units(self):
        model, _ = param_model(default=rows(a="1[mm]"), groups={
            "phys": {"T0": {"expression": "293.15[K]", "description": "ambient", "unit": "K"}}})
        result = w13.parameter_list(worker_for(model), "Model", {})
        assert result["group_count"] == 2
        assert result["parameter_count"] == 2
        assert result["group_attribution"] == "model.param().group().tags()"
        by_group = {row["group"]: row for row in result["groups"]}
        assert by_group["phys"]["parameters"][0] == {
            "name": "T0", "expression": "293.15[K]", "description": "ambient",
            "description_error": None, "unit": "K", "unit_error": None,
        }

    def test_single_group_filter(self):
        model, _ = param_model(groups={"phys": rows(T0="293.15[K]")})
        result = w13.parameter_list(worker_for(model), "Model", {"group": "phys"})
        assert [row["group"] for row in result["groups"]] == ["phys"]

    def test_missing_group_is_rejected(self):
        model, _ = param_model()
        expect_error("NODE_NOT_FOUND", w13.parameter_list, worker_for(model), "Model", {"group": "nope"})

    def test_group_accessor_unavailable_falls_back_and_reports_it(self):
        model, _ = param_model(unavailable=("group",), default=rows(a="1"))
        result = w13.parameter_list(worker_for(model), "Model", {})
        assert result["group_attribution"] == "top_level_collection_fallback"
        assert result["group_probe_error"]["allowlist_entry_required"] == "group"
        assert result["groups"][0]["readback_scope"] == "model.param()"
        assert result["parameter_count"] == 1

    def test_unknown_field_is_rejected(self):
        model, _ = param_model()
        expect_error("INVALID_REQUEST", w13.parameter_list, worker_for(model), "Model", {"nope": 1})


class TestParameterGet:
    def test_reads_value_description_and_unit_per_group(self):
        model, _ = param_model(default=rows(a="1[mm]"), groups={
            "phys": {"T0": {"expression": "293.15[K]", "description": "ambient", "unit": "K"}}})
        result = w13.parameter_get(worker_for(model), "Model", {"names": ["T0"]})
        row = result["parameters"][0]
        assert row["name"] == "T0" and row["group"] == "phys" and row["unit"] == "K"

    def test_evaluate_returns_typed_scalar(self):
        model, _ = param_model(default={"a": {"expression": "2", "description": None, "unit": "1"}})
        model.collections["param"].evaluations["a"] = 2.0
        result = w13.parameter_get(worker_for(model), "Model", {"names": ["a"], "evaluate": True})
        assert result["parameters"][0]["evaluated"] == {"kind": "float64", "shape": [], "data": 2.0}

    def test_missing_parameter_name(self):
        model, _ = param_model()
        expect_error("NAME_NOT_FOUND", w13.parameter_get, worker_for(model), "Model", {"names": ["nothing"]})

    def test_names_is_required(self):
        model, _ = param_model()
        expect_error("INVALID_REQUEST", w13.parameter_get, worker_for(model), "Model", {})

    def test_evaluate_must_be_boolean(self):
        model, _ = param_model()
        expect_error("INVALID_REQUEST", w13.parameter_get, worker_for(model), "Model",
                     {"names": ["a"], "evaluate": "yes"})


class TestParameterSet:
    def test_applies_expression_description_and_verified_unit(self):
        model, collection = param_model()
        result = w13.parameter_set(worker_for(model), "Model", {"parameters": [
            {"name": "L", "expression": "5[mm]", "description": "length", "unit": "mm"}]})
        assert result["ok"] is True and result["status"] == "APPLIED"
        assert collection.expressions["L"]["expression"] == "5[mm]"
        assert collection.expressions["L"]["description"] == "length"
        assert result["applied"][0]["unit_verified"] is True

    def test_declared_unit_conflict_is_refused_before_the_write(self):
        model, collection = param_model(default={"L": {"expression": "1[mm]", "description": None, "unit": "mm"}})
        result = w13.parameter_set(worker_for(model), "Model", {"parameters": [
            {"name": "L", "expression": "5", "unit": "m"}]})
        assert result["ok"] is False and result["status"] == "FAILED"
        assert collection.expressions["L"]["expression"] == "1[mm]"
        assert result["failed"][0]["error"]["code"] == "PROPERTY_TYPE_MISMATCH"
        assert result["not_executed"] == []

    def test_post_write_unit_mismatch_is_reported_without_conversion(self):
        model, _ = param_model()
        # The expression carries no unit, so evaluateUnit() cannot confirm the
        # declared unit after the write: reported, never converted.
        result = w13.parameter_set(worker_for(model), "Model", {"parameters": [
            {"name": "L", "expression": "2", "unit": "mm"}]})
        assert result["ok"] is False and result["status"] == "PARTIAL_FAILURE"
        assert result["applied"][0]["unit_verified"] is False
        assert result["applied"][0]["unit_requested"] == "mm"
        assert result["failed"][0]["name"] == "L"
        assert result["partial_change"] is True

    def test_expression_readback_mismatch_is_execution_state_unknown(self):
        model, collection = param_model()
        collection.expression_overrides["L"] = "3[mm]"
        result = w13.parameter_set(worker_for(model), "Model", {"parameters": [
            {"name": "L", "expression": "2[mm]"}]})
        assert result["status"] == "EXECUTION_STATE_UNKNOWN"
        assert result["execution_state_unknown"] is True
        assert result["failed"][0]["execution_state_unknown"] is True

    def test_group_target_requires_the_group_accessor(self):
        model, _ = param_model(unavailable=("group",))
        expect_error("API_UNSUPPORTED", w13.parameter_set, worker_for(model), "Model",
                     {"parameters": [{"name": "L", "expression": "1"}], "group": "phys"})

    def test_group_target_must_exist(self):
        model, _ = param_model()
        expect_error("NODE_NOT_FOUND", w13.parameter_set, worker_for(model), "Model",
                     {"parameters": [{"name": "L", "expression": "1"}], "group": "phys"})

    def test_duplicate_names_are_rejected(self):
        model, _ = param_model()
        expect_error("INVALID_REQUEST", w13.parameter_set, worker_for(model), "Model", {"parameters": [
            {"name": "L", "expression": "1"}, {"name": "L", "expression": "2"}]})

    def test_unknown_field_is_rejected(self):
        model, _ = param_model()
        expect_error("INVALID_REQUEST", w13.parameter_set, worker_for(model), "Model", {"parameters": [
            {"name": "L", "expression": "1", "value": "2"}]})


class TestParameterRemove:
    def test_removes_and_reports_reference_risk(self):
        model, collection = param_model(default=rows(a="1", b="2"))
        result = w13.parameter_remove(worker_for(model), "Model", {"names": ["a"]})
        assert result["ok"] is True
        assert list(collection.expressions) == ["b"]
        assert result["reference_risk"]["level"] == "UNSCANNED"

    def test_missing_name_stops_and_reports_not_executed(self):
        model, collection = param_model(default=rows(a="1"))
        result = w13.parameter_remove(worker_for(model), "Model", {"names": ["a", "b", "c"]})
        assert result["ok"] is False
        assert result["failed"][0]["name"] == "b"
        assert result["failed"][0]["error"]["code"] == "NAME_NOT_FOUND"
        assert result["not_executed"] == ["c"]
        assert "a" not in collection.expressions
        assert result["reference_risk"]["level"] == "UNSCANNED"


class TestParameterGroupManage:
    def test_create_group(self):
        model, collection = param_model()
        result = w13.parameter_group_manage(worker_for(model), "Model",
                                            {"action": "create", "tag": "phys"})
        assert result["created"] is True and result["tag"] == "phys"
        assert "phys" in collection.groups.items

    def test_create_existing_group_reports_tag_conflict(self):
        model, _ = param_model(groups={"phys": rows(T0="1")})
        expect_error("TAG_CONFLICT", w13.parameter_group_manage, worker_for(model), "Model",
                     {"action": "create", "tag": "phys"})

    def test_reserved_group_tag_cannot_be_created(self):
        model, _ = param_model()
        expect_error("INVALID_REQUEST", w13.parameter_group_manage, worker_for(model), "Model",
                     {"action": "create", "tag": "default"})

    def test_reserved_default_group_cannot_be_deleted(self):
        model, _ = param_model()
        expect_error("INVALID_REQUEST", w13.parameter_group_manage, worker_for(model), "Model",
                     {"action": "delete", "tag": "default"})

    def test_delete_group(self):
        model, collection = param_model(groups={"phys": rows(T0="1")})
        result = w13.parameter_group_manage(worker_for(model), "Model", {"action": "delete", "tag": "phys"})
        assert result["removed"] is True
        assert "phys" not in collection.groups.items

    def test_rename_parameter_uses_the_documented_api(self):
        model, collection = param_model(default=rows(a="1"))
        result = w13.parameter_group_manage(worker_for(model), "Model",
                                            {"action": "rename", "tag": "a", "arguments": {"new_name": "b"}})
        assert result["renamed"] is True and "b" in collection.expressions

    def test_rename_requires_the_new_name(self):
        model, _ = param_model(default=rows(a="1"))
        expect_error("INVALID_REQUEST", w13.parameter_group_manage, worker_for(model), "Model",
                     {"action": "rename", "tag": "a"})

    def test_move_parameters_between_groups(self):
        model, collection = param_model(default=rows(a="1", b="2"), groups={"phys": {}})
        result = w13.parameter_group_manage(worker_for(model), "Model",
                                            {"action": "move", "tag": "phys", "arguments": {"names": ["a"]}})
        assert result["moved"] == ["a"]
        assert "a" in collection.groups.items["phys"].expressions
        assert "a" not in collection.expressions

    def test_move_requires_a_verifiable_group_list(self):
        model, _ = param_model(unavailable=("group",))
        expect_error("API_UNSUPPORTED", w13.parameter_group_manage, worker_for(model), "Model",
                     {"action": "move", "tag": "phys", "arguments": {"names": ["a"]}})

    def test_unknown_action_is_refused(self):
        model, _ = param_model()
        expect_error("API_UNSUPPORTED", w13.parameter_group_manage, worker_for(model), "Model",
                     {"action": "rename_group", "tag": "phys"})

    def test_invalid_tag_is_refused(self):
        model, _ = param_model()
        expect_error("INVALID_REQUEST", w13.parameter_group_manage, worker_for(model), "Model",
                     {"action": "create", "tag": "1bad tag"})


# ---------------------------------------------------------------------------
# variable domain
# ---------------------------------------------------------------------------


def variable_model(*, global_vars: Mapping[str, FNode] | None = None,
                   component_vars: Mapping[str, FNode] | None = None,
                   selections: Mapping[str, FNode] | None = None) -> tuple[FModel, FNode]:
    model, component = component_with(selections=selections, variables=component_vars, geometry=FNode(
        tag="geom1", integers={"sdim": 2}))
    if global_vars:
        for tag, node in global_vars.items():
            model.collections["variable"].items[tag] = node
    return model, component


class TestVariableList:
    def test_lists_global_groups(self):
        group = FNode(tag="var1", expressions={"X": {"expression": "1", "description": None, "unit": None}})
        model, _ = variable_model(global_vars={"var1": group})
        result = w13.variable_list(worker_for(model), "Model", {})
        assert result["scope"] == "global"
        assert result["groups"][0]["group"] == "var1"
        assert result["groups"][0]["variables"][0]["name"] == "X"

    def test_lists_component_groups(self):
        group = FNode(tag="var1", expressions={"X": {"expression": "1", "description": None, "unit": None}})
        model, _ = variable_model(component_vars={"var1": group})
        result = w13.variable_list(worker_for(model), "Model", {"component": "comp1"})
        assert result["scope"] == "comp1"
        assert result["groups"][0]["path"]["segments"][-1] == {"collection": "variable", "tag": "var1"}

    def test_unknown_component(self):
        model, _ = variable_model()
        expect_error("NODE_NOT_FOUND", w13.variable_list, worker_for(model), "Model", {"component": "nope"})


class TestVariableGet:
    def test_reads_requested_names(self):
        group = FNode(tag="var1", expressions={
            "X": {"expression": "x+1", "description": "shift", "unit": None},
            "Y": {"expression": "2", "description": None, "unit": None}})
        model, _ = variable_model(global_vars={"var1": group})
        path = {"segments": [{"collection": "variable", "tag": "var1"}]}
        result = w13.variable_get(worker_for(model), "Model", {"group": path, "names": ["X"]})
        assert result["count"] == 1 and result["variables"][0]["expression"] == "x+1"

    def test_missing_name(self):
        group = FNode(tag="var1", expressions={"X": {"expression": "1", "description": None, "unit": None}})
        model, _ = variable_model(global_vars={"var1": group})
        path = {"segments": [{"collection": "variable", "tag": "var1"}]}
        expect_error("NAME_NOT_FOUND", w13.variable_get, worker_for(model), "Model",
                     {"group": path, "names": ["Z"]})

    def test_group_is_required(self):
        model, _ = variable_model()
        expect_error("INVALID_REQUEST", w13.variable_get, worker_for(model), "Model", {})

    def test_bad_path_is_rejected(self):
        model, _ = variable_model()
        expect_error("INVALID_NODE_PATH", w13.variable_get, worker_for(model), "Model",
                     {"group": {"segments": [{"collection": "nope", "tag": "x"}]}})


class TestVariableSet:
    def test_sets_multiple_variables_in_one_group(self):
        group = FNode(tag="var1", expressions={})
        model, _ = variable_model(global_vars={"var1": group})
        path = {"segments": [{"collection": "variable", "tag": "var1"}]}
        result = w13.variable_set(worker_for(model), "Model", {"group": path, "variables": [
            {"name": "X", "expression": "x+1"}, {"name": "Y", "expression": "2*X", "description": "twice"}]})
        assert result["ok"] is True
        assert group.expressions["Y"]["description"] == "twice"
        assert result["varnames"] == ["X", "Y"]

    def test_readback_mismatch_reports_unknown_state(self):
        group = FNode(tag="var1", expressions={}, expression_overrides={"X": "other"})
        model, _ = variable_model(global_vars={"var1": group})
        path = {"segments": [{"collection": "variable", "tag": "var1"}]}
        result = w13.variable_set(worker_for(model), "Model", {"group": path, "variables": [
            {"name": "X", "expression": "x+1"}]})
        assert result["status"] == "EXECUTION_STATE_UNKNOWN"

    def test_duplicate_names_are_rejected(self):
        group = FNode(tag="var1", expressions={})
        model, _ = variable_model(global_vars={"var1": group})
        path = {"segments": [{"collection": "variable", "tag": "var1"}]}
        expect_error("INVALID_REQUEST", w13.variable_set, worker_for(model), "Model", {"group": path, "variables": [
            {"name": "X", "expression": "1"}, {"name": "X", "expression": "2"}]})

    def test_empty_variable_list_is_rejected(self):
        group = FNode(tag="var1", expressions={})
        model, _ = variable_model(global_vars={"var1": group})
        path = {"segments": [{"collection": "variable", "tag": "var1"}]}
        expect_error("INVALID_REQUEST", w13.variable_set, worker_for(model), "Model",
                     {"group": path, "variables": []})


class TestVariableRemove:
    def test_removes_names(self):
        group = FNode(tag="var1", expressions={"X": {"expression": "1", "description": None, "unit": None}})
        model, _ = variable_model(global_vars={"var1": group})
        path = {"segments": [{"collection": "variable", "tag": "var1"}]}
        result = w13.variable_remove(worker_for(model), "Model", {"group": path, "names": ["X"]})
        assert result["ok"] is True and result["varnames"] == []

    def test_whole_group_removal(self):
        group = FNode(tag="var1", expressions={})
        model, _ = variable_model(global_vars={"var1": group})
        path = {"segments": [{"collection": "variable", "tag": "var1"}]}
        result = w13.variable_remove(worker_for(model), "Model", {"group": path, "whole_group": True})
        assert result["removed"] is True
        assert "var1" not in model.collections["variable"].items

    def test_names_and_whole_group_conflict(self):
        group = FNode(tag="var1", expressions={})
        model, _ = variable_model(global_vars={"var1": group})
        path = {"segments": [{"collection": "variable", "tag": "var1"}]}
        expect_error("INVALID_REQUEST", w13.variable_remove, worker_for(model), "Model",
                     {"group": path, "whole_group": True, "names": ["X"]})

    def test_names_required_without_whole_group(self):
        group = FNode(tag="var1", expressions={})
        model, _ = variable_model(global_vars={"var1": group})
        path = {"segments": [{"collection": "variable", "tag": "var1"}]}
        expect_error("INVALID_REQUEST", w13.variable_remove, worker_for(model), "Model", {"group": path})


class TestVariableGroupCreate:
    def test_creates_component_scoped_group_with_named_selection(self):
        named = selection_node(tag="sel1", type_id="Explicit")
        model, component = variable_model(selections={"sel1": named})
        result = w13.variable_group_create(worker_for(model), "Model", {
            "tag": "var1", "component": "comp1",
            "selection": {"kind": "named", "component": "comp1", "tag": "sel1"}})
        assert result["created"] is True and result["scope"] == "comp1"
        assert result["selection"]["applied"][0]["readback"] == "sel1"
        node = component.collections["variable"].items["var1"]
        assert node.named_ref == "sel1"

    def test_creates_global_group_and_binds_the_specs_component(self):
        named = selection_node(tag="sel1", type_id="Explicit")
        model, _ = variable_model(selections={"sel1": named})
        result = w13.variable_group_create(worker_for(model), "Model", {
            "tag": "var9", "selection": {"kind": "named", "component": "comp1", "tag": "sel1"}})
        assert result["scope"] == "global"
        node = model.collections["variable"].items["var9"]
        assert node.named_ref == "sel1"
        assert node.model_ref == "comp1"
        assert result["selection"]["model_binding"]["readback"] == "comp1"

    def test_creates_component_group(self):
        model, _ = variable_model()
        result = w13.variable_group_create(worker_for(model), "Model", {"tag": "var2", "component": "comp1"})
        assert result["scope"] == "comp1"
        assert "var2" in model.collections["component"].items["comp1"].collections["variable"].items

    def test_existing_tag_reports_conflict(self):
        group = FNode(tag="var1", expressions={})
        model, _ = variable_model(global_vars={"var1": group})
        expect_error("TAG_CONFLICT", w13.variable_group_create, worker_for(model), "Model", {"tag": "var1"})

    def test_explicit_selection_on_global_group_needs_a_component(self):
        model, _ = variable_model()
        expect_error("INVALID_REQUEST", w13.variable_group_create, worker_for(model), "Model", {
            "tag": "var1", "selection": {"kind": "explicit", "entities": [1], "entity_dimension": 2}})

    def test_explicit_selection_binds_geometry_and_entities(self):
        model, component = variable_model()
        result = w13.variable_group_create(worker_for(model), "Model", {
            "tag": "var1", "component": "comp1",
            "selection": {"kind": "explicit", "geometry": "geom1", "entity_dimension": 2, "entities": [1, 2]}})
        node = model.collections["component"].items["comp1"].collections["variable"].items["var1"]
        assert node.entities_ == [1, 2]
        assert node.model_ref == "comp1"
        assert result["selection"]["applied"][-1]["readback"] == [1, 2]


class TestVariableSelectionSet:
    def path(self) -> dict[str, Any]:
        return {"segments": [{"collection": "variable", "tag": "var1"}]}

    def test_named_selection(self):
        group = FNode(tag="var1", expressions={})
        named = selection_node(tag="sel1")
        model, _ = variable_model(global_vars={"var1": group}, selections={"sel1": named})
        result = w13.variable_selection_set(worker_for(model), "Model", {
            "group": self.path(), "selection": {"kind": "named", "component": "comp1", "tag": "sel1"}})
        assert result["ok"] is True and group.named_ref == "sel1"

    def test_spatial_selection_is_refused(self):
        group = FNode(tag="var1", expressions={})
        model, _ = variable_model(global_vars={"var1": group})
        expect_error("API_UNSUPPORTED", w13.variable_selection_set, worker_for(model), "Model", {
            "group": self.path(),
            "selection": {"kind": "spatial", "component": "comp1", "geometry": "geom1",
                          "entity_dimension": 2, "query": {"kind": "box"}}})

    def test_inherited_selection_sets_the_flag(self):
        group = FNode(tag="var1", expressions={})
        model, _ = variable_model(global_vars={"var1": group})
        result = w13.variable_selection_set(worker_for(model), "Model", {
            "group": self.path(), "selection": {"kind": "inherited", "component": "comp1"}})
        assert group.inheriting_ is True
        assert result["entity_state"]["is_inheriting"] is True

    def test_all_kind_uses_geometry_and_all(self):
        group = FNode(tag="var1", expressions={})
        group.measure_entities = {2: [1, 2, 3]}
        model, _ = variable_model(global_vars={"var1": group})
        result = w13.variable_selection_set(worker_for(model), "Model", {
            "group": self.path(),
            "selection": {"kind": "all", "component": "comp1", "geometry": "geom1", "entity_dimension": 2}})
        assert group.entities_ == [1, 2, 3]
        assert result["entity_state"]["entities"] == [1, 2, 3]

    def test_selection_is_required(self):
        group = FNode(tag="var1", expressions={})
        model, _ = variable_model(global_vars={"var1": group})
        expect_error("INVALID_REQUEST", w13.variable_selection_set, worker_for(model), "Model",
                     {"group": self.path()})


# ---------------------------------------------------------------------------
# function domain
# ---------------------------------------------------------------------------


def function_node(tag: str = "int1", *, type_id: str = "Interpolation", **kwargs: Any) -> FNode:
    kwargs.setdefault("values", {})
    return FNode(tag=tag, type_id=type_id, **kwargs)


def function_factory(tag: str, type_id: str) -> FNode:
    """Nodes created through ``create(tag, type)`` carry the engine's metadata."""
    metadata, allowed = {
        "Interpolation": (INTERPOLATION_META, INTERPOLATION_ALLOWED),
        "Analytic": ({"expr": "String", "args": "StringArray", "descr": "String"}, {}),
    }.get(type_id, ({}, {}))
    return FNode(tag=tag, type_id=type_id, value_types=dict(metadata), allowed=dict(allowed))


def function_model(functions: Mapping[str, FNode] | None = None) -> tuple[FModel, FNode]:
    model, component = component_with(geometry=FNode(tag="geom1", integers={"sdim": 3}))
    model.collections["func"].node_factory = function_factory
    for tag, node in (functions or {}).items():
        model.collections["func"].items[tag] = node
    return model, component


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


class TestFunctionList:
    def test_lists_global_functions_with_type_and_names(self):
        node = function_node(values={"funcname": "f"})
        node.arrays["function_names"] = ["f"]
        model, _ = function_model()
        model.collections["func"].items["int1"] = node
        result = w13.function_list(worker_for(model), "Model", {})
        assert result["count"] == 1
        assert result["functions"][0]["type_id"] == "Interpolation"
        assert result["functions"][0]["function_names"] == ["f"]

    def test_function_names_unavailable_is_reported_not_hidden(self):
        node = function_node(unavailable=("functionNames",))
        model, _ = function_model()
        model.collections["func"].items["int1"] = node
        result = w13.function_list(worker_for(model), "Model", {})
        assert result["functions"][0]["function_names"] is None
        assert result["functions"][0]["function_names_error"]["allowlist_entry_required"] == "functionNames"


class TestFunctionCreate:
    def test_creates_an_interpolation_function_and_verifies_properties(self):
        model, _ = function_model()
        result = w13.function_create(worker_for(model), "Model", {
            "tag": "int1", "type_id": "Interpolation",
            "definition": {"funcname": "f", "struct": "grid", "nargs": 2, "argunit": "m,m", "fununit": "W/m^2"}})
        assert result["ok"] is True and result["status"] == "APPLIED"
        assert result["type_readback"] == "Interpolation"
        node = model.collections["func"].items["int1"]
        assert node.values["funcname"] == "f"
        assert node.integers["nargs"] == 2
        assert result["properties"]["fununit"]["data"] == "W/m^2"

    def test_nested_properties_wrapper_is_supported(self):
        model, _ = function_model()
        result = w13.function_create(worker_for(model), "Model", {
            "tag": "an1", "type_id": "Analytic",
            "definition": {"properties": {"expr": "sin(x)", "args": ["x"]}}})
        assert result["ok"] is True
        assert model.collections["func"].items["an1"].values["expr"] == "sin(x)"

    def test_unknown_type_is_refused(self):
        model, _ = function_model()
        expect_error("API_UNSUPPORTED", w13.function_create, worker_for(model), "Model", {
            "tag": "f1", "type_id": "NotAFunction", "definition": {}})

    def test_property_outside_the_documented_table_is_refused(self):
        model, _ = function_model()
        expect_error("INVALID_REQUEST", w13.function_create, worker_for(model), "Model", {
            "tag": "int1", "type_id": "Interpolation", "definition": {"posx": 1.0}})

    def test_type_without_a_retrieved_property_table_refuses_properties(self):
        model, _ = function_model()
        expect_error("API_UNSUPPORTED", w13.function_create, worker_for(model), "Model", {
            "tag": "dnn1", "type_id": "DNN", "definition": {"filename": "x.txt"}})

    def test_property_kind_mismatch_is_refused_before_the_write(self):
        model, _ = function_model()
        expect_error("PROPERTY_TYPE_MISMATCH", w13.function_create, worker_for(model), "Model", {
            "tag": "int1", "type_id": "Interpolation", "definition": {"nargs": "two"}})

    def test_property_without_engine_metadata_is_refused(self):
        model, _ = function_model()
        # ``adaptol`` is in the documented Interpolation table but the fake engine
        # exposes no value metadata for it, so the write must be refused.
        expect_error("API_UNSUPPORTED", w13.function_create, worker_for(model), "Model", {
            "tag": "int1", "type_id": "Interpolation", "definition": {"adaptol": 1e-3}})

    def test_existing_tag_reports_conflict(self):
        model, _ = function_model(functions={"int1": function_node()})
        expect_error("TAG_CONFLICT", w13.function_create, worker_for(model), "Model", {
            "tag": "int1", "type_id": "Interpolation", "definition": {}})

    def test_definition_is_required(self):
        model, _ = function_model()
        expect_error("INVALID_REQUEST", w13.function_create, worker_for(model), "Model",
                     {"tag": "int1", "type_id": "Interpolation"})


class TestFunctionInspect:
    def test_reads_type_names_and_documented_properties(self):
        node = function_node(values={"funcname": "f", "filename": "data.txt"},
                             value_types=dict(INTERPOLATION_META))
        node.arrays["function_names"] = ["f"]
        model, _ = function_model(functions={"int1": node})
        result = w13.function_inspect(worker_for(model), "Model", {
            "path": {"segments": [{"collection": "func", "tag": "int1"}]}})
        assert result["type_id"] == "Interpolation"
        assert result["function_names"] == ["f"]
        assert result["properties"]["filename"]["value"] == "data.txt"
        assert result["property_metadata"]["nargs"]["kind"] == "int32"

    def test_missing_node(self):
        model, _ = function_model()
        expect_error("NODE_NOT_FOUND", w13.function_inspect, worker_for(model), "Model",
                     {"path": {"segments": [{"collection": "func", "tag": "nope"}]}})


class TestFunctionUpdate:
    def test_updates_a_documented_property(self):
        node = function_node(values={"funcname": "f"}, value_types=dict(INTERPOLATION_META),
                             allowed=dict(INTERPOLATION_ALLOWED))
        model, _ = function_model(functions={"int1": node})
        result = w13.function_update(worker_for(model), "Model", {
            "path": {"segments": [{"collection": "func", "tag": "int1"}]},
            "definition": {"interp": "cubicspline"}})
        assert result["ok"] is True
        assert node.values["interp"] == "cubicspline"

    def test_confirming_store_never_reads_back_a_different_kind(self):
        """An int property that reads back as a string is a reported mismatch, not a silent success."""
        node = function_node(values={"nargs": 2}, value_types=dict(INTERPOLATION_META),
                             readback_overrides={"nargs": "2"})
        model, _ = function_model(functions={"int1": node})
        result = w13.function_update(worker_for(model), "Model", {
            "path": {"segments": [{"collection": "func", "tag": "int1"}]},
            "definition": {"nargs": 2}})
        assert result["ok"] is False
        assert result["failed"][0]["name"] == "nargs"
        assert result["failed"][0]["comparison"]["matched"] is False
        assert result["failed"][0]["execution_state_unknown"] is True

    def test_refuses_a_property_outside_the_table(self):
        node = function_node(value_types=dict(INTERPOLATION_META))
        model, _ = function_model(functions={"int1": node})
        expect_error("INVALID_REQUEST", w13.function_update, worker_for(model), "Model", {
            "path": {"segments": [{"collection": "func", "tag": "int1"}]},
            "definition": {"posx": 1.0}})


class TestFunctionRemove:
    def test_removes_and_reports_reference_risk(self):
        model, _ = function_model(functions={"int1": function_node()})
        result = w13.function_remove(worker_for(model), "Model", {
            "path": {"segments": [{"collection": "func", "tag": "int1"}]}})
        assert result["removed"] is True
        assert "int1" not in model.collections["func"].items
        assert result["reference_risk"]["level"] == "UNSCANNED"

    def test_missing_function(self):
        model, _ = function_model()
        expect_error("NODE_NOT_FOUND", w13.function_remove, worker_for(model), "Model",
                     {"path": {"segments": [{"collection": "func", "tag": "nope"}]}})


class TestFunctionDataImport:
    def test_requires_an_engine_visible_path(self):
        model, _ = function_model(functions={"int1": function_node()})
        expect_error("ARTIFACT_MISSING", w13.function_data_import, worker_for(model), "Model", {
            "path": {"segments": [{"collection": "func", "tag": "int1"}]},
            "artifact_id": "art-1", "layout": {"struct": "grid"}, "units": {}})

    def test_imports_with_verified_layout_and_units_and_reports_import_call(self, tmp_path):
        node = function_node(value_types=dict(INTERPOLATION_META), allowed=dict(INTERPOLATION_ALLOWED))
        model, _ = function_model(functions={"int1": node})
        data = tmp_path / "grid.txt"
        data.write_text("% x y f\n0 0 1\n")
        result = w13.function_data_import(worker_for(model), "Model", {
            "path": {"segments": [{"collection": "func", "tag": "int1"}]},
            "artifact_id": "art-1",
            "layout": {"file_path": str(data), "struct": "grid", "nargs": 2, "dseparator": "point"},
            "units": {"argunit": "m,m", "fununit": "W/m^2"}})
        assert result["ok"] is True
        assert result["import_data"]["ok"] is True
        assert node.import_datasets == 1
        assert node.values["filename"] == str(data)
        assert result["artifact"]["sha256"]["status"] == "verified"
        assert result["applied_count"] >= 6

    def test_import_reports_unavailable_local_hash(self):
        node = function_node(value_types=dict(INTERPOLATION_META))
        model, _ = function_model(functions={"int1": node})
        result = w13.function_data_import(worker_for(model), "Model", {
            "path": {"segments": [{"collection": "func", "tag": "int1"}]},
            "artifact_id": "art-1",
            "layout": {"file_path": "/nonexistent/remote/data.txt", "struct": "grid"}, "units": {}})
        assert result["artifact"]["sha256"]["status"] == "unavailable"
        assert result["import_data"]["ok"] is True

    def test_invalid_layout_vocabulary_is_refused(self):
        node = function_node(value_types=dict(INTERPOLATION_META), allowed=dict(INTERPOLATION_ALLOWED))
        model, _ = function_model(functions={"int1": node})
        expect_error("INVALID_REQUEST", w13.function_data_import, worker_for(model), "Model", {
            "path": {"segments": [{"collection": "func", "tag": "int1"}]},
            "artifact_id": "art-1", "layout": {"file_path": "/tmp/x.txt", "struct": "nonsense"}, "units": {}})

    def test_non_interpolation_function_is_refused(self):
        node = function_node(type_id="Analytic")
        model, _ = function_model(functions={"an1": node})
        expect_error("API_UNSUPPORTED", w13.function_data_import, worker_for(model), "Model", {
            "path": {"segments": [{"collection": "func", "tag": "an1"}]},
            "artifact_id": "art-1", "layout": {"file_path": "/tmp/x.txt"}, "units": {}})

    def test_import_engine_error_is_reported_as_partial(self, tmp_path):
        node = function_node(value_types=dict(INTERPOLATION_META), unavailable=("importData",))
        model, _ = function_model(functions={"int1": node})
        data = tmp_path / "grid.txt"
        data.write_text("0 1\n")
        result = w13.function_data_import(worker_for(model), "Model", {
            "path": {"segments": [{"collection": "func", "tag": "int1"}]},
            "artifact_id": "art-1", "layout": {"file_path": str(data)}, "units": {}})
        assert result["import_data"]["ok"] is False
        assert result["ok"] is False and result["partial_change"] is True


class TestFunctionDataReload:
    def test_refreshes_a_file_backed_function(self, tmp_path):
        data = tmp_path / "grid.txt"
        data.write_text("0 1\n")
        node = function_node(values={"filename": str(data)}, value_types=dict(INTERPOLATION_META))
        model, _ = function_model(functions={"int1": node})
        result = w13.function_data_reload(worker_for(model), "Model", {
            "path": {"segments": [{"collection": "func", "tag": "int1"}]}})
        assert result["refresh"]["ok"] is True
        assert node.refreshes == 1
        assert result["sha256"]["status"] == "verified"

    def test_without_a_filename_there_is_nothing_to_reload(self):
        node = function_node(value_types=dict(INTERPOLATION_META))
        model, _ = function_model(functions={"int1": node})
        expect_error("INVALID_REQUEST", w13.function_data_reload, worker_for(model), "Model",
                     {"path": {"segments": [{"collection": "func", "tag": "int1"}]}})

    def test_refresh_failure_is_execution_state_unknown(self, tmp_path):
        data = tmp_path / "grid.txt"
        data.write_text("0 1\n")
        node = function_node(values={"filename": str(data)}, value_types=dict(INTERPOLATION_META),
                             unavailable=("refresh",))
        model, _ = function_model(functions={"int1": node})
        result = w13.function_data_reload(worker_for(model), "Model", {
            "path": {"segments": [{"collection": "func", "tag": "int1"}]}})
        assert result["status"] == "EXECUTION_STATE_UNKNOWN"


class TestFunctionEvaluate:
    def test_sampling_is_refused_with_the_documented_reason(self):
        node = function_node(values={"funcname": "f"})
        node.arrays["function_names"] = ["f"]
        model, _ = function_model(functions={"int1": node})
        error = expect_error("API_UNSUPPORTED", w13.function_evaluate, worker_for(model), "Model", {
            "path": {"segments": [{"collection": "func", "tag": "int1"}]},
            "arguments": [{"coordinate": [0.5, 0.5]}]})
        assert "no offline-verified COMSOL 6.4 API" in str(error)

    def test_invalid_argument_shape_is_rejected_before_the_refusal(self):
        node = function_node(values={"funcname": "f"})
        model, _ = function_model(functions={"int1": node})
        expect_error("INVALID_REQUEST", w13.function_evaluate, worker_for(model), "Model", {
            "path": {"segments": [{"collection": "func", "tag": "int1"}]},
            "arguments": [{"nope": 1}]})

    def test_arguments_must_be_a_non_empty_array(self):
        node = function_node(values={"funcname": "f"})
        model, _ = function_model(functions={"int1": node})
        expect_error("INVALID_REQUEST", w13.function_evaluate, worker_for(model), "Model", {
            "path": {"segments": [{"collection": "func", "tag": "int1"}]}, "arguments": []})

    def test_derivative_shape_is_validated(self):
        node = function_node(values={"funcname": "f"})
        model, _ = function_model(functions={"int1": node})
        expect_error("INVALID_REQUEST", w13.function_evaluate, worker_for(model), "Model", {
            "path": {"segments": [{"collection": "func", "tag": "int1"}]},
            "arguments": [{"value": 0.5}], "derivative": {"order": 3}})


# ---------------------------------------------------------------------------
# selection domain
# ---------------------------------------------------------------------------


class TestSelectionList:
    def test_lists_with_dimension_and_entity_count(self):
        node = selection_node(tag="sel1", entities_=[1, 2, 3], dim_=2)
        model, _ = component_with(selections={"sel1": node})
        result = w13.selection_list(worker_for(model), "Model", {"component": "comp1"})
        assert result["count"] == 1
        assert result["selections"][0]["entity_count"] == 3
        assert result["selections"][0]["type_id"] == "Explicit"

    def test_entities_unavailable_is_reported(self):
        node = selection_node(tag="sel1", unavailable=("entities",))
        model, _ = component_with(selections={"sel1": node})
        result = w13.selection_list(worker_for(model), "Model", {"component": "comp1"})
        assert result["selections"][0]["entity_count"] is None
        assert result["selections"][0]["entities_error"]["allowlist_entry_required"] == "entities"


class TestSelectionCreate:
    def test_creates_explicit_selection_with_entities(self):
        model, component = component_with(geometry=FNode(tag="geom1"))
        result = w13.selection_create(worker_for(model), "Model", {
            "component": "comp1", "tag": "sel1", "type_id": "Explicit",
            "definition": {"geometry": "geom1", "entity_dimension": 2, "entities": [1, 2]}})
        assert result["ok"] is True
        node = component.collections["selection"].items["sel1"]
        assert node.entities_ == [1, 2]
        assert node.dim_ == 2
        assert result["entities"] == [1, 2]

    def test_creates_box_selection_from_region_properties(self):
        model, component = component_with(geometry=FNode(tag="geom1"))
        node = selection_node(tag="box1", type_id="Box", value_types={
            "entitydim": "Int", "condition": "String", "xmin": "Double", "xmax": "Double",
            "ymin": "Double", "ymax": "Double"},
            allowed={"condition": ["intersects", "inside", "somevertex", "allvertices"]})
        component.collections["selection"].create = lambda tag, type_id: (
            component.collections["selection"].items.setdefault(tag, node), node)[1]
        result = w13.selection_create(worker_for(model), "Model", {
            "component": "comp1", "tag": "box1", "type_id": "Box",
            "definition": {"entity_dimension": 2, "xmin": 0.0, "xmax": 1.0, "ymin": 0.0, "ymax": 1.0}})
        assert result["ok"] is True
        assert node.numbers["xmax"] == 1.0
        assert node.integers["entitydim"] == 2

    def test_unknown_type_is_refused(self):
        model, _ = component_with()
        expect_error("API_UNSUPPORTED", w13.selection_create, worker_for(model), "Model", {
            "component": "comp1", "tag": "sel1", "type_id": "Nope", "definition": {}})

    def test_entities_on_a_non_explicit_type_are_refused(self):
        model, _ = component_with()
        expect_error("INVALID_REQUEST", w13.selection_create, worker_for(model), "Model", {
            "component": "comp1", "tag": "box1", "type_id": "Box",
            "definition": {"entity_dimension": 2, "entities": [1]}})

    def test_named_assignment_is_refused_with_the_reason(self):
        model, _ = component_with()
        expect_error("API_UNSUPPORTED", w13.selection_create, worker_for(model), "Model", {
            "component": "comp1", "tag": "sel1", "type_id": "Explicit", "definition": {"named": "other"}})

    def test_property_outside_the_type_vocabulary_is_refused(self):
        model, _ = component_with()
        expect_error("INVALID_REQUEST", w13.selection_create, worker_for(model), "Model", {
            "component": "comp1", "tag": "box1", "type_id": "Box", "definition": {"posx": 1.0}})

    def test_existing_tag_reports_conflict(self):
        model, _ = component_with(selections={"sel1": selection_node(tag="sel1")})
        expect_error("TAG_CONFLICT", w13.selection_create, worker_for(model), "Model", {
            "component": "comp1", "tag": "sel1", "type_id": "Explicit", "definition": {}})

    def test_unknown_component(self):
        model, _ = component_with()
        expect_error("NODE_NOT_FOUND", w13.selection_create, worker_for(model), "Model", {
            "component": "nope", "tag": "sel1", "type_id": "Explicit", "definition": {}})


class TestSelectionInspect:
    def test_reports_definition_and_state(self):
        node = selection_node(tag="sel1", entities_=[1, 2], dim_=2, geometry_tag="geom1")
        model, _ = component_with(selections={"sel1": node})
        result = w13.selection_inspect(worker_for(model), "Model", {"component": "comp1", "tag": "sel1"})
        assert result["entities"] == [1, 2]
        assert result["entity_dimension"] == 2
        assert result["state"]["geometry"] == "geom1"

    def test_missing_selection(self):
        model, _ = component_with()
        expect_error("NODE_NOT_FOUND", w13.selection_inspect, worker_for(model), "Model",
                     {"component": "comp1", "tag": "sel1"})


class TestSelectionUpdate:
    def test_reports_entity_changes(self):
        node = selection_node(tag="sel1", entities_=[1, 2], dim_=2)
        model, _ = component_with(selections={"sel1": node}, geometry=FNode(tag="geom1"))
        result = w13.selection_update(worker_for(model), "Model", {
            "component": "comp1", "tag": "sel1",
            "definition": {"geometry": "geom1", "entity_dimension": 2, "entities": [2, 3]}})
        assert result["entity_changes"]["added"] == [3]
        assert result["entity_changes"]["removed"] == [1]
        assert node.entities_ == [2, 3]

    def test_refuses_entities_on_a_non_explicit_type(self):
        node = selection_node(tag="box1", type_id="Box")
        model, _ = component_with(selections={"box1": node})
        expect_error("INVALID_REQUEST", w13.selection_update, worker_for(model), "Model", {
            "component": "comp1", "tag": "box1",
            "definition": {"entity_dimension": 2, "entities": [1]}})


class TestSelectionRemove:
    def test_removes_and_reports_reference_risk(self):
        model, component = component_with(selections={"sel1": selection_node(tag="sel1")})
        result = w13.selection_remove(worker_for(model), "Model", {"component": "comp1", "tag": "sel1"})
        assert result["removed"] is True
        assert "sel1" not in component.collections["selection"].items
        assert result["reference_risk"]["level"] == "UNSCANNED"

    def test_missing_selection(self):
        model, _ = component_with()
        expect_error("NODE_NOT_FOUND", w13.selection_remove, worker_for(model), "Model",
                     {"component": "comp1", "tag": "sel1"})


class TestSelectionEntities:
    def named_spec(self) -> dict[str, Any]:
        return {"kind": "named", "component": "comp1", "tag": "sel1"}

    def test_named_selection_entities(self):
        node = selection_node(tag="sel1", entities_=[3, 1, 2], dim_=2)
        model, _ = component_with(selections={"sel1": node})
        result = w13.selection_entities(worker_for(model), "Model", {"selection": self.named_spec()})
        assert result["entities"] == [1, 2, 3]  # entity lists are normalised: sorted, duplicate-free
        assert result["total_count"] == 3
        assert result["has_more"] is False

    def test_pagination_and_cursor_resume(self):
        node = selection_node(tag="sel1", entities_=[1, 2, 3, 4, 5], dim_=2)
        model, _ = component_with(selections={"sel1": node})
        first = w13.selection_entities(worker_for(model), "Model",
                                       {"selection": self.named_spec(), "limit": 2})
        assert first["entities"] == [1, 2] and first["has_more"] is True
        second = w13.selection_entities(worker_for(model), "Model",
                                        {"selection": self.named_spec(), "limit": 2,
                                         "cursor": first["next_cursor"]})
        assert second["entities"] == [3, 4] and second["has_more"] is True
        third = w13.selection_entities(worker_for(model), "Model",
                                      {"selection": self.named_spec(), "limit": 2,
                                       "cursor": second["next_cursor"]})
        assert third["entities"] == [5] and third["has_more"] is False

    def test_stale_cursor_is_refused(self):
        node = selection_node(tag="sel1", entities_=[1, 2, 3], dim_=2)
        model, _ = component_with(selections={"sel1": node})
        first = w13.selection_entities(worker_for(model), "Model",
                                       {"selection": self.named_spec(), "limit": 1})
        node.entities_ = [1, 2, 3, 4]
        expect_error("CURSOR_STALE", w13.selection_entities, worker_for(model), "Model",
                     {"selection": self.named_spec(), "limit": 1, "cursor": first["next_cursor"]})

    def test_explicit_spec_is_normalised_and_echoed(self):
        model, _ = component_with()
        result = w13.selection_entities(worker_for(model), "Model", {
            "selection": {"kind": "explicit", "entities": [2, 1], "entity_dimension": 2}})
        assert result["entities"] == [1, 2]
        assert result["selection"]["source"] == "caller_explicit"
        assert "sorted" in result["selection"]["notes"][0]

    def test_all_kind_uses_the_measurement_tool(self):
        model, component = component_with(selections={})
        component.measure_entities = {2: [1, 2, 3]}
        result = w13.selection_entities(worker_for(model), "Model", {
            "selection": {"kind": "all", "component": "comp1", "geometry": "geom1",
                          "entity_dimension": 2}})
        assert result["entities"] == [1, 2, 3]
        assert result["selection"]["source"] == "measure_tool_all"

    def test_spatial_kind_is_refused_for_a_read(self):
        model, _ = component_with()
        expect_error("API_UNSUPPORTED", w13.selection_entities, worker_for(model), "Model", {
            "selection": {"kind": "spatial", "component": "comp1", "geometry": "geom1",
                          "entity_dimension": 2, "query": {"kind": "box"}}})

    def test_missing_kind(self):
        model, _ = component_with()
        expect_error("INVALID_REQUEST", w13.selection_entities, worker_for(model), "Model",
                     {"selection": {}})

    def test_limit_is_bounded(self):
        model, _ = component_with()
        expect_error("INVALID_REQUEST", w13.selection_entities, worker_for(model), "Model", {
            "selection": {"kind": "explicit", "entities": [1]}, "limit": 9000})


class TestSelectionMeasure:
    def spec(self) -> dict[str, Any]:
        return {"kind": "named", "component": "comp1", "tag": "sel1"}

    def test_measures_area_and_volume(self):
        node = selection_node(tag="sel1", entities_=[1, 2], dim_=2, geometry_tag="geom1")
        model, component = component_with(selections={"sel1": node}, geometry=FNode(tag="geom1"))
        result = w13.selection_measure(worker_for(model), "Model",
                                       {"selection": self.spec(), "metrics": ["area", "volume"]})
        assert result["metrics"]["area"]["value"]["data"] == 0.25
        assert result["metrics"]["volume"]["value"]["data"] == 1.0e-6
        assert result["metrics"]["area"]["length_unit_power"] == 2
        assert result["isolation_required"] is True

    def test_centroid_is_refused_with_the_reason(self):
        node = selection_node(tag="sel1", entities_=[1], dim_=2)
        model, _ = component_with(selections={"sel1": node}, geometry=FNode(tag="geom1"))
        error = expect_error("API_UNSUPPORTED", w13.selection_measure, worker_for(model), "Model",
                             {"selection": self.spec(), "metrics": ["centroid"]})
        assert "centroid" in str(error)

    def test_unknown_metric(self):
        node = selection_node(tag="sel1", entities_=[1], dim_=2)
        model, _ = component_with(selections={"sel1": node}, geometry=FNode(tag="geom1"))
        expect_error("API_UNSUPPORTED", w13.selection_measure, worker_for(model), "Model",
                     {"selection": self.spec(), "metrics": ["magic"]})

    def test_explicit_spec_without_component_is_refused(self):
        model, _ = component_with()
        expect_error("INVALID_REQUEST", w13.selection_measure, worker_for(model), "Model",
                     {"selection": {"kind": "explicit", "entities": [1], "entity_dimension": 2},
                      "metrics": ["area"]})

    def test_metric_error_is_reported_per_metric(self):
        node = selection_node(tag="sel1", entities_=[1], dim_=2)
        model, component = component_with(selections={"sel1": node}, geometry=FNode(tag="geom1"))
        del component.measure_metrics["area"]  # measuring an unavailable metric (e.g. volume in 2D)
        result = w13.selection_measure(worker_for(model), "Model",
                                       {"selection": self.spec(), "metrics": ["area"]})
        assert "error" in result["metrics"]["area"]


class TestSelectionQuerySpatial:
    QUERY_META = {
        "entitydim": "Int", "condition": "String", "xmin": "Double", "xmax": "Double",
        "ymin": "Double", "ymax": "Double", "r": "Double", "posx": "Double", "posy": "Double",
        "axistype": "String", "pos": "DoubleArray",
    }
    QUERY_ALLOWED = {"condition": ["intersects", "inside", "somevertex", "allvertices"]}

    def stub_create(self, container: FList, entities: tuple[int, ...] = (1, 2),
                    capture: dict[str, Any] | None = None) -> Any:
        """``create(tag, type)`` on the selection list returning a metadata-bearing node."""

        def create(tag: str, type_id: str) -> FNode:
            node = selection_node(tag=tag, type_id=type_id, entities_=list(entities),
                                  value_types=dict(self.QUERY_META), allowed=dict(self.QUERY_ALLOWED))
            container.items[tag] = node
            if capture is not None:
                capture["node"] = node
            return node

        return create

    def box_query(self, **kwargs: Any) -> dict[str, Any]:
        payload = {"component": "comp1", "geometry": "geom1", "dimension": 2,
                   "query": {"kind": "box", "xmin": 0.0, "xmax": 1.0, "ymin": 0.0, "ymax": 1.0},
                   "tolerance": {"value": 0.0, "unit": "m"}}
        payload.update(kwargs)
        return payload

    def test_box_query_creates_reads_and_removes_a_temporary_selection(self):
        model, component = component_with(geometry=FNode(tag="geom1"))
        container = component.collections["selection"]
        container.create = self.stub_create(container)  # type: ignore[assignment]
        result = w13.selection_query_spatial(worker_for(model), "Model", self.box_query())
        assert result["entities"] == [1, 2]
        assert result["temporary_node"]["removed"] is True
        assert container.items == {}
        assert result["region_unit"] == "m"

    def test_tolerance_inflates_the_region_in_the_geometry_unit(self):
        geom = FNode(tag="geom1")
        geom.geometry_tag = "mm"
        model, component = component_with(geometry=geom)
        container = component.collections["selection"]
        capture: dict[str, Any] = {}
        container.create = self.stub_create(container, entities=(), capture=capture)  # type: ignore[assignment]
        result = w13.selection_query_spatial(worker_for(model), "Model",
                                             self.box_query(tolerance={"value": 0.5, "unit": "mm"}))
        assert result["region"]["xmin"] == -0.5 and result["region"]["xmax"] == 1.5
        assert capture["node"].numbers["xmax"] == 1.5
        assert result["temporary_node"]["removed"] is True

    def test_tolerance_unit_mismatch_is_refused_without_conversion(self):
        geom = FNode(tag="geom1")
        geom.geometry_tag = "mm"
        model, _ = component_with(geometry=geom)
        expect_error("INVALID_REQUEST", w13.selection_query_spatial, worker_for(model), "Model",
                     self.box_query(tolerance={"value": 1.0, "unit": "m"}))

    def test_unreadable_geometry_unit_is_refused(self):
        model, _ = component_with(geometry=FNode(tag="geom1", unavailable=("lengthUnit",)))
        expect_error("API_UNSUPPORTED", w13.selection_query_spatial, worker_for(model), "Model",
                     self.box_query())

    def test_unsupported_query_kind(self):
        model, _ = component_with(geometry=FNode(tag="geom1"))
        expect_error("API_UNSUPPORTED", w13.selection_query_spatial, worker_for(model), "Model",
                     self.box_query(query={"kind": "normal", "normal": [0, 0, 1]}))

    def test_missing_box_bounds(self):
        model, _ = component_with(geometry=FNode(tag="geom1"))
        expect_error("INVALID_REQUEST", w13.selection_query_spatial, worker_for(model), "Model",
                     self.box_query(query={"kind": "box", "xmin": 0.0, "xmax": 1.0, "ymin": 0.0}))

    def test_dimension_beyond_space_dimension(self):
        model, _ = component_with(geometry=FNode(tag="geom1", integers={"sdim": 2}))
        expect_error("INVALID_REQUEST", w13.selection_query_spatial, worker_for(model), "Model",
                     self.box_query(dimension=3))

    def test_cleanup_failure_is_reported_as_unknown_state(self):
        model, component = component_with(geometry=FNode(tag="geom1"))
        container = component.collections["selection"]
        container.create = self.stub_create(container, entities=())  # type: ignore[assignment]
        container.remove = lambda tag: None  # type: ignore[assignment]
        expect_error("EXECUTION_STATE_UNKNOWN", w13.selection_query_spatial, worker_for(model), "Model",
                     self.box_query())

    def test_ball_center_shorthand(self):
        model, component = component_with(geometry=FNode(tag="geom1"))
        container = component.collections["selection"]
        container.create = self.stub_create(container, entities=(1,))  # type: ignore[assignment]
        result = w13.selection_query_spatial(worker_for(model), "Model", {
            "component": "comp1", "geometry": "geom1", "dimension": 2,
            "query": {"kind": "ball", "center": [0.1, 0.2], "r": 0.5},
            "tolerance": {"value": 0.1, "unit": "m"}})
        assert result["region"]["r"] == 0.6
        assert result["region"]["posx"] == 0.1
        assert result["entities"] == [1]


class TestSelectionAdjacency:
    def spec(self, **kwargs: Any) -> dict[str, Any]:
        payload = {"kind": "explicit", "component": "comp1", "geometry": "geom1",
                   "entity_dimension": 2, "entities": [1, 2]}
        payload.update(kwargs)
        return payload

    def test_adjacency_uses_getAdj_rows_indexed_by_entity(self):
        geom = FNode(tag="geom1")
        geom.matrices["adjacency"] = [[], [11, 12], [12, 13], []]
        model, _ = component_with(geometry=geom)
        result = w13.selection_adjacency(worker_for(model), "Model",
                                         {"selection": self.spec(), "target_dimension": 1})
        assert result["adjacent_entities"] == [11, 12, 13]
        assert result["per_entity"][0] == {"entity": 1, "adjacent": [11, 12]}

    def test_entity_outside_the_adjacency_matrix_is_unknown_state(self):
        geom = FNode(tag="geom1")
        geom.matrices["adjacency"] = [[], []]
        model, _ = component_with(geometry=geom)
        expect_error("EXECUTION_STATE_UNKNOWN", w13.selection_adjacency, worker_for(model), "Model",
                     {"selection": self.spec(entities=[1, 5]), "target_dimension": 1})

    def test_single_geometry_is_auto_selected(self):
        geom = FNode(tag="geom1")
        geom.matrices["adjacency"] = [[], [11], []]
        model, _ = component_with(geometry=geom)
        result = w13.selection_adjacency(worker_for(model), "Model", {
            "selection": {"kind": "named", "component": "comp1", "tag": "sel1"},
            "target_dimension": 1}) if False else None
        spec = {"kind": "explicit", "component": "comp1", "entity_dimension": 2, "entities": [1]}
        result = w13.selection_adjacency(worker_for(model), "Model",
                                         {"selection": spec, "target_dimension": 1})
        assert result["geometry_selection"] == "single_geometry_auto_selected"
        assert result["adjacent_entities"] == [11]

    def test_multiple_geometries_require_an_explicit_geometry(self):
        geom = FNode(tag="geom1")
        other = FNode(tag="geom2")
        model, component = component_with(geometry=geom)
        component.collections["geom"].items["geom2"] = other
        expect_error("INVALID_REQUEST", w13.selection_adjacency, worker_for(model), "Model", {
            "selection": {"kind": "explicit", "component": "comp1", "entity_dimension": 2, "entities": [1]},
            "target_dimension": 1})

    def test_target_dimension_is_bounded(self):
        model, _ = component_with(geometry=FNode(tag="geom1"))
        expect_error("INVALID_REQUEST", w13.selection_adjacency, worker_for(model), "Model",
                     {"selection": self.spec(), "target_dimension": 7})


class TestSelectionValidate:
    def spec(self) -> dict[str, Any]:
        return {"kind": "named", "component": "comp1", "tag": "sel1"}

    def test_pass_verdict_with_measures(self):
        node = selection_node(tag="sel1", entities_=[1, 2], dim_=2, geometry_tag="geom1")
        model, _ = component_with(selections={"sel1": node}, geometry=FNode(tag="geom1"))
        result = w13.selection_validate(worker_for(model), "Model", {
            "selection": self.spec(),
            "expectations": {"non_empty": True, "entity_dimension": 2, "count": 2,
                             "measures": {"area": {"value": 0.25, "unit": "m", "abs_tol": 1e-9}}}})
        assert result["verdict"] == "PASS" and result["ok"] is True
        assert all(check["status"] == "PASS" for check in result["checks"])

    def test_fail_verdict_is_data_not_an_exception(self):
        node = selection_node(tag="sel1", entities_=[1, 2], dim_=2, geometry_tag="geom1")
        model, _ = component_with(selections={"sel1": node}, geometry=FNode(tag="geom1"))
        result = w13.selection_validate(worker_for(model), "Model", {
            "selection": self.spec(), "expectations": {"count": 5}})
        assert result["verdict"] == "FAIL" and result["ok"] is False
        assert result["checks"][0]["actual"] == 2

    def test_measure_out_of_tolerance_fails(self):
        node = selection_node(tag="sel1", entities_=[1], dim_=2, geometry_tag="geom1")
        model, _ = component_with(selections={"sel1": node}, geometry=FNode(tag="geom1"))
        result = w13.selection_validate(worker_for(model), "Model", {
            "selection": self.spec(), "expectations": {"measures": {"area": {"value": 0.5,
                                                                            "abs_tol": 1e-6}}}})
        assert result["verdict"] == "FAIL"
        assert result["measures"]["area"]["status"] == "FAIL"

    def test_unit_mismatch_never_silently_passes(self):
        node = selection_node(tag="sel1", entities_=[1], dim_=2, geometry_tag="geom1")
        model, _ = component_with(selections={"sel1": node}, geometry=FNode(tag="geom1"))
        result = w13.selection_validate(worker_for(model), "Model", {
            "selection": self.spec(),
            "expectations": {"measures": {"area": {"value": 0.25, "unit": "mm", "abs_tol": 1e-9}}}})
        assert result["verdict"] == "FAIL"

    def test_bounding_box_expectation_is_refused(self):
        node = selection_node(tag="sel1", entities_=[1], dim_=2)
        model, _ = component_with(selections={"sel1": node}, geometry=FNode(tag="geom1"))
        error = expect_error("API_UNSUPPORTED", w13.selection_validate, worker_for(model), "Model", {
            "selection": self.spec(), "expectations": {"bounding_box": {"min": [0, 0]}}})
        assert "bounding-box" in str(error)

    def test_entity_ids_in_range_check(self):
        node = selection_node(tag="sel1", entities_=[1, 9], dim_=2, geometry_tag="geom1")
        model, component = component_with(selections={"sel1": node}, geometry=FNode(tag="geom1"))
        component.measure_entities = {2: [1, 2, 3]}
        result = w13.selection_validate(worker_for(model), "Model", {
            "selection": self.spec(), "expectations": {"entity_ids_in_range": True}})
        assert result["verdict"] == "FAIL"
        assert result["checks"][0]["out_of_range"] == [9]

    def test_unknown_expectation_key(self):
        node = selection_node(tag="sel1", entities_=[1], dim_=2)
        model, _ = component_with(selections={"sel1": node}, geometry=FNode(tag="geom1"))
        expect_error("INVALID_REQUEST", w13.selection_validate, worker_for(model), "Model", {
            "selection": self.spec(), "expectations": {"colour": "red"}})

    def test_measures_are_required_to_carry_a_value(self):
        node = selection_node(tag="sel1", entities_=[1], dim_=2)
        model, _ = component_with(selections={"sel1": node}, geometry=FNode(tag="geom1"))
        expect_error("INVALID_REQUEST", w13.selection_validate, worker_for(model), "Model", {
            "selection": self.spec(), "expectations": {"measures": {"area": {"abs_tol": 1e-9}}}})


def test_nested_definition_properties_accept_the_catalogue_row_array() -> None:
    """``definition.properties`` is a PropertySet: the catalogue row array must be accepted.

    The production driver sends ``[{"name": ..., "value": <TypedValue>}, ...]`` (the shape
    ``common.schema.json#/$defs/PropertySet`` declares); the module's internal spelling is a
    mapping.  Both must reach the same writer with the same readback.
    """
    model, _ = function_model()
    result = w13.function_create(worker_for(model), "Model", {
        "tag": "an2", "type_id": "Analytic",
        "definition": {"properties": [
            {"name": "expr", "value": {"kind": "expression", "shape": [], "data": "cos(x)"}},
            {"name": "args", "value": {"kind": "string", "shape": [1], "data": ["x"]}},
        ]}})
    assert result["ok"] is True and result["status"] == "APPLIED"
    assert model.collections["func"].items["an2"].values["expr"] == "cos(x)"
    assert list(result["properties"]) == ["expr", "args"] or {"expr", "args"} <= set(result["properties"])


def test_a_nested_row_may_not_duplicate_a_flat_property() -> None:
    model, _ = function_model()
    expect_error("INVALID_REQUEST", w13.function_create, worker_for(model), "Model", {
        "tag": "an3", "type_id": "Analytic",
        "definition": {"expr": "sin(x)",
                       "properties": [{"name": "expr",
                                       "value": {"kind": "expression", "shape": [], "data": "cos(x)"}}]}})


def test_a_structurally_broken_row_never_reaches_the_writer() -> None:
    """A row that is not a PropertySet row is refused while the definition is normalised.

    Every create in this layer creates the node first and writes the definition afterwards
    (a property refusal is reported as a partial failure against the visible node); what a
    broken row may never do is reach the frozen writer.
    """
    model, _ = function_model()
    expect_error("INVALID_REQUEST", w13.function_create, worker_for(model), "Model", {
        "tag": "an4", "type_id": "Analytic",
        "definition": {"properties": [{"name": "expr"}]}})
    created = model.collections["func"].items.get("an4")
    assert created is not None and not created.values, "no property value may be written from a broken PropertySet"


# ---------------------------------------------------------------------------
# function definition field alignment (C06 / W13_T016)
# ---------------------------------------------------------------------------


class TestFunctionDefinitionFieldAlignment:
    """The GUI-label spelling of an interpolation setting is a translated key.

    driver5c W13_T016 sent ``{"interpolation": "linear", "extrapolation":
    "none", "data_unit": "W/m^2"}``; the local COMSOL 6.4 corpus documents
    those settings under the API property names ``interp``, ``extrap`` and
    ``fununit`` (Programming Reference interpolation properties table, p.113),
    and ``02_ACTION_CATALOG.json`` does not name any definition field at all
    (``definition`` is a bare object).  The product side therefore translates
    the documented GUI labels to the documented property names and reports the
    translation instead of writing an unverified property name.
    """

    DRIVER_DEFINITION = {"interpolation": "linear", "extrapolation": "none", "data_unit": "W/m^2"}

    def test_gui_labels_are_translated_to_the_documented_properties(self):
        model, _ = function_model()
        result = w13.function_create(worker_for(model), "Model", {
            "tag": "int1", "type_id": "Interpolation", "definition": dict(self.DRIVER_DEFINITION)})
        assert result["status"] == "APPLIED", result["failed"]
        node = model.collections["func"].items["int1"]
        assert node.values["interp"] == "linear"
        assert node.values["extrap"] == "none"
        assert node.values["fununit"] == "W/m^2"
        assert not any(name in node.values for name in ("interpolation", "extrapolation", "data_unit"))
        assert result["definition_keys"] == sorted(self.DRIVER_DEFINITION)
        assert {row["given"] for row in result["definition_key_translations"]} == set(self.DRIVER_DEFINITION)
        assert {row["property"] for row in result["definition_key_translations"]} == {"interp", "extrap", "fununit"}
        assert "Programming Reference 6.4" in result["definition_key_translations"][0]["basis"]

    def test_the_nested_properties_wrapper_is_translated_too(self):
        model, _ = function_model()
        result = w13.function_create(worker_for(model), "Model", {
            "tag": "int1", "type_id": "Interpolation",
            "definition": {"properties": {"extrapolation": "const", "fununit": "Pa"}}})
        assert result["status"] == "APPLIED", result["failed"]
        node = model.collections["func"].items["int1"]
        assert node.values["extrap"] == "const" and node.values["fununit"] == "Pa"

    def test_both_spellings_of_one_setting_are_refused(self):
        model, _ = function_model()
        error = expect_error("INVALID_REQUEST", w13.function_create, worker_for(model), "Model", {
            "tag": "int1", "type_id": "Interpolation",
            "definition": {"interp": "linear", "interpolation": "neighbor"}})
        assert "twice" in str(error)

    def test_an_uncited_field_is_still_refused_before_the_write(self):
        model, _ = function_model()
        expect_error("INVALID_REQUEST", w13.function_create, worker_for(model), "Model", {
            "tag": "int1", "type_id": "Interpolation", "definition": {"data_units": "W/m^2"}})

    def test_update_translates_the_same_labels(self):
        node = function_node(values={"funcname": "f"}, value_types=dict(INTERPOLATION_META),
                             allowed=dict(INTERPOLATION_ALLOWED))
        model, _ = function_model({"int1": node})
        result = w13.function_update(worker_for(model), "Model", {
            "path": {"segments": [{"collection": "func", "tag": "int1"}]},
            "definition": dict(self.DRIVER_DEFINITION)})
        assert result["status"] == "APPLIED", result["failed"]
        assert node.values["interp"] == "linear"
        assert result["definition_key_translations"]

    def test_inspect_reports_the_documented_label_view(self):
        node = function_node(values={"funcname": "f", "interp": "linear", "extrap": "none",
                                     "fununit": "W/m^2"},
                             value_types=dict(INTERPOLATION_META), allowed=dict(INTERPOLATION_ALLOWED))
        model, _ = function_model({"int1": node})
        result = w13.function_inspect(worker_for(model), "Model", {
            "path": {"segments": [{"collection": "func", "tag": "int1"}]}})
        assert result["interpolation"] == "linear"
        assert result["extrapolation"] == "none"
        assert result["data_unit"] == "W/m^2"
        assert result["settings"]["interpolation"]["property"] == "interp"
        assert "Programming Reference 6.4" in result["settings"]["interpolation"]["basis"]
        assert result["properties"]["interp"]["value"] == "linear"

    def test_inspect_keeps_the_label_keys_present_when_a_value_is_unreadable(self):
        node = function_node(values={"funcname": "f"}, value_types=dict(INTERPOLATION_META),
                             allowed=dict(INTERPOLATION_ALLOWED))
        model, _ = function_model({"int1": node})
        result = w13.function_inspect(worker_for(model), "Model", {
            "path": {"segments": [{"collection": "func", "tag": "int1"}]}})
        assert "extrapolation" in result and result["extrapolation"] is None
        assert result["settings"]["extrapolation"]["property"] == "extrap"
