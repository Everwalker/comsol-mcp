"""W14: geometry / work plane / CAD-path / coordinate / pair / coupling operations.

Every test drives the real operation code against a fake COMSOL node tree.  The
fake mirrors the *verified* API surface the implementation uses: a
``ModelEntityList``-like container (``tags/get/create/remove``), the
``GeomSequence``/``GeomFeature`` pair (``feature/create/run/getSDim/lengthUnit/
getNEntities/getBoundingBox/geom/selection``), the ``PropFeature`` property
metadata (``properties/getValueType/getAllowedPropertyValues`` and the typed
getters), the geometry measurement tools (``geom.measure()`` ->
``GeomObjectSelection`` and ``component.measure()`` -> ``MeshSelection``), the
component/geometry/pair/coupler/coordinate-system lists and the local
``Selection`` surface.  A method the fake does not implement surfaces as
``AttributeError`` and a method listed in ``unavailable`` raises the worker
allow-list refusal (``METHOD_REJECTED``), so both "the API does not exist" and
"the worker refuses the method" paths are exercised.

Pre-write refusal tests additionally assert that the mutating engine call was
never issued, and post-write verification failures assert that the result is
returned as *data* (``status``/``partial_change``/``execution_state_unknown``/
``not_executed``) rather than raised.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Mapping

import pytest

from comsol_mcp._execution_contract import ExecutionContractError

from comsol_mcp import _g3_w14 as w14
from comsol_mcp._g3_ops import DISPATCH, EFFECTS, IMPLEMENTED_OPERATIONS, REQUIRES_ISOLATION

# ---------------------------------------------------------------------------
# fake engine
# ---------------------------------------------------------------------------

_METADATA_KINDS = {
    "String": "string", "StringArray": "string", "StringMatrix": "string",
    "Boolean": "boolean", "BooleanArray": "boolean",
    "Int": "int32", "IntArray": "int32",
    "Double": "float64", "DoubleArray": "float64", "DoubleMatrix": "float64",
}
_GETTERS = {
    "String": "getString", "StringArray": "getStringArray",
    "Boolean": "getBoolean", "BooleanArray": "getBooleanArray",
    "Int": "getInt", "IntArray": "getIntArray",
    "Double": "getDouble", "DoubleArray": "getDoubleArray",
}


class FakeEngineError(RuntimeError):
    """Structured worker failure; ``code`` feeds ``_worker_failure_code``."""

    def __init__(self, message: str, *, code: str = "ENGINE_CALL_FAILED") -> None:
        super().__init__(message)
        self.reply = {"ok": False, "code": code, "message": message}
        self.failure = {"code": code, "message": message}


def _rejected(method: str) -> FakeEngineError:
    return FakeEngineError(f"SecurityException: METHOD_REJECTED ({method})", code="METHOD_REJECTED")


class FList:
    """Stand-in for a COMSOL ``ModelEntityList`` (``tags/get/create/remove``)."""

    def __init__(self, *, arity: int = 1, node_type: str = "Fake", unavailable: tuple[str, ...] = (),
                 factory: Any = None) -> None:
        self.arity = arity
        self.items: dict[str, Any] = {}
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.unavailable = set(unavailable)
        self.node_type = node_type
        self.factory = factory

    def tags(self) -> list[str]:
        self.calls.append(("tags", ()))
        if "tags" in self.unavailable:
            raise _rejected("tags")
        return list(self.items)

    def get(self, tag: str) -> Any:
        self.calls.append(("get", (tag,)))
        if "get" in self.unavailable:
            raise _rejected("get")
        return self.items[tag]

    def hasTag(self, tag: str) -> bool:
        return tag in self.items

    def size(self) -> int:
        return len(self.items)

    def index(self, tag: str) -> int:
        return list(self.items).index(tag)

    def create(self, tag: str, *type_id: Any) -> Any:
        self.calls.append(("create", (tag,) + tuple(type_id)))
        if "create" in self.unavailable:
            raise _rejected("create")
        if tag in self.items:
            raise FakeEngineError(f"tag {tag} already exists")
        if self.factory is not None:
            node = self.factory(tag, *type_id)
        else:
            node = FNode(tag=tag, type_id=str(type_id[0]) if type_id else self.node_type)
        self.items[tag] = node
        return node

    def remove(self, tag: str) -> None:
        self.calls.append(("remove", (tag,)))
        if "remove" in self.unavailable:
            raise _rejected("remove")
        if tag not in self.items:
            raise FakeEngineError(f"tag {tag} does not exist")
        del self.items[tag]

    def mutation_calls(self) -> list[tuple[str, tuple[Any, ...]]]:
        return [call for call in self.calls if call[0] in {"create", "remove"}]

    def __getattr__(self, name: str) -> Any:
        raise AttributeError(name)


class FSelection:
    """``GeomObjectSelection``-like local selection (``named/set/entities/all``)."""

    def __init__(self, owner: "FNode | None" = None) -> None:
        self.owner = owner
        self.named_ref: str | None = None
        self.objects: list[Any] = []
        self.numeric = False
        self.dimension: int | None = None
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.unavailable: set[str] = set()
        self.entity_overrides: dict[str, list[int]] = {}

    def named(self, *args: Any) -> Any:
        self.calls.append(("named", args))
        if "named" in self.unavailable:
            raise _rejected("named")
        if args:
            self.named_ref = str(args[0])
            return None
        return self.named_ref

    def geom(self, *args: Any) -> Any:
        self.calls.append(("geom", args))
        if args:
            self.dimension = int(args[0])
            return None
        return self.dimension

    def set(self, *args: Any) -> None:
        self.calls.append(("set", args))
        if "set" in self.unavailable:
            raise _rejected("set")
        if len(args) == 1 and isinstance(args[0], (list, tuple)):
            items = list(args[0])
        else:
            items = list(args)
        # A coupling's default selection is a geometric entity selection: set(1,2,3)
        # then entities() reads the numeric ids back (javap Selection.set(int...)).
        self.numeric = bool(items) and all(isinstance(item, int) and not isinstance(item, bool) for item in items)
        self.objects = list(items)

    def all(self) -> None:
        self.calls.append(("all", ()))
        self.objects = list(self.owner.objects) if self.owner is not None else []

    def entities(self, *args: Any) -> list[int]:
        self.calls.append(("entities", args))
        if "entities" in self.unavailable:
            raise _rejected("entities")
        if args:
            tag = str(args[0])
            if tag in self.entity_overrides:
                return list(self.entity_overrides[tag])
            return [self.objects.index(tag) + 1] if tag in self.objects else []
        if self.numeric:
            return [int(item) for item in self.objects]
        return [index + 1 for index, _ in enumerate(self.objects)]

    def dim(self) -> int:
        return 2

    def mutation_calls(self) -> list[tuple[str, tuple[Any, ...]]]:
        return [call for call in self.calls if call[0] in {"named", "set", "all"}]


class FMeasureSelection:
    """``MeshSelection``-like selection of the component's finalized measure tool."""

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
        self.selected = list(self.owner.measure_entities.get(self.dim_value if self.dim_value is not None else 0, []))

    def entities(self) -> list[int]:
        return list(self.selected)

    def dim(self) -> int:
        return self.dim_value if self.dim_value is not None else 0


class FMeasure:
    """``GeomMeasureFinal`` / ``GeomMeasure``: a transient measurement tool."""

    def __init__(self, owner: "FNode", *, mesh_selection: bool = True) -> None:
        self.owner = owner
        self.mesh_selection = mesh_selection
        self._selection = FMeasureSelection(owner) if mesh_selection else FSelection(owner)

    def selection(self, *args: Any) -> Any:
        return self._selection

    def _metric(self, name: str) -> Any:
        if name not in self.owner.measure_metrics:
            raise FakeEngineError(f"no metric {name}")
        return self.owner.measure_metrics[name]

    def getArea(self) -> float:
        return float(self._metric("area"))

    def getVolume(self) -> float:
        return float(self._metric("volume"))

    def getLength(self) -> float:
        return float(self._metric("length"))

    def getPerimeter(self) -> float:
        return float(self._metric("perimeter"))

    def getBoundaryArea(self) -> float:
        return float(self._metric("boundary_area"))

    def getBoundaryVolume(self) -> float:
        return float(self._metric("boundary_volume"))

    def getBoundingBox(self) -> list[float]:
        return list(self._metric("bounding_box"))

    def getNEntities(self) -> list[int]:
        return list(self._metric("n_entities"))

    def getNFiniteVoids(self) -> int:
        return int(self._metric("finite_voids"))

    def getVtxCoord(self) -> list[float]:
        return list(self._metric("vtx_coord"))

    def getVtxDistance(self) -> float:
        return float(self._metric("vtx_distance"))

    def getEdgeAngle(self) -> float:
        return float(self._metric("edge_angle"))


class FNode:
    """Generic fake COMSOL node covering the W14 API surface."""

    def __init__(self, tag: str = "n1", *, type_id: str = "Fake", label: str | None = None,
                 value_types: Mapping[str, str] | None = None,
                 values: Mapping[str, Any] | None = None,
                 allowed: Mapping[str, list[str]] | None = None,
                 collections: Mapping[str, Any] | None = None,
                 unavailable: tuple[str, ...] = (),
                 readback_overrides: Mapping[str, Any] | None = None,
                 errors: Mapping[str, Exception] | None = None,
                 active_: bool = True,
                 objects: list[str] | None = None,
                 measure_metrics: Mapping[str, Any] | None = None,
                 measure_entities: Mapping[int, list[int]] | None = None,
                 feature_list: FList | None = None,
                 inner_sequence: Any = None,
                 build_error: Exception | None = None,
                 run_log: list[Any] | None = None,
                 selection_override: Any = None,
                 sdim: int | None = None,
                 length_unit_value: str | None = None) -> None:
        self.tag_ = tag
        self.type_id = type_id
        self.label_ = label if label is not None else f"{tag} label"
        self.value_types = dict(value_types or {})
        self.values = dict(values or {})
        self.allowed = dict(allowed or {})
        self.collections = dict(collections or {})
        self.unavailable = set(unavailable)
        self.readback_overrides = dict(readback_overrides or {})
        self.errors = dict(errors or {})
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.active_ = bool(active_)
        self.objects = list(objects or [])
        self.measure_metrics = dict(measure_metrics or {})
        self.measure_entities = dict(measure_entities or {})
        self.feature_list = feature_list
        self.inner_sequence = inner_sequence
        self.build_error = build_error
        self.run_log = run_log if run_log is not None else []
        self.selection_override = selection_override
        self.sdim = sdim
        self.length_unit_value = length_unit_value
        self.selection_calls: dict[str, FSelection] = {}
        self.removed = False

    # -- identity ---------------------------------------------------------
    def tag(self) -> str:
        return self.tag_

    def label(self, *args: Any) -> Any:
        self.calls.append(("label", args))
        if args:
            self.label_ = args[0]
            return None
        return self.label_

    def getType(self) -> str:
        self.calls.append(("getType", ()))
        if "getType" in self.unavailable:
            raise _rejected("getType")
        return self.type_id

    def active(self, *args: Any) -> Any:
        self.calls.append(("active", args))
        if "active" in self.unavailable:
            raise _rejected("active")
        if args:
            self.active_ = bool(args[0])
            if args[0] in {False, 0}:
                self.active_ = False
            return None
        return self.active_

    def identifier(self) -> str:
        return self.tag_

    # -- property surface -------------------------------------------------
    def properties(self) -> list[str]:
        self.calls.append(("properties", ()))
        if "properties" in self.unavailable:
            raise _rejected("properties")
        return list(self.value_types)

    def getValueType(self, name: str) -> str | None:
        self.calls.append(("getValueType", (name,)))
        if "getValueType" in self.unavailable:
            raise _rejected("getValueType")
        return self.value_types.get(name)

    def getAllowedPropertyValues(self, name: str) -> list[str] | None:
        return self.allowed.get(name)

    def hasProperty(self, name: str) -> bool:
        return name in self.value_types

    def set(self, name: str, value: Any) -> None:
        self.calls.append(("set", (name, value)))
        if "set" in self.unavailable:
            raise _rejected("set")
        if name in self.errors:
            raise self.errors[name]
        self.values[name] = value.get("data") if isinstance(value, dict) and "data" in value else value

    def _read(self, name: str, getter: str) -> Any:
        self.calls.append((getter, (name,)))
        if getter in self.unavailable:
            raise _rejected(getter)
        if name in self.readback_overrides:
            return self.readback_overrides[name]
        if name in self.errors:
            raise self.errors[name]
        value_type = self.value_types.get(name)
        if value_type is None or _GETTERS.get(value_type) != getter:
            raise FakeEngineError(f"property {name} has no {getter} overload")
        return self.values.get(name)

    def getString(self, name: str) -> str:
        return self._read(name, "getString")

    def getStringArray(self, name: str) -> list[str]:
        return self._read(name, "getStringArray")

    def getStringMatrix(self, name: str) -> list[list[str]]:
        return self._read(name, "getStringMatrix")

    def getDouble(self, name: str) -> float:
        return self._read(name, "getDouble")

    def getDoubleArray(self, name: str) -> list[float]:
        return self._read(name, "getDoubleArray")

    def getDoubleMatrix(self, name: str) -> list[list[float]]:
        return self._read(name, "getDoubleMatrix")

    def getInt(self, name: str) -> int:
        return self._read(name, "getInt")

    def getIntArray(self, name: str) -> list[int]:
        return self._read(name, "getIntArray")

    def getBoolean(self, name: str) -> bool:
        return self._read(name, "getBoolean")

    def getBooleanArray(self, name: str) -> list[bool]:
        return self._read(name, "getBooleanArray")

    # -- collections ------------------------------------------------------
    def _collection(self, name: str) -> Any:
        node = self.collections.get(name)
        if node is None:
            raise AttributeError(name)
        return node

    def component(self, tag: Any = None) -> Any:
        self.calls.append(("component", (tag,)))
        return self._collection("component") if tag is None else self._collection("component").items[tag]

    def geom(self, tag: Any = None) -> Any:
        self.calls.append(("geom", (tag,)))
        if "geom" in self.unavailable:
            raise _rejected("geom")
        if tag is None:
            # GeomFeature.geom(): a WorkPlane feature exposes its nested 2D sequence.
            if self.inner_sequence is not None:
                return self.inner_sequence
            if "geom" in self.collections:
                return self.collections["geom"]
            raise AttributeError("geom")
        container = self.collections.get("geom")
        if container is None:
            raise AttributeError("geom")
        return container.items[tag]

    def feature(self, tag: Any = None) -> Any:
        self.calls.append(("feature", (tag,)))
        if "feature" in self.unavailable:
            raise _rejected("feature")
        if self.feature_list is None:
            raise AttributeError("feature")
        return self.feature_list if tag is None else self.feature_list.items[tag]

    def selection(self, name: Any = None) -> Any:
        self.calls.append(("selection", (name,)))
        if "selection" in self.unavailable:
            raise _rejected("selection")
        if self.selection_override is not None:
            return self.selection_override
        if name is None:
            # PropFeature.selection(): the feature's own (default) selection.
            if "selection" in self.collections:
                return self.collections["selection"]
            return self.selection_calls.setdefault("__default__", FSelection(self))
        key = str(name)
        if key not in self.selection_calls:
            self.selection_calls[key] = FSelection(self)
        return self.selection_calls[key]

    def measure(self) -> FMeasure:
        self.calls.append(("measure", ()))
        if "measure" in self.unavailable:
            raise _rejected("measure")
        if "measure" in self.collections:
            return self.collections["measure"]
        # geom.measure() -> GeomObjectSelection (object tags); the component's
        # finalized measurement tool selects numeric entities (geometric
        # entity selection).  Verified against javap GeomMeasureGeom /
        # GeomMeasure.selection().
        return FMeasure(self, mesh_selection=self.type_id != "GeomSequence")

    def pair(self, tag: Any = None) -> Any:
        return self._collection("pair") if tag is None else self._collection("pair").items[tag]

    def cpl(self, tag: Any = None) -> Any:
        return self._collection("cpl") if tag is None else self._collection("cpl").items[tag]

    def coordSystem(self, tag: Any = None) -> Any:
        return self._collection("coordSystem") if tag is None else self._collection("coordSystem").items[tag]

    def material(self, tag: Any = None) -> Any:
        return self._collection("material") if tag is None else self._collection("material").items[tag]

    def physics(self, tag: Any = None) -> Any:
        return self._collection("physics") if tag is None else self._collection("physics").items[tag]

    def variable(self, tag: Any = None) -> Any:
        return self._collection("variable") if tag is None else self._collection("variable").items[tag]

    def func(self, tag: Any = None) -> Any:
        return self._collection("func") if tag is None else self._collection("func").items[tag]

    # -- sequence-like surface (used by FGeomSequence and work planes) -----
    def getSDim(self) -> int:
        self.calls.append(("getSDim", ()))
        if "getSDim" in self.unavailable:
            raise _rejected("getSDim")
        return int(self.sdim if self.sdim is not None else 3)

    def lengthUnit(self) -> str:
        self.calls.append(("lengthUnit", ()))
        if "lengthUnit" in self.unavailable:
            raise _rejected("lengthUnit")
        return self.length_unit_value if self.length_unit_value is not None else "m"

    def getNEntities(self) -> list[int]:
        self.calls.append(("getNEntities", ()))
        if "getNEntities" in self.unavailable:
            raise _rejected("getNEntities")
        return list(self.measure_metrics.get("n_entities", []))

    def getNFiniteVoids(self) -> int:
        self.calls.append(("getNFiniteVoids", ()))
        if "getNFiniteVoids" in self.unavailable:
            raise _rejected("getNFiniteVoids")
        return int(self.measure_metrics.get("finite_voids", 0))

    def getBoundingBox(self) -> list[float]:
        self.calls.append(("getBoundingBox", ()))
        if "getBoundingBox" in self.unavailable:
            raise _rejected("getBoundingBox")
        return list(self.measure_metrics.get("bounding_box", []))

    def run(self, *args: Any) -> None:
        self.calls.append(("run", args))
        self.run_log.append(args)
        if "run" in self.unavailable:
            raise _rejected("run")
        if self.build_error is not None:
            raise self.build_error

    def create(self, tag: str, *type_id: Any) -> Any:
        if self.feature_list is None:
            raise AttributeError("create")
        return self.feature_list.create(tag, *type_id)

    def mutation_calls(self) -> list[tuple[str, tuple[Any, ...]]]:
        calls = [call for call in self.calls if call[0] in {"set", "run", "active", "create"}]
        if self.feature_list is not None:
            calls.extend(self.feature_list.mutation_calls())
        return calls

    def __getattr__(self, name: str) -> Any:
        errors = self.__dict__.get("errors") or {}
        if name in errors:
            raise errors[name]
        raise AttributeError(name)


class FComponent(FNode):
    def __init__(self, tag: str = "comp1", **kwargs: Any) -> None:
        kwargs.setdefault("type_id", "Component")
        super().__init__(tag=tag, **kwargs)


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
# builders
# ---------------------------------------------------------------------------

GEOM_MEASURE_METRICS = {
    "area": 0.25, "volume": 1.0e-6, "length": 0.5, "perimeter": 2.0,
    "boundary_area": 0.75, "boundary_volume": 2.5e-3,
    "bounding_box": [0.0, 1.0, 0.0, 1.0, 0.0, 1.0],
    "n_entities": [8, 12, 6, 1], "finite_voids": 0,
    "vtx_coord": [0.5, 0.5, 0.0], "vtx_distance": 0.25, "edge_angle": 1.5707963267948966,
}


def feature_node(tag: str, type_id: str, **kwargs: Any) -> FNode:
    """A geometry feature with the property metadata a real feature exposes."""
    kwargs.setdefault("value_types", {})
    kwargs.setdefault("values", {})
    return FNode(tag=tag, type_id=type_id, **kwargs)


def rectangle_feature(tag: str = "r1", *, size: Any = None, pos: Any = None, **kwargs: Any) -> FNode:
    value_types = {"size": "DoubleArray", "pos": "DoubleArray", "base": "String", "type": "String"}
    values = {"size": list(size or [1.0, 1.0]), "pos": list(pos or [0.0, 0.0]), "base": "corner", "type": "solid"}
    value_types.update(kwargs.pop("value_types", {}))
    values.update(kwargs.pop("values", {}))
    return feature_node(tag, "Rectangle", value_types=value_types, values=values, **kwargs)


def array_feature(tag: str = "arr1", **kwargs: Any) -> FNode:
    value_types = {"type": "String", "size": "IntArray", "fullsize": "DoubleArray", "displ": "DoubleArray"}
    values = {"type": "rectangular", "size": [2, 1], "fullsize": [1.0, 1.0], "displ": [0.5, 0.0]}
    allowed = {"type": ["linear", "rectangular", "three-dimensional"]}
    return feature_node(tag, "Array", value_types=value_types, values=values, allowed=allowed, **kwargs)


def workplane_feature(tag: str = "wp1", *, inner: Any = None, **kwargs: Any) -> FNode:
    value_types = {"planetype": "String", "quickplane": "String", "unite": "Boolean", "rot": "Double"}
    values = {"planetype": "quick", "quickplane": "xy", "unite": False, "rot": 0.0}
    allowed = {"planetype": ["quick", "faceparallel", "coordinates", "transformed", "normal", "vertices"]}
    return feature_node(
        tag, "WorkPlane", inner_sequence=inner, value_types=value_types, values=values, allowed=allowed, **kwargs
    )


def geometry(tag: str = "geom1", *, features: Mapping[str, FNode] | None = None,
             sdim: int = 3, measure_metrics: Mapping[str, Any] | None = None,
             unavailable: tuple[str, ...] = (), **kwargs: Any) -> FNode:
    feature_list = FList(arity=2, node_type="GeomFeature")
    for feature_tag, node in (features or {}).items():
        feature_list.items[feature_tag] = node
    feature_list.factory = lambda feature_tag, *args: feature_node(
        feature_tag, str(args[0]) if args else "Fake",
        value_types={"size": "DoubleArray", "pos": "DoubleArray", "base": "String", "type": "String"},
        values={"size": [1.0, 1.0], "pos": [0.0, 0.0], "base": "corner", "type": "solid"},
    )
    node = FNode(
        tag=tag, type_id="GeomSequence", feature_list=feature_list, sdim=sdim,
        measure_metrics=dict(measure_metrics or GEOM_MEASURE_METRICS),
        unavailable=unavailable, **kwargs,
    )
    return node


def finalize_feature(tag: str = "fin", type_id: str = "FormUnion", **kwargs: Any) -> FNode:
    value_types = {"action": "String", "pairtype": "String", "createpairs": "String",
                   "imprint": "Boolean", "repairtol": "Double"}
    values = {"action": "union", "pairtype": "identity", "createpairs": "off",
              "imprint": False, "repairtol": 1e-6}
    allowed = {"action": ["union", "assembly"]}
    return feature_node(tag, type_id, value_types=value_types, values=values, allowed=allowed, **kwargs)


def component(tag: str = "comp1", *, geometries: Mapping[str, FNode] | None = None,
              pairs: Mapping[str, FNode] | None = None, couplings: Mapping[str, FNode] | None = None,
              coordinate_systems: Mapping[str, FNode] | None = None, **kwargs: Any) -> FComponent:
    geom_list = FList(arity=2, node_type="GeomSequence")
    for geometry_tag, node in (geometries or {}).items():
        geom_list.items[geometry_tag] = node
    geom_list.factory = lambda geometry_tag, *args: geometry(
        geometry_tag, sdim=int(args[0]) if args else 3
    )
    pair_list = FList(arity=2, node_type="Pair")
    for pair_tag, node in (pairs or {}).items():
        pair_list.items[pair_tag] = node
    # pair().create(<tag>,type[,<gtag>]) and cpl().create(<tag>,type[,<gtag>]):
    # the type string is the first extra argument (Programming Reference
    # model.component().pair() / cpl() pages).
    pair_list.factory = lambda pair_tag, *args: FNode(tag=pair_tag, type_id=str(args[0]) if args else "Identity")
    cpl_list = FList(arity=2, node_type="Cpl")
    for cpl_tag, node in (couplings or {}).items():
        cpl_list.items[cpl_tag] = node
    cpl_list.factory = lambda cpl_tag, *args: FNode(tag=cpl_tag, type_id=str(args[0]) if args else "Integration")
    coord_list = FList(arity=3, node_type="Coordsys")
    for coord_tag, node in (coordinate_systems or {}).items():
        coord_list.items[coord_tag] = node
    coord_list.factory = lambda coord_tag, *args: FNode(
        tag=coord_tag, type_id=str(args[-1]) if args else "Cylindrical",
        value_types={"x": "DoubleArray", "frame": "String"},
    )
    return FComponent(
        tag=tag,
        measure_metrics=dict(GEOM_MEASURE_METRICS),
        measure_entities={2: [1, 2, 3], 1: [1, 2, 3, 4], 0: [1, 2, 3, 4, 5]},
        collections={
            "geom": geom_list, "pair": pair_list, "cpl": cpl_list, "coordSystem": coord_list,
            "selection": FList(arity=2), "material": FList(arity=1), "physics": FList(arity=2),
        },
        **kwargs,
    )


def model_with(*, components: Mapping[str, FNode] | None = None, **kwargs: Any) -> FModel:
    component_list = FList(arity=1, node_type="Component")
    for component_tag, node in (components or {}).items():
        component_list.items[component_tag] = node
    component_list.factory = lambda component_tag, *args: component(component_tag, type_id=str(args[0]) if args else "Component")
    return FModel(collections={"component": component_list, **kwargs.pop("collections", {})}, **kwargs)


def world(*, geometries: Mapping[str, FNode] | None = None, **component_kwargs: Any) -> tuple[FWorker, FModel, FComponent]:
    comp = component(geometries=geometries, **component_kwargs)
    model = model_with(components={"comp1": comp})
    return FWorker(model), model, comp


def expect_error(code: str, function: Any, *args: Any, **kwargs: Any) -> ExecutionContractError:
    with pytest.raises(ExecutionContractError) as info:
        function(*args, **kwargs)
    assert info.value.code == code, f"expected {code}, got {info.value.code}: {info.value}"
    return info.value


def geom_path(tag: str = "geom1") -> dict[str, Any]:
    return {"segments": [{"collection": "component", "tag": "comp1"}, {"collection": "geom", "tag": tag}]}


def feature_path(tag: str, geometry_tag: str = "geom1") -> dict[str, Any]:
    return {"segments": [
        {"collection": "component", "tag": "comp1"},
        {"collection": "geom", "tag": geometry_tag},
        {"collection": "feature", "tag": tag},
    ]}


def workplane_path(tag: str = "wp1", geometry_tag: str = "geom1") -> dict[str, Any]:
    return feature_path(tag, geometry_tag)


def call(op: str, worker: FWorker, arguments: Mapping[str, Any], model_tag: str = "Model") -> dict[str, Any]:
    return w14.OPERATIONS[op](worker, model_tag, dict(arguments))


# ---------------------------------------------------------------------------
# aggregator surface and verified vocabularies
# ---------------------------------------------------------------------------


class TestAggregator:
    def test_all_w14_operations_are_published(self):
        expected = {
            "geometry.sequence_create", "geometry.inspect", "geometry.feature_create",
            "geometry.feature_update", "geometry.feature_remove", "geometry.workplane_create",
            "geometry.workplane_edit", "geometry.array_create", "geometry.build",
            "geometry.finalize", "geometry.import", "geometry.measure", "geometry.validate",
            "definition.component_manage", "definition.coordinate_manage",
            "definition.pair_manage", "definition.coupling_manage",
        }
        assert expected == set(w14.OPERATIONS)
        assert expected <= IMPLEMENTED_OPERATIONS
        assert expected <= set(DISPATCH)
        assert DISPATCH["geometry.build"] is w14.OPERATIONS["geometry.build"]

    def test_isolation_set_is_exactly_the_non_read_effects(self):
        for operation_id, effect in EFFECTS.items():
            if effect == "READ":
                assert operation_id not in REQUIRES_ISOLATION
            else:
                assert operation_id in REQUIRES_ISOLATION

    def test_effects_for_w14_come_from_the_design_catalog(self):
        assert EFFECTS["geometry.inspect"] == "READ"
        assert EFFECTS["geometry.feature_create"] == "WRITE"
        assert EFFECTS["geometry.build"] == "COMPUTE"
        assert EFFECTS["geometry.measure"] == "EVALUATE"
        assert EFFECTS["geometry.validate"] == "EVALUATE"
        assert EFFECTS["geometry.import"] == "WRITE"
        assert EFFECTS["definition.component_manage"] == "DYNAMIC"
        assert EFFECTS["definition.coordinate_manage"] == "DYNAMIC"
        assert EFFECTS["definition.pair_manage"] == "DYNAMIC"
        assert EFFECTS["definition.coupling_manage"] == "DYNAMIC"

    def test_dispatch_rejects_an_unknown_operation(self):
        from comsol_mcp import _g3_ops

        worker, _, _ = world()
        expect_error("UNSUPPORTED_OPERATION", _g3_ops.dispatch, "geometry.not_an_operation", worker, "Model", {})


class TestVerifiedVocabulary:
    def test_geometry_type_vocabulary_matches_the_documented_command_pages(self):
        for type_id in ("Rectangle", "Block", "Array", "WorkPlane", "Import", "FormUnion",
                        "FormAssembly", "Move", "Copy", "Difference", "Extrude"):
            assert type_id in w14.GEOMETRY_FEATURE_TYPE_IDS
        # "Finalize" is the page name; the create type strings are FormUnion/FormAssembly.
        assert "Finalize" not in w14.GEOMETRY_FEATURE_TYPE_IDS

    def test_documented_property_tables_quote_the_pages(self):
        assert {"size", "pos", "base", "type"} <= w14.GEOMETRY_FEATURE_PROPERTIES["Rectangle"]
        assert {"size", "pos", "base", "axis", "axistype", "workplane"} <= w14.GEOMETRY_FEATURE_PROPERTIES["Block"]
        assert {"type", "size", "fullsize", "displ", "linearsize", "input"} <= w14.GEOMETRY_FEATURE_PROPERTIES["Array"]
        assert {"planetype", "quickplane", "unite", "displ", "rot"} <= w14.GEOMETRY_FEATURE_PROPERTIES["WorkPlane"]
        assert {"action", "pairtype", "createpairs", "imprint", "repairtol"} <= w14.GEOMETRY_FEATURE_PROPERTIES["FormUnion"]
        assert {"filename", "type", "sequence", "mesh", "includevirtual"} <= w14.GEOMETRY_FEATURE_PROPERTIES["Import"]

    def test_type_vocabularies_are_the_documented_ones(self):
        assert w14.PAIR_TYPE_IDS == {"Contact", "GeneralContact", "Identity", "SectorSymmetry"}
        assert "Integration" in w14.COUPLING_TYPE_IDS and "GeneralExtrusion" in w14.COUPLING_TYPE_IDS
        assert "Minimum" in w14.COUPLING_TYPE_IDS and len(w14.COUPLING_TYPE_IDS) == 10
        assert "Cylindrical" in w14.COORDINATE_SYSTEM_TYPE_IDS
        assert "Boundary" in w14.COORDINATE_SYSTEM_TYPE_IDS
        assert "SystemFromGeometry" in w14.COORDINATE_SYSTEM_TYPE_IDS
        assert {"Spherical", "FromGeometry", "Combined", "Composite"} <= w14.COORDINATE_SYSTEM_TYPE_IDS
        assert w14.COMPONENT_TYPE_IDS == {"Component", "ExtraDim", "MeshComponent"}

    def test_unverified_type_strings_are_kept_out_of_the_accepted_vocabulary(self):
        assert w14.GEOMETRY_FEATURE_TYPE_IDS_UNVERIFIED == {
            "CompositeCurve", "FromMesh", "If", "ElseIf", "Else", "EndIf",
        }
        assert not (w14.GEOMETRY_FEATURE_TYPE_IDS_UNVERIFIED & w14.GEOMETRY_FEATURE_TYPE_IDS)
        assert w14.GEOMETRY_SEQUENCE_TYPE_IDS_UNSUPPORTED == {"Part"}
        assert not (w14.GEOMETRY_SEQUENCE_TYPE_IDS_UNSUPPORTED & w14.GEOMETRY_FEATURE_TYPE_IDS)


class TestWorkerAllowListReality:
    """The refusal table must still describe the installed Java worker."""

    @staticmethod
    def _worker_methods() -> set[str]:
        source = open(
            os.path.join(os.path.dirname(__file__), "..", "comsol_mcp", "worker_java",
                         "PersistentComsolWorker.java"),
            encoding="utf-8",
        ).read()
        block = source.split("private static final Set<String> METHODS", 1)[1]
        block = block.split("private static final Set<String> MODEL_UTIL", 1)[0]
        return set(re.findall(r'"([A-Za-z_][A-Za-z0-9_]*)"', block))

    def test_unavailable_methods_are_still_absent_from_the_java_allow_list(self):
        methods = self._worker_methods()
        overlap = sorted(w14.WORKER_UNAVAILABLE_METHODS & methods)
        assert overlap == [], (
            f"the worker allow-list now contains {overlap}; the W14 refusal table "
            f"(WORKER_UNAVAILABLE_METHODS) must be repaired to use those methods"
        )

    def test_methods_the_implementation_relies_on_are_allow_listed(self):
        methods = self._worker_methods()
        required = {
            "create", "remove", "tags", "get", "set", "properties", "getValueType",
            "getAllowedPropertyValues", "getString", "getDouble", "getInt", "getBoolean",
            "getStringArray", "getDoubleArray", "getIntArray", "getBooleanArray",
            "feature", "geom", "run", "getSDim", "lengthUnit", "getNEntities", "getBoundingBox",
            "getNFiniteVoids", "measure", "selection", "named", "all", "entities", "active",
            "getType", "label", "component", "pair", "cpl", "coordSystem", "material", "physics",
        }
        missing = sorted(required - methods)
        assert missing == [], f"the implementation needs allow-list entries that are absent: {missing}"


# ---------------------------------------------------------------------------
# geometry.sequence_create
# ---------------------------------------------------------------------------


class TestSequenceCreate:
    def test_creates_a_sequence_with_readback(self):
        worker, model, comp = world()
        result = call("geometry.sequence_create", worker,
                      {"component": "comp1", "tag": "geom2", "dimension": 2})
        assert result["created"] is True
        assert result["path"] == geom_path("geom2")
        assert result["dimension"] == 2 and result["dimension_readback"] == 2
        assert result["length_unit"] == "m"
        assert comp.collections["geom"].items["geom2"] is not None
        assert result["unavailable"]["axisymmetric"]["allowlist_entry_required"] == "axisymmetric"

    def test_reports_the_created_dimension_when_it_matches(self):
        worker, _, comp = world()
        comp.collections["geom"].factory = lambda tag, *args: geometry(tag, sdim=int(args[0]))
        result = call("geometry.sequence_create", worker,
                      {"component": "comp1", "tag": "geom3", "dimension": 3})
        assert result["created"] is True
        assert result["dimension_readback"] == 3
        assert result["features"] == []

    def test_tag_conflict_is_refused_before_the_create_call(self):
        worker, _, comp = world(geometries={"geom1": geometry("geom1")})
        geom_list = comp.collections["geom"]
        before = list(geom_list.mutation_calls())
        expect_error("TAG_CONFLICT", call, "geometry.sequence_create", worker,
                     {"component": "comp1", "tag": "geom1", "dimension": 3})
        assert geom_list.mutation_calls() == before

    def test_axisymmetric_is_refused_before_the_write(self):
        worker, _, comp = world()
        geom_list = comp.collections["geom"]
        error = expect_error("API_UNSUPPORTED", call, "geometry.sequence_create", worker,
                             {"component": "comp1", "tag": "geom1", "dimension": 2, "axisymmetric": True})
        assert "axisymmetric" in str(error)
        assert geom_list.mutation_calls() == []

    def test_missing_component_is_a_pre_write_refusal(self):
        worker, _, _ = world()
        expect_error("NODE_NOT_FOUND", call, "geometry.sequence_create", worker,
                     {"component": "comp9", "tag": "geom1", "dimension": 3})

    def test_dimension_readback_mismatch_is_reported_as_execution_state_unknown(self):
        worker, _, comp = world()
        comp.collections["geom"].factory = lambda tag, *args: geometry(tag, sdim=99)
        expect_error("EXECUTION_STATE_UNKNOWN", call, "geometry.sequence_create", worker,
                     {"component": "comp1", "tag": "geom1", "dimension": 3})

    def test_missing_required_field_is_refused(self):
        worker, _, _ = world()
        expect_error("INVALID_REQUEST", call, "geometry.sequence_create", worker, {"component": "comp1"})
        expect_error("INVALID_REQUEST", call, "geometry.sequence_create", worker,
                     {"component": "comp1", "tag": "geom1", "dimension": 0})
        expect_error("INVALID_REQUEST", call, "geometry.sequence_create", worker,
                     {"component": "comp1", "tag": "geom1", "dimension": 3, "unexpected": 1})


# ---------------------------------------------------------------------------
# geometry.inspect
# ---------------------------------------------------------------------------


class TestInspect:
    def test_reads_features_counters_and_boxes(self):
        wp = workplane_feature("wp1", inner=geometry("wp_geom", sdim=2))
        geom = geometry("geom1", features={"r1": rectangle_feature("r1"), "wp1": wp})
        worker, _, _ = world(geometries={"geom1": geom})
        result = call("geometry.inspect", worker, {"path": geom_path("geom1")})
        assert result["kind"] == "geometry_sequence"
        assert result["feature_count"] == 2
        assert [row["tag"] for row in result["features"]] == ["r1", "wp1"]
        assert result["features"][1]["type_id"] == "WorkPlane"
        assert result["features"][0]["enabled"] is True
        assert "size" in result["features"][0]["properties"]
        assert result["geometry_state"]["entity_counters"]["n_entities"] == [8, 12, 6, 1]
        assert result["geometry_state"]["bounding_box"]["value"][0] == 0.0
        assert result["geometry_state"]["unavailable"]["problems"]["allowlist_entry_required"] == "problems"

    def test_resolves_a_workplanes_nested_sequence(self):
        inner = geometry("wp_geom", sdim=2, features={"r1": rectangle_feature("r1")})
        wp = workplane_feature("wp1", inner=inner)
        geom = geometry("geom1", features={"wp1": wp})
        worker, _, _ = world(geometries={"geom1": geom})
        result = call("geometry.inspect", worker, {"path": workplane_path("wp1")})
        assert result["kind"] == "workplane_sequence"
        assert result["workplane"] == "wp1"
        assert [row["tag"] for row in result["features"]] == ["r1"]

    def test_feature_property_readback_is_bounded_by_the_request(self):
        geom = geometry("geom1", features={"r1": rectangle_feature("r1", size=[3.0, 4.0])})
        worker, _, _ = world(geometries={"geom1": geom})
        result = call("geometry.inspect", worker, {
            "path": geom_path("geom1"), "feature_properties": {"r1": ["base", "size"]},
        })
        rows = result["features"][0]["property_values"]
        assert rows["base"]["value"] == "corner"
        # the bounded reader is getString/getDouble only, so an array-valued
        # property is reported as unread instead of being silently coerced
        assert rows["size"]["value"] is None and rows["size"]["error"] is not None
        assert result["features"][0]["properties"] == ["size", "pos", "base", "type"]

    def test_missing_node_and_bad_path_are_refused(self):
        worker, _, _ = world(geometries={"geom1": geometry("geom1")})
        expect_error("NODE_NOT_FOUND", call, "geometry.inspect", worker, {"path": geom_path("geom9")})
        expect_error("INVALID_NODE_PATH", call, "geometry.inspect", worker,
                     {"path": {"segments": [{"collection": "component", "tag": "comp1"}]}})
        expect_error("INVALID_REQUEST", call, "geometry.inspect", worker,
                     {"path": geom_path("geom1"), "unexpected": True})

    def test_a_non_workplane_feature_path_is_refused(self):
        geom = geometry("geom1", features={"r1": rectangle_feature("r1")})
        worker, _, _ = world(geometries={"geom1": geom})
        error = expect_error("INVALID_NODE_PATH", call, "geometry.inspect", worker,
                             {"path": feature_path("r1")})
        assert "WorkPlane" in str(error)


# ---------------------------------------------------------------------------
# geometry.feature_create / update / remove
# ---------------------------------------------------------------------------


class TestFeatureCreate:
    def test_creates_a_rectangle_and_writes_documented_properties(self):
        geom = geometry("geom1")
        worker, _, _ = world(geometries={"geom1": geom})
        result = call("geometry.feature_create", worker, {
            "parent": geom_path("geom1"), "tag": "r1", "type_id": "Rectangle",
            "definition": {"size": [2.0, 1.0], "pos": [0.5, 0.0], "base": "center"},
        })
        assert result["ok"] is True and result["status"] == "APPLIED"
        assert result["type_readback"] == "Rectangle"
        assert [row["name"] for row in result["applied"]] == ["size", "pos", "base"]
        assert result["properties"]["size"]["data"] == [2.0, 1.0]
        assert result["property_source"] == "documented_property_table"
        assert result["feature_tags"] == ["r1"]

    def test_type_conflict_is_refused_before_the_create_call(self):
        geom = geometry("geom1", features={"f1": rectangle_feature("f1")})
        worker, _, _ = world(geometries={"geom1": geom})
        before = list(geom.feature_list.mutation_calls())
        expect_error("TYPE_CONFLICT", call, "geometry.feature_create", worker, {
            "parent": geom_path("geom1"), "tag": "f1", "type_id": "Block",
        })
        assert geom.feature_list.mutation_calls() == before

    def test_same_tag_same_type_is_a_tag_conflict(self):
        geom = geometry("geom1", features={"f1": rectangle_feature("f1")})
        worker, _, _ = world(geometries={"geom1": geom})
        expect_error("TAG_CONFLICT", call, "geometry.feature_create", worker, {
            "parent": geom_path("geom1"), "tag": "f1", "type_id": "Rectangle",
        })

    def test_unverified_type_string_is_refused(self):
        geom = geometry("geom1")
        worker, _, _ = world(geometries={"geom1": geom})
        error = expect_error("API_UNSUPPORTED", call, "geometry.feature_create", worker, {
            "parent": geom_path("geom1"), "tag": "x1", "type_id": "NotAFeature",
        })
        assert "vocabulary" in str(error)
        expect_error("API_UNSUPPORTED", call, "geometry.feature_create", worker, {
            "parent": geom_path("geom1"), "tag": "x1", "type_id": "Finalize",
        })
        # documented commands whose create() type string was not retrieved offline
        refused = expect_error("API_UNSUPPORTED", call, "geometry.feature_create", worker, {
            "parent": geom_path("geom1"), "tag": "x1", "type_id": "FromMesh",
        })
        assert "before the first engine change" in str(refused)
        # a geometry *sequence* type is not a feature type
        expect_error("API_UNSUPPORTED", call, "geometry.feature_create", worker, {
            "parent": geom_path("geom1"), "tag": "x1", "type_id": "Part",
        })
        assert geom.feature_list.mutation_calls() == []

    def test_unknown_property_name_is_refused_before_the_create_call(self):
        geom = geometry("geom1")
        worker, _, _ = world(geometries={"geom1": geom})
        before = list(geom.feature_list.mutation_calls())
        expect_error("INVALID_REQUEST", call, "geometry.feature_create", worker, {
            "parent": geom_path("geom1"), "tag": "r1", "type_id": "Rectangle",
            "definition": {"not_a_documented_property": 1.0},
        })
        assert geom.feature_list.mutation_calls() == before

    def test_finalize_tag_is_fixed(self):
        geom = geometry("geom1")
        worker, _, _ = world(geometries={"geom1": geom})
        error = expect_error("INVALID_REQUEST", call, "geometry.feature_create", worker, {
            "parent": geom_path("geom1"), "tag": "fin2", "type_id": "FormUnion",
        })
        assert "fin" in str(error)
        assert geom.feature_list.mutation_calls() == []

    def test_type_without_a_retrieved_table_needs_the_engine_property_source(self):
        geom = geometry("geom1")
        worker, _, _ = world(geometries={"geom1": geom})
        error = expect_error("API_UNSUPPORTED", call, "geometry.feature_create", worker, {
            "parent": geom_path("geom1"), "tag": "ext1", "type_id": "Extrude", "definition": {"distance": "1"},
        })
        assert "property_source='engine'" in str(error)
        assert geom.feature_list.mutation_calls() == []

    def test_engine_property_source_uses_the_nodes_own_enumeration(self):
        geom = geometry("geom1")
        worker, _, _ = world(geometries={"geom1": geom})

        def factory(tag: str, *args: Any) -> FNode:
            return FNode(tag=tag, type_id=str(args[0]), value_types={"distance": "String"},
                         values={"distance": "1"})

        geom.feature_list.factory = factory
        result = call("geometry.feature_create", worker, {
            "parent": geom_path("geom1"), "tag": "ext1", "type_id": "Extrude", "definition": {"distance": "0.5"},
            "property_source": "engine",
        })
        assert result["ok"] is True
        assert result["property_source"] == "engine_property_enumeration"
        assert geom.feature_list.items["ext1"].values["distance"] == "0.5"

    def test_engine_property_source_reports_a_name_the_node_does_not_expose(self):
        geom = geometry("geom1")
        worker, _, _ = world(geometries={"geom1": geom})

        def factory(tag: str, *args: Any) -> FNode:
            return FNode(tag=tag, type_id=str(args[0]), value_types={"distance": "String"}, values={"distance": "1"})

        geom.feature_list.factory = factory
        result = call("geometry.feature_create", worker, {
            "parent": geom_path("geom1"), "tag": "ext1", "type_id": "Extrude", "definition": {"twist": "30"},
            "property_source": "engine",
        })
        assert result["ok"] is False
        assert result["status"] == "FAILED"
        assert result["partial_change"] is True
        assert result["failed"][0]["stage"] == "property_payload"
        assert result["failed"][0]["feature_created"] is True
        assert geom.feature_list.items["ext1"].values.get("twist") is None

    def test_unknown_value_metadata_is_a_post_create_data_failure(self):
        geom = geometry("geom1")
        worker, _, _ = world(geometries={"geom1": geom})

        def factory(tag: str, *args: Any) -> FNode:
            return FNode(tag=tag, type_id=str(args[0]), value_types={"size": None})

        geom.feature_list.factory = factory
        result = call("geometry.feature_create", worker, {
            "parent": geom_path("geom1"), "tag": "r1", "type_id": "Rectangle", "definition": {"size": [1.0, 1.0]},
        })
        assert result["status"] == "FAILED" and result["partial_change"] is True
        assert result["failed"][0]["code"] == "API_UNSUPPORTED"
        assert geom.feature_list.items["r1"] is not None

    def test_readback_mismatch_stays_data(self):
        geom = geometry("geom1")
        worker, _, _ = world(geometries={"geom1": geom})

        def factory(tag: str, *args: Any) -> FNode:
            return FNode(tag=tag, type_id=str(args[0]), value_types={"size": "DoubleArray"},
                         values={"size": [9.0, 9.0]}, readback_overrides={"size": [9.0, 9.0]})

        geom.feature_list.factory = factory
        result = call("geometry.feature_create", worker, {
            "parent": geom_path("geom1"), "tag": "r1", "type_id": "Rectangle", "definition": {"size": [2.0, 1.0]},
        })
        assert result["status"] == "EXECUTION_STATE_UNKNOWN"
        assert result["execution_state_unknown"] is True and result["partial_change"] is True
        assert result["failed"] and result["failed"][0]["name"] == "size"

    def test_input_selection_binding_and_readback(self):
        geom = geometry("geom1")
        worker, _, _ = world(geometries={"geom1": geom})

        def factory(tag: str, *args: Any) -> FNode:
            return array_feature(tag)

        geom.feature_list.factory = factory
        result = call("geometry.feature_create", worker, {
            "parent": geom_path("geom1"), "tag": "arr1", "type_id": "Array",
            "definition": {"type": "rectangular", "size": [2, 1]},
            "inputs": {"input": ["r1"]},
        })
        assert result["ok"] is True
        assert result["inputs"]["applied"][0]["objects"] == ["r1"]
        node = geom.feature_list.items["arr1"]
        assert node.selection("input").objects == ["r1"]

    def test_undocumented_input_selection_name_is_refused_before_the_create(self):
        geom = geometry("geom1")
        worker, _, _ = world(geometries={"geom1": geom})
        expect_error("INVALID_REQUEST", call, "geometry.feature_create", worker, {
            "parent": geom_path("geom1"), "tag": "arr1", "type_id": "Array", "inputs": {"input2": ["r1"]},
        })
        assert geom.feature_list.mutation_calls() == []

    def test_missing_parent_is_refused(self):
        geom = geometry("geom1")
        worker, _, _ = world(geometries={"geom1": geom})
        expect_error("NODE_NOT_FOUND", call, "geometry.feature_create", worker,
                     {"parent": geom_path("geom8"), "tag": "r1", "type_id": "Rectangle"})

    def test_feature_create_accepts_the_properties_alias(self):
        geom = geometry("geom1")
        worker, _, _ = world(geometries={"geom1": geom})
        geom.feature_list.factory = lambda tag, *args: rectangle_feature(tag)
        result = call("geometry.feature_create", worker, {
            "parent": geom_path("geom1"), "tag": "r1", "type_id": "Rectangle", "properties": {"pos": [1.0, 2.0]},
        })
        assert result["ok"] is True
        assert result["properties"]["pos"]["data"] == [1.0, 2.0]


class TestFeatureUpdate:
    def test_updates_properties_with_readback(self):
        geom = geometry("geom1", features={"r1": rectangle_feature("r1")})
        worker, _, _ = world(geometries={"geom1": geom})
        result = call("geometry.feature_update", worker, {
            "path": feature_path("r1"), "definition": {"size": [5.0, 2.0]},
        })
        assert result["ok"] is True and result["status"] == "APPLIED"
        assert result["properties"]["size"]["data"] == [5.0, 2.0]
        assert geom.feature_list.items["r1"].values["size"] == [5.0, 2.0]

    def test_missing_property_or_inputs_is_refused(self):
        geom = geometry("geom1", features={"r1": rectangle_feature("r1")})
        worker, _, _ = world(geometries={"geom1": geom})
        expect_error("INVALID_REQUEST", call, "geometry.feature_update", worker, {"path": feature_path("r1")})

    def test_path_must_end_in_a_feature_segment(self):
        worker, _, _ = world(geometries={"geom1": geometry("geom1")})
        expect_error("INVALID_NODE_PATH", call, "geometry.feature_update", worker,
                     {"path": geom_path("geom1"), "definition": {"size": [1.0, 1.0]}})

    def test_unreadable_type_is_refused(self):
        geom = geometry("geom1", features={"f1": FNode(tag="f1", type_id="Rectangle",
                                                      unavailable=("getType",))})
        worker, _, _ = world(geometries={"geom1": geom})
        expect_error("API_UNSUPPORTED", call, "geometry.feature_update", worker,
                     {"path": feature_path("f1"), "definition": {"size": [1.0, 1.0]}})

    def test_unknown_name_for_an_undocumented_type_is_refused_before_the_write(self):
        geom = geometry("geom1", features={"ext1": FNode(tag="ext1", type_id="Extrude",
                                                         value_types={"distance": "String"},
                                                         values={"distance": "1"})})
        worker, _, _ = world(geometries={"geom1": geom})
        node = geom.feature_list.items["ext1"]
        before = len(node.calls)
        expect_error("API_UNSUPPORTED", call, "geometry.feature_update", worker,
                     {"path": feature_path("ext1"), "definition": {"twist": "30"}})
        assert not [call_row for call_row in node.calls[before:] if call_row[0] == "set"]
        result = call("geometry.feature_update", worker, {
            "path": feature_path("ext1"), "definition": {"distance": "2"}, "property_source": "engine",
        })
        assert result["ok"] is True and node.values["distance"] == "2"

    def test_update_readback_mismatch_is_data(self):
        geom = geometry("geom1", features={
            "r1": rectangle_feature("r1", readback_overrides={"size": [7.0, 7.0]}),
        })
        worker, _, _ = world(geometries={"geom1": geom})
        result = call("geometry.feature_update", worker, {
            "path": feature_path("r1"), "definition": {"size": [1.0, 1.0]},
        })
        assert result["status"] == "EXECUTION_STATE_UNKNOWN"
        assert result["execution_state_unknown"] is True
        assert result["not_executed_count"] == 0

    def test_inputs_binding_on_update(self):
        geom = geometry("geom1", features={"mov1": FNode(tag="mov1", type_id="Move",
                                                         value_types={"displx": "Double"},
                                                         values={"displx": 0.0})})
        worker, _, _ = world(geometries={"geom1": geom})
        result = call("geometry.feature_update", worker, {
            "path": feature_path("mov1"), "inputs": {"input": ["r1"]},
        })
        assert result["inputs"]["applied"][0]["objects"] == ["r1"]
        assert result["feature_tags"] == ["mov1"]


class TestFeatureRemove:
    def test_removes_a_feature_with_readback(self):
        geom = geometry("geom1", features={"r1": rectangle_feature("r1"), "wp1": workplane_feature("wp1")})
        worker, _, _ = world(geometries={"geom1": geom})
        result = call("geometry.feature_remove", worker, {"path": feature_path("r1")})
        assert result["removed"] is True and result["tag"] == "r1"
        assert result["feature_tags"] == ["wp1"]
        assert result["dependency_risk"]["level"] == "UNSCANNED"

    def test_missing_feature_is_refused_pre_write(self):
        geom = geometry("geom1", features={"r1": rectangle_feature("r1")})
        worker, _, _ = world(geometries={"geom1": geom})
        before = list(geom.feature_list.mutation_calls())
        expect_error("NODE_NOT_FOUND", call, "geometry.feature_remove", worker, {"path": feature_path("r9")})
        assert geom.feature_list.mutation_calls() == before

    def test_a_remove_that_does_not_take_effect_is_unknown(self):
        geom = geometry("geom1", features={"r1": rectangle_feature("r1")})
        geom.feature_list.unavailable = {"remove"}
        worker, _, _ = world(geometries={"geom1": geom})
        expect_error("ENGINE_CALL_FAILED", call, "geometry.feature_remove", worker, {"path": feature_path("r1")})

    def test_path_must_end_in_a_feature(self):
        worker, _, _ = world(geometries={"geom1": geometry("geom1")})
        expect_error("INVALID_NODE_PATH", call, "geometry.feature_remove", worker, {"path": geom_path("geom1")})


# ---------------------------------------------------------------------------
# work planes
# ---------------------------------------------------------------------------


class TestWorkplaneCreate:
    def test_creates_a_workplane_and_writes_the_documented_location_properties(self):
        geom = geometry("geom1")
        worker, _, _ = world(geometries={"geom1": geom})
        geom.feature_list.factory = lambda tag, *args: workplane_feature(tag)
        result = call("geometry.workplane_create", worker, {
            "geometry": geom_path("geom1"), "tag": "wp1",
            "definition": {"planetype": "quick", "quickplane": "yz", "unite": True},
        })
        assert result["ok"] is True
        assert result["type_readback"] == "WorkPlane"
        node = geom.feature_list.items["wp1"]
        assert node.values["quickplane"] == "yz" and node.values["unite"] is True

    def test_an_unknown_property_name_is_refused_before_the_create(self):
        geom = geometry("geom1")
        worker, _, _ = world(geometries={"geom1": geom})
        expect_error("INVALID_REQUEST", call, "geometry.workplane_create", worker, {
            "geometry": geom_path("geom1"), "tag": "wp1", "definition": {"quicksizex": 1.0},
        })
        assert geom.feature_list.mutation_calls() == []

    def test_geometry_tag_conflict(self):
        geom = geometry("geom1", features={"wp1": workplane_feature("wp1")})
        worker, _, _ = world(geometries={"geom1": geom})
        expect_error("TAG_CONFLICT", call, "geometry.workplane_create", worker,
                     {"geometry": geom_path("geom1"), "tag": "wp1"})


class TestWorkplaneEdit:
    def _world(self) -> tuple[FWorker, FNode, FNode]:
        inner = geometry("wp_geom", sdim=2, features={"r1": rectangle_feature("r1", size=[2.0, 1.0], pos=[0.0, 0.0])})
        wp = workplane_feature("wp1", inner=inner)
        geom = geometry("geom1", features={"wp1": wp, "blk1": feature_node("blk1", "Block")})
        worker, _, _ = world(geometries={"geom1": geom})
        return worker, geom, inner

    def test_creates_an_array_and_preserves_siblings(self):
        worker, parent, inner = self._world()
        inner.feature_list.factory = lambda tag, *args: array_feature(tag)
        result = call("geometry.workplane_edit", worker, {
            "workplane": workplane_path("wp1"),
            "actions": [
                {"action": "create", "tag": "arr1", "type_id": "Array",
                 "definition": {"type": "rectangular", "size": [2, 1], "displ": [0.002, 0.0]},
                 "inputs": {"input": ["r1"]}},
                {"action": "build"},
            ],
        })
        assert result["ok"] is True
        assert result["features_before"] == ["r1"]
        assert result["features_after"] == ["r1", "arr1"]
        assert result["preserved_features"] == ["r1"]
        assert inner.feature_list.items["arr1"].selection("input").objects == ["r1"]
        assert parent.run_log == [("wp1",)]
        assert result["geometry_state"]["entity_counters"]["n_entities"] == [8, 12, 6, 1]

    def test_the_array_edit_is_quantifiable_from_the_readback(self):
        """T009: after the work-plane array edit the left-side object survives and the
        array's count, position and spacing can be read back from the engine."""
        worker, _, inner = self._world()
        inner.feature_list.factory = lambda tag, *args: array_feature(tag)
        result = call("geometry.workplane_edit", worker, {
            "workplane": workplane_path("wp1"),
            "actions": [
                {"action": "create", "tag": "arr1", "type_id": "Array",
                 "definition": {"type": "rectangular", "size": [3, 1], "fullsize": [0.004, 0.001],
                                "displ": [0.002, 0.0]},
                 "inputs": {"input": ["r1"]}},
            ],
        })
        created = result["applied"][0]
        assert created["tag"] == "arr1"
        # count, spacing and extent are the engine's own readback of the write
        assert created["properties"]["size"]["data"] == [3, 1]
        assert created["properties"]["displ"]["data"] == [0.002, 0.0]
        assert created["properties"]["fullsize"]["data"] == [0.004, 0.001]
        assert created["inputs"]["applied"][0]["objects"] == ["r1"]
        # and the same values are readable through a follow-up inspect of the
        # work plane's nested sequence, whose sibling list still holds r1
        inspected = call("geometry.inspect", worker, {
            "path": workplane_path("wp1"), "feature_properties": {"arr1": ["type"]},
        })
        assert inspected["kind"] == "workplane_sequence"
        assert [row["tag"] for row in inspected["features"]] == ["r1", "arr1"]
        assert "property_values" not in inspected["features"][0]  # not requested for r1
        rows = inspected["features"][1]
        assert rows["property_values"]["type"]["value"] == "rectangular"
        assert rows["properties"] == ["type", "size", "fullsize", "displ"]
        assert inspected["geometry_state"]["bounding_box"]["value"] == [0.0, 1.0, 0.0, 1.0, 0.0, 1.0]
        assert inspected["geometry_state"]["entity_counters"]["n_entities"] == [8, 12, 6, 1]

    def test_a_failing_action_stops_the_plan_and_reports_not_executed(self):
        worker, _, inner = self._world()
        result = call("geometry.workplane_edit", worker, {
            "workplane": workplane_path("wp1"),
            "actions": [
                {"action": "create", "tag": "r1", "type_id": "Rectangle", "definition": {"size": [2, 2]}},
                {"action": "create", "tag": "arr1", "type_id": "Array"},
            ],
        })
        assert result["ok"] is False and result["status"] == "FAILED"
        assert result["failed"][0]["code"] == "TAG_CONFLICT"
        assert result["failed"][0]["action"] == "create"
        assert result["not_executed"] and result["not_executed"][0]["action"] == "create"
        assert result["preserved_features"] == ["r1"]

    def test_a_build_failure_is_reported_as_execution_state_unknown(self):
        worker, parent, _ = self._world()
        parent.build_error = FakeEngineError("geometry build failed at wp1")
        result = call("geometry.workplane_edit", worker, {
            "workplane": workplane_path("wp1"), "actions": [{"action": "build"}],
        })
        assert result["status"] == "EXECUTION_STATE_UNKNOWN"
        assert result["execution_state_unknown"] is True
        assert result["partial_change"] is True

    def test_remove_and_set_active_actions(self):
        worker, _, inner = self._world()
        inner.feature_list.items["r2"] = rectangle_feature("r2")
        result = call("geometry.workplane_edit", worker, {
            "workplane": workplane_path("wp1"),
            "actions": [
                {"action": "set_active", "tag": "r2", "enabled": False},
                {"action": "remove", "tag": "r2"},
            ],
        })
        assert result["ok"] is True
        assert result["removed_features"] == ["r2"]
        assert result["features_after"] == ["r1"]

    def test_set_active_readback_mismatch_is_unknown(self):
        worker, _, inner = self._world()

        class Stubborn(FNode):
            def active(self, *args: Any) -> Any:
                return True

        inner.feature_list.items["r2"] = Stubborn(tag="r2", type_id="Rectangle")
        result = call("geometry.workplane_edit", worker, {
            "workplane": workplane_path("wp1"),
            "actions": [{"action": "set_active", "tag": "r2", "enabled": False}],
        })
        assert result["status"] == "EXECUTION_STATE_UNKNOWN"
        assert result["failed"][0]["action"] == "set_active"

    def test_inner_build_with_until_tag_uses_the_nested_sequence(self):
        worker, _, inner = self._world()
        inner.feature_list.items["r2"] = rectangle_feature("r2")
        result = call("geometry.workplane_edit", worker, {
            "workplane": workplane_path("wp1"), "actions": [{"action": "build", "until_tag": "r2"}],
        })
        assert result["applied"][0]["scope"] == "nested_sequence_until_feature"
        assert inner.run_log == [("r2",)]

    def test_an_unknown_until_tag_is_refused_before_the_build(self):
        worker, _, inner = self._world()
        result = call("geometry.workplane_edit", worker, {
            "workplane": workplane_path("wp1"), "actions": [{"action": "build", "until_tag": "nope"}],
        })
        assert result["status"] == "FAILED"
        assert inner.run_log == []

    def test_actions_are_validated_before_any_write(self):
        worker, _, inner = self._world()
        expect_error("INVALID_REQUEST", call, "geometry.workplane_edit", worker,
                     {"workplane": workplane_path("wp1"), "actions": [{"action": "explode"}]})
        expect_error("INVALID_REQUEST", call, "geometry.workplane_edit", worker,
                     {"workplane": workplane_path("wp1"), "actions": []})
        expect_error("INVALID_REQUEST", call, "geometry.workplane_edit", worker,
                     {"workplane": workplane_path("wp1"), "actions": [{"action": "create", "tag": "r9"}]})
        assert inner.feature_list.mutation_calls() == []

    def test_a_geometry_path_is_not_a_workplane_edit_target(self):
        worker, _, _ = self._world()
        expect_error("INVALID_NODE_PATH", call, "geometry.workplane_edit", worker,
                     {"workplane": geom_path("geom1"), "actions": [{"action": "build"}]})


# ---------------------------------------------------------------------------
# arrays, build, finalize, import
# ---------------------------------------------------------------------------


class TestArrayCreate:
    def test_creates_a_rectangular_array_and_quantifies_the_result(self):
        geom = geometry("geom1")
        geom.feature_list.factory = lambda tag, *args: array_feature(tag)
        worker, _, _ = world(geometries={"geom1": geom})
        result = call("geometry.array_create", worker, {
            "geometry": geom_path("geom1"), "tag": "arr1",
            "definition": {"type": "rectangular", "size": [3, 1], "fullsize": [0.004, 0.001],
                           "displ": [0.002, 0.0]},
            "inputs": {"input": ["r1"]},
        })
        assert result["ok"] is True
        assert result["array_type"] == "rectangular"
        assert result["properties"]["fullsize"]["data"] == [0.004, 0.001]
        assert result["inputs"]["applied"][0]["objects"] == ["r1"]

    def test_an_undocumented_array_type_is_refused_before_the_write(self):
        geom = geometry("geom1")
        worker, _, _ = world(geometries={"geom1": geom})
        expect_error("API_UNSUPPORTED", call, "geometry.array_create", worker, {
            "geometry": geom_path("geom1"), "tag": "arr1", "definition": {"type": "polar"},
        })
        assert geom.feature_list.mutation_calls() == []

    def test_definition_is_required_and_strict(self):
        worker, _, _ = world(geometries={"geom1": geometry("geom1")})
        expect_error("INVALID_REQUEST", call, "geometry.array_create", worker, {"geometry": geom_path("geom1")})
        expect_error("INVALID_REQUEST", call, "geometry.array_create", worker, {
            "geometry": geom_path("geom1"), "tag": "arr1", "definition": {"shape": [2, 2]},
        })

    def test_array_readback_mismatch_is_data(self):
        geom = geometry("geom1")
        geom.feature_list.factory = lambda tag, *args: array_feature(tag, readback_overrides={"fullsize": [9.0, 9.0]})
        worker, _, _ = world(geometries={"geom1": geom})
        result = call("geometry.array_create", worker, {
            "geometry": geom_path("geom1"), "tag": "arr1",
            "definition": {"type": "rectangular", "fullsize": [0.004, 0.001]},
        })
        assert result["status"] == "EXECUTION_STATE_UNKNOWN"


class TestBuild:
    def test_run_all_and_read_the_state(self):
        geom = geometry("geom1", features={"r1": rectangle_feature("r1")})
        worker, _, _ = world(geometries={"geom1": geom})
        result = call("geometry.build", worker, {"geometry": geom_path("geom1")})
        assert result["ok"] is True and result["built"] is True
        assert result["scope"] == "all_features"
        assert geom.run_log == [()]
        assert result["geometry_state"]["entity_counters"]["n_entities"] == [8, 12, 6, 1]

    def test_run_until_a_feature(self):
        geom = geometry("geom1", features={"r1": rectangle_feature("r1"), "arr1": array_feature("arr1")})
        worker, _, _ = world(geometries={"geom1": geom})
        result = call("geometry.build", worker, {"geometry": geom_path("geom1"), "until_tag": "r1"})
        assert result["scope"] == "feature_and_preceding"
        assert geom.run_log == [("r1",)]

    def test_unknown_until_tag_is_refused_before_the_run(self):
        geom = geometry("geom1", features={"r1": rectangle_feature("r1")})
        worker, _, _ = world(geometries={"geom1": geom})
        expect_error("NODE_NOT_FOUND", call, "geometry.build", worker,
                     {"geometry": geom_path("geom1"), "until_tag": "nope"})
        assert geom.run_log == []

    def test_a_build_failure_is_unknown_state_data(self):
        geom = geometry("geom1", features={"r1": rectangle_feature("r1")},
                        build_error=FakeEngineError("build stopped at r1"))
        worker, _, _ = world(geometries={"geom1": geom})
        result = call("geometry.build", worker, {"geometry": geom_path("geom1")})
        assert result["ok"] is False
        assert result["status"] == "EXECUTION_STATE_UNKNOWN"
        assert result["execution_state_unknown"] is True and result["partial_change"] is True
        assert result["failed"][0]["stage"] == "run"
        assert geom.run_log == [()]

    def test_a_workplane_geometry_is_built_through_the_parent_sequence(self):
        inner = geometry("wp_geom", sdim=2)
        wp = workplane_feature("wp1", inner=inner)
        geom = geometry("geom1", features={"wp1": wp})
        worker, _, _ = world(geometries={"geom1": geom})
        result = call("geometry.build", worker, {"geometry": workplane_path("wp1")})
        assert result["scope"] == "workplane_feature_and_preceding"
        assert geom.run_log == [("wp1",)]


class TestFinalize:
    def test_creates_the_union_finalize_and_sets_the_action(self):
        geom = geometry("geom1", features={"r1": rectangle_feature("r1")})
        geom.feature_list.factory = lambda tag, *args: finalize_feature(tag, str(args[0]))
        worker, _, _ = world(geometries={"geom1": geom})
        result = call("geometry.finalize", worker, {"geometry": geom_path("geom1"), "mode": "union"})
        assert result["created"] is True
        assert result["type_readback"] == "FormUnion"
        assert result["properties"]["action"]["data"] == "union"
        assert geom.feature_list.items["fin"].values["action"] == "union"

    def test_assembly_mode_uses_formassembly(self):
        geom = geometry("geom1")
        geom.feature_list.factory = lambda tag, *args: finalize_feature(tag, str(args[0]))
        worker, _, _ = world(geometries={"geom1": geom})
        result = call("geometry.finalize", worker, {
            "geometry": geom_path("geom1"), "mode": "assembly",
            "options": {"createpairs": "on", "pairtype": "contact", "imprint": True},
        })
        assert result["type_readback"] == "FormAssembly"
        assert result["properties"]["action"]["data"] == "assembly"
        assert result["properties"]["pairtype"]["data"] == "contact"
        assert result["properties"]["createpairs"]["data"] == "on"
        assert result["properties"]["imprint"]["data"] is True

    def test_an_existing_finalize_of_another_type_is_a_type_conflict(self):
        geom = geometry("geom1", features={"fin": finalize_feature("fin", "FormAssembly")})
        worker, _, _ = world(geometries={"geom1": geom})
        expect_error("TYPE_CONFLICT", call, "geometry.finalize", worker,
                     {"geometry": geom_path("geom1"), "mode": "union"})
        assert geom.feature_list.mutation_calls() == []

    def test_an_existing_finalize_is_updated_not_recreated(self):
        geom = geometry("geom1", features={"fin": finalize_feature("fin", "FormUnion")})
        worker, _, _ = world(geometries={"geom1": geom})
        result = call("geometry.finalize", worker, {
            "geometry": geom_path("geom1"), "mode": "union", "options": {"repairtol": 1e-5},
        })
        assert result["created"] is False
        assert result["properties"]["repairtol"]["data"] == 1e-5
        assert geom.feature_list.mutation_calls() == []

    def test_bad_mode_and_unknown_option_are_refused(self):
        worker, _, _ = world(geometries={"geom1": geometry("geom1")})
        expect_error("INVALID_REQUEST", call, "geometry.finalize", worker,
                     {"geometry": geom_path("geom1"), "mode": "shuffle"})
        expect_error("INVALID_REQUEST", call, "geometry.finalize", worker,
                     {"geometry": geom_path("geom1"), "mode": "union", "options": {"mystery": 1}})

    def test_a_workplane_sequence_has_no_finalize_path_here(self):
        inner = geometry("wp_geom", sdim=2)
        wp = workplane_feature("wp1", inner=inner)
        geom = geometry("geom1", features={"wp1": wp})
        worker, _, _ = world(geometries={"geom1": geom})
        expect_error("API_UNSUPPORTED", call, "geometry.finalize", worker,
                     {"geometry": workplane_path("wp1"), "mode": "union"})


class TestImport:
    def test_sets_the_engine_path_verbatim_with_spaces_and_chinese(self):
        geom = geometry("geom1")
        geom.feature_list.factory = lambda tag, *args: FNode(
            tag=tag, type_id=str(args[0]), value_types={"filename": "String", "type": "String"},
            values={"filename": "", "type": "dxf"}, readback_overrides={"type": "dxf"},
        )
        worker, _, _ = world(geometries={"geom1": geom})
        engine_path = "/tmp/几何 模型/part 1.dxf"
        result = call("geometry.import", worker, {
            "geometry": geom_path("geom1"), "tag": "imp1", "artifact_id": engine_path,
        })
        assert result["ok"] is True
        assert result["artifact_id"] == engine_path
        assert result["artifact_path_verbatim"] is True
        assert geom.feature_list.items["imp1"].values["filename"] == engine_path
        assert result["license"]["status"] == "UNVERIFIED"
        assert result["local_probe"] is None

    def test_local_path_check_reports_a_missing_file_before_the_write(self, tmp_path):
        geom = geometry("geom1")
        worker, _, _ = world(geometries={"geom1": geom})
        expect_error("ARTIFACT_NOT_FOUND", call, "geometry.import", worker, {
            "geometry": geom_path("geom1"), "tag": "imp1",
            "artifact_id": str(tmp_path / "missing.dxf"), "options": {"path_check": "local"},
        })
        assert geom.feature_list.mutation_calls() == []

    def test_local_path_check_reports_hash_size_and_unicode(self, tmp_path):
        payload = tmp_path / "几何 part.step"
        payload.write_bytes(b"solid")
        geom = geometry("geom1")
        geom.feature_list.factory = lambda tag, *args: FNode(
            tag=tag, type_id=str(args[0]),
            value_types={"filename": "String", "type": "String", "includevirtual": "Boolean"},
            values={"filename": "", "type": "native"},
        )
        worker, _, _ = world(geometries={"geom1": geom})
        result = call("geometry.import", worker, {
            "geometry": geom_path("geom1"), "tag": "imp1", "artifact_id": str(payload),
            "options": {"path_check": "local", "includevirtual": False},
        })
        assert result["local_probe"]["is_file"] is True
        assert result["local_probe"]["non_ascii"] is True
        assert result["local_probe"]["size_bytes"] == 5
        assert len(result["local_probe"]["sha256"]) == 64
        assert geom.feature_list.items["imp1"].values["includevirtual"] is False

    def test_relative_and_control_character_paths_are_refused(self):
        worker, _, _ = world(geometries={"geom1": geometry("geom1")})
        expect_error("INVALID_REQUEST", call, "geometry.import", worker, {
            "geometry": geom_path("geom1"), "tag": "imp1", "artifact_id": "relative/part.dxf",
        })
        expect_error("INVALID_REQUEST", call, "geometry.import", worker, {
            "geometry": geom_path("geom1"), "tag": "imp1", "artifact_id": "/tmp/bad\x00name.dxf",
        })

    def test_unknown_option_is_refused(self):
        worker, _, _ = world(geometries={"geom1": geometry("geom1")})
        expect_error("INVALID_REQUEST", call, "geometry.import", worker, {
            "geometry": geom_path("geom1"), "tag": "imp1", "artifact_id": "/tmp/a.dxf",
            "options": {"shell": True},
        })

    def test_build_option_runs_the_sequence(self):
        geom = geometry("geom1")
        geom.feature_list.factory = lambda tag, *args: FNode(
            tag=tag, type_id=str(args[0]), value_types={"filename": "String"}, values={"filename": ""},
        )
        worker, _, _ = world(geometries={"geom1": geom})
        result = call("geometry.import", worker, {
            "geometry": geom_path("geom1"), "tag": "imp1", "artifact_id": "/tmp/a.dxf",
            "options": {"build": True},
        })
        assert result["build"]["built"] is True
        assert geom.run_log == [()]


# ---------------------------------------------------------------------------
# measure and validate
# ---------------------------------------------------------------------------


class TestMeasure:
    def test_objects_mode_reads_the_sequence_measure_tool(self):
        geom = geometry("geom1", features={"r1": rectangle_feature("r1")}, objects=["r1"])
        worker, _, _ = world(geometries={"geom1": geom})
        result = call("geometry.measure", worker, {
            "geometry": geom_path("geom1"), "query": {"mode": "objects", "all": True,
                                                      "metrics": ["volume", "bounding_box", "n_entities"]},
        })
        assert result["metrics"]["volume"]["value"] == 1.0e-6
        assert result["metrics"]["bounding_box"]["value"] == [0.0, 1.0, 0.0, 1.0, 0.0, 1.0]
        assert result["length_unit"] == "m"

    def test_objects_mode_can_select_specific_objects(self):
        geom = geometry("geom1", features={"r1": rectangle_feature("r1")}, objects=["r1"])
        worker, _, _ = world(geometries={"geom1": geom})
        result = call("geometry.measure", worker, {
            "geometry": geom_path("geom1"), "query": {"objects": ["r1"], "metrics": ["area"]},
        })
        assert result["objects"] == ["r1"]
        assert result["metrics"]["area"]["value"] == 0.25

    def test_entities_mode_uses_the_finalized_component_measure_tool(self):
        geom = geometry("geom1", features={"r1": rectangle_feature("r1")})
        worker, _, _ = world(geometries={"geom1": geom})
        result = call("geometry.measure", worker, {
            "geometry": geom_path("geom1"),
            "query": {"mode": "entities", "entity_dimension": 2, "all": True, "metrics": ["area"]},
        })
        assert result["entities"] == [1, 2, 3]
        assert result["entity_count"] == 3
        assert result["source"] == "component.measure().selection()"

    def test_entities_mode_with_an_explicit_entity_list(self):
        geom = geometry("geom1", features={"r1": rectangle_feature("r1")})
        worker, _, _ = world(geometries={"geom1": geom})
        result = call("geometry.measure", worker, {
            "geometry": geom_path("geom1"),
            "query": {"mode": "entities", "entity_dimension": 2, "entities": [2], "metrics": ["area"]},
        })
        assert result["entities"] == [2]

    def test_unknown_metric_is_refused_before_any_engine_call(self):
        geom = geometry("geom1")
        worker, _, _ = world(geometries={"geom1": geom})
        before = list(geom.calls)
        expect_error("API_UNSUPPORTED", call, "geometry.measure", worker, {
            "geometry": geom_path("geom1"), "query": {"all": True, "metrics": ["centroid"]},
        })
        assert not [row for row in geom.calls[len(before):] if row[0] == "measure"]

    def test_query_shape_is_enforced(self):
        worker, _, _ = world(geometries={"geom1": geometry("geom1")})
        expect_error("INVALID_REQUEST", call, "geometry.measure", worker,
                     {"geometry": geom_path("geom1"), "query": {"metrics": ["area"]}})
        expect_error("INVALID_REQUEST", call, "geometry.measure", worker,
                     {"geometry": geom_path("geom1"), "query": {"mode": "entities", "entity_dimension": 2}})
        expect_error("INVALID_REQUEST", call, "geometry.measure", worker,
                     {"geometry": geom_path("geom1"), "query": {"mode": "nope", "all": True}})


class TestValidate:
    def test_all_supported_expectations_pass(self):
        geom = geometry("geom1", features={"r1": rectangle_feature("r1"), "wp1": workplane_feature("wp1")})
        worker, _, _ = world(geometries={"geom1": geom})
        result = call("geometry.validate", worker, {
            "geometry": geom_path("geom1"),
            "expectations": {
                "dimension": 3,
                "length_unit": "m",
                "entity_counts": {"0": 8, "3": 1},
                "bounding_box": [0.0, 1.0, 0.0, 1.0, 0.0, 1.0],
                "feature_tags": {"present": ["r1"], "absent": ["nope"]},
                "volume": {"value": 1.0e-6, "tolerance": 1e-9},
                "area": {"value": 0.25, "tolerance": 1e-9},
            },
        })
        assert result["ok"] is True
        assert result["status"] == "APPLIED"
        assert result["failed_checks"] == []
        assert [row["name"] for row in result["checks"]] == [
            "dimension", "length_unit", "entity_counts[0]", "entity_counts[3]",
            "bounding_box", "feature_tags.present", "feature_tags.absent", "volume", "area",
        ]
        assert result["check_count"] == 9

    def test_failed_checks_are_reported_without_an_exception(self):
        geom = geometry("geom1", features={"r1": rectangle_feature("r1")})
        worker, _, _ = world(geometries={"geom1": geom})
        result = call("geometry.validate", worker, {
            "geometry": geom_path("geom1"),
            "expectations": {"entity_counts": {"3": 5}, "feature_tags": {"absent": ["r1"]},
                             "bounding_box": [0.0, 1.0, 0.0, 1.0, 0.0, 2.0]},
        })
        assert result["ok"] is False and result["status"] == "FAILED"
        assert set(result["failed_checks"]) == {"entity_counts[3]", "feature_tags.absent", "bounding_box"}
        box = [row for row in result["checks"] if row["name"] == "bounding_box"][0]
        assert box["detail"]["max_abs_delta"] == 1.0

    def test_design_spacing_is_refused_before_any_engine_call(self):
        geom = geometry("geom1")
        worker, _, _ = world(geometries={"geom1": geom})
        expect_error("API_UNSUPPORTED", call, "geometry.validate", worker, {
            "geometry": geom_path("geom1"), "expectations": {"design_spacing": 0.001},
        })
        assert not [row for row in geom.calls if row[0] == "measure"]

    def test_unknown_or_malformed_expectations_are_refused(self):
        worker, _, _ = world(geometries={"geom1": geometry("geom1")})
        expect_error("INVALID_REQUEST", call, "geometry.validate", worker,
                     {"geometry": geom_path("geom1"), "expectations": {"colour": "red"}})
        expect_error("INVALID_REQUEST", call, "geometry.validate", worker,
                     {"geometry": geom_path("geom1"), "expectations": {"entity_counts": {"domains": 1}}})

    def test_validate_without_expectations_still_reports_the_state(self):
        worker, _, _ = world(geometries={"geom1": geometry("geom1")})
        result = call("geometry.validate", worker, {"geometry": geom_path("geom1")})
        assert result["ok"] is True and result["check_count"] == 0
        assert result["geometry_state"]["unavailable"]["problems"]["method"] == "problems"


# ---------------------------------------------------------------------------
# definition.component_manage
# ---------------------------------------------------------------------------


class TestComponentManage:
    def test_list(self):
        worker, model, _ = world()
        model.collections["component"].items["comp2"] = component("comp2")
        result = call("definition.component_manage", worker, {"action": "list"})
        assert result["action"] == "list"
        assert [row["tag"] for row in result["components"]] == ["comp1", "comp2"]
        assert result["components"][0]["type_id"] == "Component"

    def test_create_with_a_documented_type(self):
        worker, model, _ = world()
        result = call("definition.component_manage", worker,
                      {"action": "create", "tag": "comp2", "definition": {"type": "MeshComponent"}})
        assert result["created"] is True and result["type_readback"] == "MeshComponent"
        assert model.collections["component"].items["comp2"].type_id == "MeshComponent"

    def test_create_without_a_type(self):
        worker, model, _ = world()
        result = call("definition.component_manage", worker, {"action": "create", "tag": "comp3"})
        assert result["created"] is True and result["type_id"] is None
        assert "comp3" in model.collections["component"].items

    def test_an_undocumented_component_type_is_refused(self):
        worker, model, _ = world()
        before = list(model.collections["component"].mutation_calls())
        expect_error("API_UNSUPPORTED", call, "definition.component_manage", worker,
                     {"action": "create", "tag": "comp2", "definition": {"type": "Part"}})
        assert model.collections["component"].mutation_calls() == before

    def test_tag_conflict_is_refused_before_the_create(self):
        worker, model, _ = world()
        before = list(model.collections["component"].mutation_calls())
        expect_error("TAG_CONFLICT", call, "definition.component_manage", worker,
                     {"action": "create", "tag": "comp1"})
        assert model.collections["component"].mutation_calls() == before

    def test_inspect_lists_the_component_collections(self):
        worker, _, _ = world()
        result = call("definition.component_manage", worker, {"action": "inspect", "tag": "comp1"})
        assert result["type_id"] == "Component"
        assert result["geometries"] == []
        assert result["pairs"] == []
        assert result["couplings"] == []
        assert result["unavailable"]["defineAllFrames"]["allowlist_entry_required"] == "defineAllFrames"

    def test_remove_and_missing_tag(self):
        worker, model, _ = world()
        model.collections["component"].items["comp2"] = component("comp2")
        result = call("definition.component_manage", worker, {"action": "remove", "tag": "comp2"})
        assert result["removed"] is True
        assert "comp2" not in model.collections["component"].items
        expect_error("NODE_NOT_FOUND", call, "definition.component_manage", worker,
                     {"action": "remove", "tag": "comp2"})

    def test_copy_is_refused_with_the_allow_list_entry(self):
        worker, model, _ = world()
        before = list(model.collections["component"].mutation_calls())
        error = expect_error("API_UNSUPPORTED", call, "definition.component_manage", worker,
                             {"action": "copy", "tag": "comp2"})
        assert "copy" in str(error)
        assert model.collections["component"].mutation_calls() == before

    def test_unknown_action_and_extra_fields_are_refused(self):
        worker, _, _ = world()
        expect_error("INVALID_REQUEST", call, "definition.component_manage", worker, {"action": "clone"})
        expect_error("INVALID_REQUEST", call, "definition.component_manage", worker,
                     {"action": "list", "extra": 1})
        expect_error("INVALID_REQUEST", call, "definition.component_manage", worker, {"action": "list", "tag": "c"})


# ---------------------------------------------------------------------------
# definition.coordinate_manage
# ---------------------------------------------------------------------------


def coord_path(tag: str = "sys1") -> dict[str, Any]:
    return {"segments": [{"collection": "component", "tag": "comp1"}, {"collection": "coordSystem", "tag": tag}]}


class TestCoordinateManage:
    def test_list(self):
        worker, _, comp = world()
        comp.collections["coordSystem"].items["sys1"] = FNode(tag="sys1", type_id="Cylindrical")
        result = call("definition.coordinate_manage", worker, {"action": "list", "component": "comp1"})
        assert [row["tag"] for row in result["coordinate_systems"]] == ["sys1"]
        assert result["unavailable"]["coord"]["allowlist_entry_required"] == "coord"

    def test_create_uses_the_documented_three_argument_call(self):
        worker, _, comp = world(geometries={"geom1": geometry("geom1")})
        result = call("definition.coordinate_manage", worker, {
            "action": "create", "component": "comp1", "tag": "sys1",
            "definition": {"type": "Cylindrical", "geometry": "geom1"},
        })
        assert result["created"] is True
        assert result["type_readback"] == "Cylindrical"
        assert ("create", ("sys1", "geom1", "Cylindrical")) in comp.collections["coordSystem"].calls

    def test_create_with_properties(self):
        worker, _, comp = world(geometries={"geom1": geometry("geom1")})
        comp.collections["coordSystem"].factory = lambda tag, *args: FNode(
            tag=tag, type_id=str(args[-1]), value_types={"x": "DoubleArray"}, values={"x": [0.0, 0.0, 0.0]},
        )
        result = call("definition.coordinate_manage", worker, {
            "action": "create", "component": "comp1", "tag": "sys1",
            "definition": {"type": "Cylindrical", "geometry": "geom1", "properties": {"x": [1.0, 2.0, 3.0]}},
        })
        assert result["ok"] is True
        assert result["properties"]["x"]["data"] == [1.0, 2.0, 3.0]

    def test_an_undocumented_type_is_refused_before_the_write(self):
        worker, _, comp = world(geometries={"geom1": geometry("geom1")})
        before = list(comp.collections["coordSystem"].mutation_calls())
        expect_error("API_UNSUPPORTED", call, "definition.coordinate_manage", worker, {
            "action": "create", "component": "comp1", "tag": "sys1",
            "definition": {"type": "Cartesian", "geometry": "geom1"},
        })
        assert comp.collections["coordSystem"].mutation_calls() == before

    def test_geometry_is_required_and_must_exist(self):
        worker, _, _ = world(geometries={"geom1": geometry("geom1")})
        expect_error("INVALID_REQUEST", call, "definition.coordinate_manage", worker,
                     {"action": "create", "component": "comp1", "tag": "sys1",
                      "definition": {"type": "Cylindrical"}})
        expect_error("NODE_NOT_FOUND", call, "definition.coordinate_manage", worker,
                     {"action": "create", "component": "comp1", "tag": "sys1",
                      "definition": {"type": "Cylindrical", "geometry": "geom9"}})

    def test_tag_conflict(self):
        worker, _, comp = world(geometries={"geom1": geometry("geom1")})
        comp.collections["coordSystem"].items["sys1"] = FNode(tag="sys1", type_id="Cylindrical")
        expect_error("TAG_CONFLICT", call, "definition.coordinate_manage", worker, {
            "action": "create", "component": "comp1", "tag": "sys1",
            "definition": {"type": "Cylindrical", "geometry": "geom1"},
        })

    def test_inspect_reports_the_engine_property_enumeration(self):
        worker, _, comp = world()
        comp.collections["coordSystem"].items["sys1"] = FNode(
            tag="sys1", type_id="Cylindrical", value_types={"x": "DoubleArray", "frame": "String"},
            values={"x": [0.0, 0.0, 0.0], "frame": "spatial"},
        )
        result = call("definition.coordinate_manage", worker, {"action": "inspect", "path": coord_path("sys1")})
        assert result["type_id"] == "Cylindrical"
        assert result["property_names"] == ["x", "frame"]
        assert result["properties"]["frame"]["value"] == "spatial"
        assert result["unavailable"]["isLinear"]["allowlist_entry_required"] == "isLinear"

    def test_update_writes_and_reads_back(self):
        worker, _, comp = world()
        comp.collections["coordSystem"].items["sys1"] = FNode(
            tag="sys1", type_id="Cylindrical", value_types={"x": "DoubleArray"}, values={"x": [0.0, 0.0, 0.0]},
        )
        result = call("definition.coordinate_manage", worker, {
            "action": "update", "path": coord_path("sys1"), "definition": {"properties": {"x": [1.0, 1.0, 0.0]}},
        })
        assert result["ok"] is True
        assert result["properties"]["x"]["data"] == [1.0, 1.0, 0.0]

    def test_update_rejects_an_unknown_property_before_the_write(self):
        worker, _, comp = world()
        node = FNode(tag="sys1", type_id="Cylindrical", value_types={"x": "DoubleArray"},
                     values={"x": [0.0, 0.0, 0.0]})
        comp.collections["coordSystem"].items["sys1"] = node
        before = len(node.calls)
        expect_error("INVALID_REQUEST", call, "definition.coordinate_manage", worker, {
            "action": "update", "path": coord_path("sys1"), "definition": {"properties": {"w": [1.0]}},
        })
        assert not [row for row in node.calls[before:] if row[0] == "set"]
        expect_error("INVALID_REQUEST", call, "definition.coordinate_manage", worker,
                     {"action": "update", "path": coord_path("sys1"), "definition": {}})

    def test_remove_and_bad_path(self):
        worker, _, comp = world()
        comp.collections["coordSystem"].items["sys1"] = FNode(tag="sys1", type_id="Cylindrical")
        result = call("definition.coordinate_manage", worker, {"action": "remove", "path": coord_path("sys1")})
        assert result["removed"] is True
        expect_error("NODE_NOT_FOUND", call, "definition.coordinate_manage", worker,
                     {"action": "inspect", "path": coord_path("sys1")})
        expect_error("INVALID_NODE_PATH", call, "definition.coordinate_manage", worker,
                     {"action": "inspect", "path": geom_path("geom1")})

    def test_unknown_action_is_refused(self):
        worker, _, _ = world()
        expect_error("INVALID_REQUEST", call, "definition.coordinate_manage", worker, {"action": "rotate"})


# ---------------------------------------------------------------------------
# definition.pair_manage
# ---------------------------------------------------------------------------


def pair_path(tag: str = "pair1") -> dict[str, Any]:
    return {"segments": [{"collection": "component", "tag": "comp1"}, {"collection": "pair", "tag": tag}]}


class TestPairManage:
    def test_list(self):
        worker, _, comp = world()
        comp.collections["pair"].items["pair1"] = FNode(tag="pair1", type_id="Identity")
        result = call("definition.pair_manage", worker, {"action": "list", "component": "comp1"})
        assert [row["tag"] for row in result["pairs"]] == ["pair1"]
        assert result["pairs"][0]["unavailable"]["source"]["allowlist_entry_required"] == "source"
        assert result["pair_count"] == 1

    def test_list_accepts_the_component_path_form(self):
        worker, _, comp = world()
        comp.collections["pair"].items["pair1"] = FNode(tag="pair1", type_id="Identity")
        path = {"segments": [{"collection": "component", "tag": "comp1"},
                             {"collection": "component", "tag": "comp1"}]}
        result = call("definition.pair_manage", worker, {"action": "list", "path": path})
        assert result["component"] == "comp1" and result["pair_count"] == 1
        expect_error("INVALID_REQUEST", call, "definition.pair_manage", worker, {"action": "list"})

    def test_create_identity_pair(self):
        worker, _, comp = world()
        result = call("definition.pair_manage", worker, {
            "action": "create", "component": "comp1", "tag": "pair1", "definition": {"type": "Identity"},
        })
        assert result["created"] is True and result["type_readback"] == "Identity"
        assert ("create", ("pair1", "Identity")) in comp.collections["pair"].calls

    def test_create_pair_on_a_geometry_uses_the_three_argument_form(self):
        worker, _, comp = world()
        result = call("definition.pair_manage", worker, {
            "action": "create", "component": "comp1", "tag": "pair2",
            "definition": {"type": "GeneralContact", "geometry": "geom1"},
        })
        assert result["geometry"] == "geom1"
        assert ("create", ("pair2", "GeneralContact", "geom1")) in comp.collections["pair"].calls

    def test_an_undocumented_pair_type_is_refused(self):
        worker, _, comp = world()
        before = list(comp.collections["pair"].mutation_calls())
        expect_error("API_UNSUPPORTED", call, "definition.pair_manage", worker, {
            "action": "create", "component": "comp1", "tag": "pair1", "definition": {"type": "IdentityPair"},
        })
        assert comp.collections["pair"].mutation_calls() == before

    def test_source_binding_is_refused_before_the_create(self):
        worker, _, comp = world()
        before = list(comp.collections["pair"].mutation_calls())
        error = expect_error("API_UNSUPPORTED", call, "definition.pair_manage", worker, {
            "action": "create", "component": "comp1", "tag": "pair1",
            "definition": {"type": "Identity", "source": {"tag": "sel1"}},
        })
        assert "source" in str(error)
        assert comp.collections["pair"].mutation_calls() == before

    def test_inspect_and_remove(self):
        worker, _, comp = world()
        comp.collections["pair"].items["pair1"] = FNode(tag="pair1", type_id="Identity")
        inspected = call("definition.pair_manage", worker, {"action": "inspect", "path": pair_path("pair1")})
        assert inspected["type_id"] == "Identity"
        assert inspected["unavailable"]["destination"]["method"] == "destination"
        removed = call("definition.pair_manage", worker, {"action": "remove", "path": pair_path("pair1")})
        assert removed["removed"] is True

    def test_tag_conflict_and_missing_tag(self):
        worker, _, comp = world()
        comp.collections["pair"].items["pair1"] = FNode(tag="pair1", type_id="Identity")
        expect_error("TAG_CONFLICT", call, "definition.pair_manage", worker, {
            "action": "create", "component": "comp1", "tag": "pair1", "definition": {"type": "Identity"},
        })
        expect_error("NODE_NOT_FOUND", call, "definition.pair_manage", worker,
                     {"action": "remove", "path": pair_path("pair9")})

    def test_type_conflict_on_create(self):
        worker, _, comp = world()
        comp.collections["pair"].items["pair1"] = FNode(tag="pair1", type_id="Contact")
        expect_error("TYPE_CONFLICT", call, "definition.pair_manage", worker, {
            "action": "create", "component": "comp1", "tag": "pair1", "definition": {"type": "Identity"},
        })

    def test_unknown_action(self):
        worker, _, _ = world()
        expect_error("INVALID_REQUEST", call, "definition.pair_manage", worker, {"action": "swap"})


# ---------------------------------------------------------------------------
# definition.coupling_manage
# ---------------------------------------------------------------------------


def cpl_path(tag: str = "int1") -> dict[str, Any]:
    return {"segments": [{"collection": "component", "tag": "comp1"}, {"collection": "cpl", "tag": tag}]}


class TestCouplingManage:
    def test_list(self):
        worker, _, comp = world()
        comp.collections["cpl"].items["int1"] = FNode(tag="int1", type_id="Integration")
        result = call("definition.coupling_manage", worker, {"action": "list", "component": "comp1"})
        assert [row["tag"] for row in result["couplings"]] == ["int1"]
        assert result["couplings"][0]["unavailable"]["type"]["allowlist_entry_required"] == "type"
        assert result["coupling_count"] == 1

    def test_list_accepts_the_component_path_form(self):
        worker, _, comp = world()
        comp.collections["cpl"].items["int1"] = FNode(tag="int1", type_id="Integration")
        path = {"segments": [{"collection": "component", "tag": "comp1"},
                             {"collection": "component", "tag": "comp1"}]}
        result = call("definition.coupling_manage", worker, {"action": "list", "path": path})
        assert result["component"] == "comp1" and result["coupling_count"] == 1
        expect_error("INVALID_REQUEST", call, "definition.coupling_manage", worker, {"action": "list"})

    def test_create_integration_coupling_with_a_named_selection(self):
        worker, _, comp = world()
        result = call("definition.coupling_manage", worker, {
            "action": "create", "component": "comp1", "tag": "int1",
            "definition": {"type": "Integration", "selection": {"kind": "named", "tag": "sel1"}},
        })
        assert result["created"] is True and result["type_readback"] == "Integration"
        assert result["selection"]["readback"] == "sel1"
        assert ("create", ("int1", "Integration")) in comp.collections["cpl"].calls

    def test_create_with_an_entity_selection_and_properties(self):
        worker, _, comp = world()
        comp.collections["cpl"].factory = lambda tag, *args: FNode(
            tag=tag, type_id=str(args[-1]), value_types={"opname": "String"}, values={"opname": ""},
        )
        result = call("definition.coupling_manage", worker, {
            "action": "create", "component": "comp1", "tag": "int1",
            "definition": {
                "type": "Integration",
                "selection": {"kind": "entities", "property": 2, "entities": [1, 2]},
                "properties": {"opname": "myint"},
            },
        })
        assert result["ok"] is True
        assert result["selection"]["readback"] == [1, 2]
        assert result["properties"]["opname"]["data"] == "myint"

    def test_an_undocumented_coupling_type_is_refused(self):
        worker, _, comp = world()
        before = list(comp.collections["cpl"].mutation_calls())
        expect_error("API_UNSUPPORTED", call, "definition.coupling_manage", worker, {
            "action": "create", "component": "comp1", "tag": "int1", "definition": {"type": "Adjoint"},
        })
        assert comp.collections["cpl"].mutation_calls() == before

    def test_geometry_argument_uses_the_documented_three_argument_form(self):
        worker, _, comp = world()
        result = call("definition.coupling_manage", worker, {
            "action": "create", "component": "comp1", "tag": "gen1",
            "definition": {"type": "GeneralExtrusion", "geometry": "geom1"},
        })
        assert result["geometry"] == "geom1"
        assert ("create", ("gen1", "GeneralExtrusion", "geom1")) in comp.collections["cpl"].calls

    def test_selection_readback_mismatch_is_reported_as_data(self):
        worker, _, comp = world()

        class DeafSelection(FSelection):
            """set() is silently ignored: the engine readback cannot confirm it."""

            def set(self, *args: Any) -> None:
                self.calls.append(("set", args))

        def factory(tag: str, *args: Any) -> FNode:
            return FNode(tag=tag, type_id=str(args[-1]), selection_override=DeafSelection(None))

        comp.collections["cpl"].factory = factory
        result = call("definition.coupling_manage", worker, {
            "action": "create", "component": "comp1", "tag": "int1",
            "definition": {"type": "Integration",
                           "selection": {"kind": "entities", "property": 2, "entities": [3]}},
        })
        assert result["status"] == "EXECUTION_STATE_UNKNOWN"
        assert result["execution_state_unknown"] is True and result["partial_change"] is True
        assert result["failed"][0]["stage"] == "selection"
        assert "int1" in comp.collections["cpl"].items

    def test_inspect_update_remove(self):
        worker, _, comp = world()
        comp.collections["cpl"].items["int1"] = FNode(
            tag="int1", type_id="Integration", value_types={"opname": "String"}, values={"opname": "myint"},
        )
        inspected = call("definition.coupling_manage", worker, {"action": "inspect", "path": cpl_path("int1")})
        assert inspected["type_id"] == "Integration"
        assert inspected["property_names"] == ["opname"]
        updated = call("definition.coupling_manage", worker, {
            "action": "update", "path": cpl_path("int1"), "definition": {"properties": {"opname": "other"}},
        })
        assert updated["ok"] is True and updated["properties"]["opname"]["data"] == "other"
        removed = call("definition.coupling_manage", worker, {"action": "remove", "path": cpl_path("int1")})
        assert removed["removed"] is True

    def test_update_requires_properties_and_rejects_unknown_names(self):
        worker, _, comp = world()
        comp.collections["cpl"].items["int1"] = FNode(
            tag="int1", type_id="Integration", value_types={"opname": "String"}, values={"opname": "myint"},
        )
        expect_error("INVALID_REQUEST", call, "definition.coupling_manage", worker,
                     {"action": "update", "path": cpl_path("int1"), "definition": {}})
        expect_error("INVALID_REQUEST", call, "definition.coupling_manage", worker, {
            "action": "update", "path": cpl_path("int1"), "definition": {"properties": {"nope": "x"}},
        })

    def test_tag_conflict_and_type_conflict(self):
        worker, _, comp = world()
        comp.collections["cpl"].items["int1"] = FNode(tag="int1", type_id="Average")
        expect_error("TAG_CONFLICT", call, "definition.coupling_manage", worker, {
            "action": "create", "component": "comp1", "tag": "int1", "definition": {"type": "Average"},
        })
        expect_error("TYPE_CONFLICT", call, "definition.coupling_manage", worker, {
            "action": "create", "component": "comp1", "tag": "int1", "definition": {"type": "Integration"},
        })

    def test_an_unknown_selection_kind_is_refused_before_the_write(self):
        worker, _, comp = world()
        before = list(comp.collections["cpl"].mutation_calls())
        expect_error("INVALID_REQUEST", call, "definition.coupling_manage", worker, {
            "action": "create", "component": "comp1", "tag": "int1",
            "definition": {"type": "Integration", "selection": {"kind": "quantum"}},
        })
        expect_error("INVALID_REQUEST", call, "definition.coupling_manage", worker, {
            "action": "create", "component": "comp1", "tag": "int1",
            "definition": {"type": "Integration", "selection": {"kind": "named"}},
        })
        expect_error("INVALID_REQUEST", call, "definition.coupling_manage", worker, {
            "action": "create", "component": "comp1", "tag": "int1",
            "definition": {"type": "Integration",
                           "selection": {"kind": "entities", "property": 2, "entities": ["r1"]}},
        })
        assert comp.collections["cpl"].mutation_calls() == before


def test_properties_accept_the_catalogue_row_array() -> None:
    """``properties`` is a PropertySet: the catalogue's row array must be accepted.

    The production driver sends ``[{"name": ..., "value": <TypedValue>}, ...]`` (the shape
    ``common.schema.json#/$defs/PropertySet`` declares, required by the catalogue for both
    geometry.feature_create and geometry.feature_update); the module's internal spelling is a
    mapping.  Both must reach the same writer and read back the same values.
    """
    geom = geometry("geom1", features={"r1": rectangle_feature("r1")})
    worker, _, _ = world(geometries={"geom1": geom})
    updated = call("geometry.feature_update", worker, {
        "path": feature_path("r1"),
        "properties": [{"name": "size", "value": {"kind": "float64", "shape": [2], "data": [5.0, 2.0]}}]})
    assert updated["status"] == "APPLIED"
    assert updated["properties"]["size"]["data"] == [5.0, 2.0]

    created = call("geometry.feature_create", worker, {
        "parent": geom_path("geom1"), "tag": "blk1", "type_id": "Block",
        "properties": [
            {"name": "size", "value": {"kind": "float64", "shape": [3], "data": [0.01, 0.02, 0.03]}},
            {"name": "pos", "value": {"kind": "float64", "shape": [3], "data": [0.0, 0.0, 0.0]}}]})
    assert created["status"] == "APPLIED"
    assert created["properties"]["size"]["data"] == [0.01, 0.02, 0.03]

def test_nested_definition_properties_accept_the_catalogue_row_array() -> None:
    """``definition.properties`` (coordinate systems, couplings) is a PropertySet as well."""
    worker, _, comp = world(geometries={"geom1": geometry("geom1")})
    comp.collections["coordSystem"].factory = lambda tag, *args: FNode(
        tag=tag, type_id=str(args[-1]), value_types={"x": "DoubleArray"}, values={"x": [0.0, 0.0, 0.0]},
    )
    result = call("definition.coordinate_manage", worker, {
        "action": "create", "component": "comp1", "tag": "sys1",
        "definition": {"type": "Cylindrical", "geometry": "geom1",
                       "properties": [{"name": "x", "value": {"kind": "float64", "shape": [3],
                                                              "data": [1.0, 2.0, 3.0]}}]}})
    assert result["ok"] is True
    assert result["properties"]["x"]["data"] == [1.0, 2.0, 3.0]


def test_a_nested_row_may_not_duplicate_a_flat_property() -> None:
    worker, _, _ = world(geometries={"geom1": geometry("geom1")})
    expect_error("INVALID_REQUEST", call, "definition.coordinate_manage", worker, {
        "action": "create", "component": "comp1", "tag": "sys2",
        "definition": {"type": "Cylindrical", "geometry": "geom1", "x": [1.0, 2.0, 3.0],
                       "properties": [{"name": "x", "value": {"kind": "float64", "shape": [3],
                                                              "data": [4.0, 5.0, 6.0]}}]}})


def test_workplane_edit_actions_accept_the_catalogue_row_array() -> None:
    inner = geometry("wp1_inner", features={"r1": rectangle_feature("r1")})
    parent = geometry("geom1", features={"wp1": workplane_feature("wp1", inner=inner)})
    worker, _, _ = world(geometries={"geom1": parent})
    result = call("geometry.workplane_edit", worker, {
        "workplane": workplane_path("wp1"),
        "actions": [{"action": "update", "tag": "r1",
                     "properties": [{"name": "size",
                                     "value": {"kind": "float64", "shape": [2], "data": [0.001, 0.002]}}]}]})
    assert result["status"] == "APPLIED"
    assert result["applied"][0]["properties"]["size"]["data"] == [0.001, 0.002]
    assert result["applied"][0]["applied"][0]["readback_match"] is True
    assert inner.feature_list.items["r1"].values["size"] == [0.001, 0.002]


def test_a_duplicated_row_is_refused_before_the_engine_is_touched() -> None:
    geom = geometry("geom1", features={"r1": rectangle_feature("r1")})
    worker, _, _ = world(geometries={"geom1": geom})
    before = list(geom.feature_list.items["r1"].values["size"])
    expect_error("INVALID_REQUEST", call, "geometry.feature_update", worker, {
        "path": feature_path("r1"),
        "properties": [{"name": "size", "value": {"kind": "float64", "shape": [2], "data": [5.0, 2.0]}},
                       {"name": "size", "value": {"kind": "float64", "shape": [2], "data": [1.0, 1.0]}}]})
    assert list(geom.feature_list.items["r1"].values["size"]) == before, "a duplicated row must write nothing"
