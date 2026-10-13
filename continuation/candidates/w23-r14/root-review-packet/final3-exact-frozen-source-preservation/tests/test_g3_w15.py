"""W15: material / physics / multiphysics domain operations.

Every test drives the real operation code against a fake COMSOL node tree.  The
fake mirrors the *verified* API surface the implementation uses: a
``MaterialList``/``ComponentMaterialList`` (``tags/get/create/remove``), a
``Material`` node (``materialType/propertyGroup/selection/label/remove``), a
``MaterialModelList`` property-group list plus a ``MaterialModel``
(``ParameterEntity``: ``properties/hasProperty/getValueType/getString/getDouble/
getStringArray/set/input/addInput/removeInput``), a ``PhysicsList`` /
``ComponentPhysicsList`` (``create(tag, type, geom[, sdim])``,
``Physics.getType/geom/feature``), a ``PhysicsFeatureList``
(``create(tag, type[, dim])``) and a ``ComponentMultiphysicsCouplingList``
(``create(tag, type, geom[, sdim])``).  A method the fake does not implement
surfaces as ``AttributeError`` and a method listed in ``unavailable`` raises the
worker allow-list refusal (``METHOD_REJECTED``), so both "the API does not
exist" and "the worker refuses the method" paths are exercised.
"""
from __future__ import annotations

from typing import Any, Mapping

import pytest

from comsol_mcp._execution_contract import ExecutionContractError

from comsol_mcp import _g3_w15 as w15
from comsol_mcp._g3_ops import DISPATCH, EFFECTS, IMPLEMENTED_OPERATIONS, OPERATION_ORIGINS

#: The default user-defined property group a created material always carries
#: (Programming Reference p.148 / Application Programming Guide).
DEFAULT_GROUP = "def"

#: Property metadata a *created* feature carries, mirroring the real COMSOL
#: feature metadata (a Heat Source has Q0; an Initial Values feature has Tinit).
FEATURE_VALUE_TYPES: dict[str, dict[str, str]] = {
    "HeatSource": {"Q0": "String"},
    "TemperatureBoundary": {"T0": "String"},
    "HeatFluxBoundary": {"q0": "String"},
    "ConvectiveHeatFlux": {"h": "String", "Text": "String"},
    "InitialValues": {"Tinit": "String"},
}

# ---------------------------------------------------------------------------
# fake engine
# ---------------------------------------------------------------------------


class FakeEngineError(RuntimeError):
    """Structured worker failure; ``code`` feeds ``_worker_failure_code``."""

    def __init__(self, message: str, *, code: str = "ENGINE_CALL_FAILED") -> None:
        super().__init__(message)
        self.reply = {"ok": False, "code": code, "message": message}
        self.failure = {"code": code, "message": message}


class FList:
    """Stand-in for a COMSOL ``ModelEntityList``."""

    def __init__(self, *, arity: int = 1, unavailable: tuple[str, ...] = (),
                 node_type: str = "Fake") -> None:
        self.arity = arity
        self.items: dict[str, Any] = {}
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.unavailable = set(unavailable)
        self.node_type = node_type

    def tags(self) -> list[str]:
        self.calls.append(("tags", ()))
        return list(self.items)

    def get(self, tag: str) -> Any:
        self.calls.append(("get", (tag,)))
        return self.items[tag]

    def hasTag(self, tag: str) -> bool:
        return tag in self.items

    def size(self) -> int:
        return len(self.items)

    def index(self, tag: str) -> int:
        return list(self.items).index(tag)

    def create(self, tag: str, *type_id: Any) -> Any:
        self.calls.append(("create", (tag,) + type_id))
        if "create" in self.unavailable:
            raise FakeEngineError("SecurityException: METHOD_REJECTED", code="METHOD_REJECTED")
        if tag in self.items:
            raise FakeEngineError(f"tag {tag} already exists")
        return None

    def remove(self, tag: str) -> None:
        self.calls.append(("remove", (tag,)))
        if "remove" in self.unavailable:
            raise FakeEngineError("SecurityException: METHOD_REJECTED", code="METHOD_REJECTED")
        if tag not in self.items:
            raise FakeEngineError(f"tag {tag} does not exist")
        del self.items[tag]

    def __getattr__(self, name: str) -> Any:
        raise AttributeError(name)


class FFeatureList(FList):
    """``PhysicsFeatureList``/``MultiphysicsCouplingList``: ``create`` builds the node.

    ``items`` is aliased to the owner node's ``children`` dict, so a feature
    created through the list is immediately visible to ``feature(tag)`` -- the
    same containment the real API has.
    """

    def __init__(self, *, owner_children: dict[str, Any] | None = None,
                 unavailable: tuple[str, ...] = (), factory: Any = None,
                 node_type: str = "PhysicsFeature") -> None:
        super().__init__(arity=3, unavailable=unavailable, node_type=node_type)
        self.factory = factory or self._default_factory
        if owner_children is not None:
            self.items = owner_children

    @staticmethod
    def _default_factory(tag: str, type_id: str, dim: int | None) -> "FFeature":
        return FFeature(tag=tag, type_id=type_id, dim=dim,
                        value_types=dict(FEATURE_VALUE_TYPES.get(type_id, {})),
                        values={"Q0": "0[W/m^3]"} if type_id == "HeatSource" else None)

    def create(self, tag: str, type_id: str, *dim: Any) -> Any:
        self.calls.append(("create", (tag, type_id) + dim))
        if "create" in self.unavailable:
            raise FakeEngineError("SecurityException: METHOD_REJECTED", code="METHOD_REJECTED")
        if tag in self.items:
            raise FakeEngineError(f"tag {tag} already exists")
        node = self.factory(str(tag), str(type_id), int(dim[0]) if dim else None)
        self.items[tag] = node
        return node


#: The properties a created material's default group carries, mirroring the
#: documented heat-transfer property set (Programming Reference p.148).
DEF_GROUP_FACTS: dict[str, Any] = {
    "value_types": {"density": "String", "heatcapacity": "String", "thermalconductivity": "StringMatrix"},
    "values": {"density": "7850[kg/m^3]", "heatcapacity": "475[J/(kg*K)]"},
    "matrices": {"thermalconductivity": [[
        "44.5[W/(m*K)]", "0", "0",
        "0", "44.5[W/(m*K)]", "0",
        "0", "0", "44.5[W/(m*K)]",
    ]]},
}


def new_material(tag: str, type_id: str) -> "FMaterial":
    """The material ``material().create(tag, type)`` builds: a ``def`` group."""
    return FMaterial(tag=tag, material_type=type_id,
                     groups={DEFAULT_GROUP: FPropertyGroup(tag=DEFAULT_GROUP, **DEF_GROUP_FACTS)})


class FMaterialList(FList):
    """``MaterialList``/``ComponentMaterialList``: ``create`` builds the node."""

    def __init__(self, *, unavailable: tuple[str, ...] = (), material_factory: Any = None) -> None:
        super().__init__(arity=2, unavailable=unavailable, node_type="Material")
        self.material_factory = material_factory or new_material

    def create(self, tag: str, *type_id: Any) -> Any:
        self.calls.append(("create", (tag,) + type_id))
        if "create" in self.unavailable:
            raise FakeEngineError("SecurityException: METHOD_REJECTED", code="METHOD_REJECTED")
        if tag in self.items:
            raise FakeEngineError(f"tag {tag} already exists")
        node = self.material_factory(tag, str(type_id[0]) if type_id else "Common")
        self.items[tag] = node
        return node


class FPropertyGroupList(FList):
    """``MaterialModelList``: ``create(tag, type)`` / ``get(tag)``.

    ``tags()`` lists the *additional* groups: as in COMSOL, the default ``def``
    group is reachable by tag and is not part of that list.
    """

    def __init__(self, *, unavailable: tuple[str, ...] = (), tags: list[str] | None = None) -> None:
        super().__init__(arity=2, unavailable=unavailable, node_type="MaterialModel")
        self.tags_: list[str] = list(tags or [])

    def tags(self) -> list[str]:
        self.calls.append(("tags", ()))
        return list(self.tags_)

    def create(self, tag: str, *type_id: Any) -> "FPropertyGroup":
        self.calls.append(("create", (tag,) + type_id))
        if "create" in self.unavailable:
            raise FakeEngineError("SecurityException: METHOD_REJECTED", code="METHOD_REJECTED")
        if tag in self.items:
            raise FakeEngineError(f"tag {tag} already exists")
        group = FPropertyGroup(tag=tag, type_id=str(type_id[0]) if type_id else "Common")
        self.items[tag] = group
        self.tags_.append(tag)
        return group

    def remove(self, tag: str) -> None:
        if "remove" in self.unavailable:
            raise FakeEngineError("SecurityException: METHOD_REJECTED", code="METHOD_REJECTED")
        super().remove(tag)
        if tag in self.tags_:
            # ``tags()`` is the enumerated list the engine reports; a removal
            # has to be visible there, not only in ``get(tag)``.
            self.tags_.remove(tag)


class NodeValuesMixin:
    """Give a fake node COMSOL's ``getX/set`` value semantics.

    The real nodes carry Java bean properties, so a worker write to a value
    (``node.selection_ = [...]`` from the engine's reflection path) is stored,
    while a *method* of the same name (``selection()``) stays callable.  The
    engine's ``resolve_node_path`` calls ``getattr(node, <method>)()``, so a
    fake that silently replaced the method with a value would break path
    resolution.
    """

    _values: dict[str, Any]

    def __setattr__(self, name: str, value: Any) -> None:
        if not name.startswith("_") and name in type(self).__dict__ and callable(type(self).__dict__[name]):
            values = self.__dict__.get("_values")
            if values is None:
                values = {}
                object.__setattr__(self, "_values", values)
            values[name] = value
            return
        object.__setattr__(self, name, value)


class FSelection:
    """``LocalSelection``/``EntitySelection`` stand-in bound to its owner node.

    The real COMSOL object is a *separate* node: ``feature.selection()`` returns
    a selection whose ``set(int...)`` writes entities, while the owning feature
    keeps its own ``set(name, value)`` property setter.  Keeping them apart in
    the fake is what makes a selection write distinguishable from a property
    write in these tests.
    """

    def __init__(self, owner: Any, *, model_tag: str | None = None) -> None:
        self.owner = owner
        self.model_tag = model_tag
        self.inherit_readback: Any = None
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def _check(self, name: str) -> None:
        if name in getattr(self.owner, "unavailable", set()):
            raise FakeEngineError("SecurityException: METHOD_REJECTED", code="METHOD_REJECTED")

    def model(self, *args: Any) -> Any:
        self.calls.append(("model", args))
        if args:
            self.model_tag = args[0]
            return None
        return self.model_tag

    def named(self, *args: Any) -> Any:
        self.calls.append(("named", args))
        self._check("named")
        if args:
            self.owner.named_ref = args[0]
            return None
        return self.owner.named_ref

    def inherit(self, *args: Any) -> Any:
        self.calls.append(("inherit", args))
        self._check("inherit")
        if args:
            self.owner.inheriting_ = bool(args[0])
            return None
        return self.owner.inheriting_

    def isInheriting(self) -> bool:
        self.calls.append(("isInheriting", ()))
        if self.inherit_readback is not None:
            return self.inherit_readback
        return bool(self.owner.inheriting_)

    def geom(self, *args: Any) -> Any:
        self.calls.append(("geom", args))
        self._check("geom")
        if not args:
            return self.owner.geometry_tag
        if len(args) == 1:
            self.owner.dim_ = int(args[0])
            return None
        self.owner.geometry_tag = str(args[0])
        self.owner.dim_ = int(args[1])
        return None

    def set(self, *entities: Any) -> None:
        self.calls.append(("set", entities))
        self._check("set")
        flat: list[int] = []
        for item in entities:
            if isinstance(item, (list, tuple)):
                flat.extend(int(value) for value in item)
            else:
                flat.append(int(item))
        self.owner.entities_ = flat

    def add(self, *entities: Any) -> None:
        self.calls.append(("add", entities))
        self._check("add")
        for item in entities:
            if isinstance(item, (list, tuple)):
                self.owner.entities_.extend(int(value) for value in item)
            else:
                self.owner.entities_.append(int(item))

    def all(self) -> None:
        self.calls.append(("all", ()))
        self._check("all")
        self.owner.entities_ = list(self.owner.entities_)

    def entities(self, *args: Any) -> list[int]:
        self.calls.append(("entities", args))
        self._check("entities")
        return list(self.owner.entities_)

    def dim(self, *args: Any) -> Any:
        self.calls.append(("dim", args))
        if args:
            self.owner.dim_ = int(args[0])
            return None
        return self.owner.dim_

    def dimension(self) -> list[int]:
        return [self.owner.dim_ if self.owner.dim_ is not None else 0]

    def __getattr__(self, name: str) -> Any:
        raise AttributeError(name)


class FPropertyGroup(NodeValuesMixin):
    """``MaterialModel`` (a ``ParameterEntity`` inside a material)."""

    def __init__(self, tag: str = "def", *, type_id: str = "Common",
                 values: Mapping[str, Any] | None = None,
                 value_types: Mapping[str, str] | None = None,
                 arrays: Mapping[str, list[Any]] | None = None,
                 matrices: Mapping[str, list[list[Any]]] | None = None,
                 numbers: Mapping[str, float] | None = None,
                 unavailable: tuple[str, ...] = (),
                 readback_overrides: Mapping[str, Any] | None = None,
                 inputs: list[str] | None = None,
                 description: str | None = None,
                 has_property_false: tuple[str, ...] = ()) -> None:
        self.tag_ = tag
        self.type_id = type_id
        self.values = dict(values or {})
        self.value_types = dict(value_types or {})
        self.arrays = dict(arrays or {})
        self.matrices = dict(matrices or {})
        self.numbers = dict(numbers or {})
        self.unavailable = set(unavailable)
        self.readback_overrides = dict(readback_overrides or {})
        self.inputs = list(inputs or [])
        self.descr_ = description
        #: Names the live build answers ``hasProperty() == False`` for while its
        #: value metadata still answers (the recorded W15_T017 behaviour on a
        #: freshly created ``Common`` material's ``def`` group); the write gate
        #: must not treat that probe as the existence proof.
        self.has_property_false = tuple(has_property_false)
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    # identity
    def tag(self) -> str:
        return self.tag_

    def getType(self) -> str:
        return self.type_id

    def descr(self, *args: Any) -> Any:
        self.calls.append(("descr", args))
        if args:
            self.descr_ = args[0]
        return self.descr_

    # metadata
    def properties(self) -> list[str]:
        names = set(self.value_types) | set(self.values) | set(self.arrays) | set(self.matrices) | set(self.numbers)
        return sorted(names)

    def hasProperty(self, name: str) -> bool:
        # ``properties()``/``getValueType`` answer for the documented def-group
        # names, but the live build's ``hasProperty`` probe answers false for
        # them on a fresh material (W15_T017); ``has_property_false`` models
        # exactly that pair.
        return name in self.properties() and name not in self.has_property_false

    def getValueType(self, name: str) -> str | None:
        if name in self.unavailable:
            raise FakeEngineError("SecurityException: METHOD_REJECTED", code="METHOD_REJECTED")
        return self.value_types.get(name)

    def getAllowedPropertyValues(self, name: str) -> list[str] | None:
        return None

    def _read(self, name: str) -> Any:
        if name in self.readback_overrides:
            return self.readback_overrides[name]
        value_type = self.value_types.get(name)
        if value_type in {"String", "StringArray", "StringMatrix"}:
            store = {"String": self.values, "StringArray": self.arrays, "StringMatrix": self.matrices}[value_type]
        elif value_type == "Double":
            store = self.numbers
        elif value_type in {"DoubleArray", "DoubleMatrix", "DoubleRowMatrix"}:
            store = self.arrays if value_type == "DoubleArray" else self.matrices
        elif value_type == "Int":
            store = self.numbers
        else:
            store = self.values
        if name in store:
            return store[name]
        for candidate in (self.values, self.numbers, self.arrays, self.matrices):
            if name in candidate:
                return candidate[name]
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

    def set(self, name: str, value: Any, *rest: Any) -> None:
        self.calls.append(("set", (name, value) + rest))
        if "set" in self.unavailable:
            raise FakeEngineError("SecurityException: METHOD_REJECTED", code="METHOD_REJECTED")
        value = unwrap_typed(value)
        value_type = self.value_types.get(name)
        if value_type in {"String", "StringArray", "StringMatrix"}:
            store = {"String": self.values, "StringArray": self.arrays, "StringMatrix": self.matrices}[value_type]
        elif value_type == "Double":
            store = self.numbers
        elif value_type in {"DoubleArray", "DoubleMatrix", "DoubleRowMatrix"}:
            store = self.arrays if value_type == "DoubleArray" else self.matrices
        else:
            store = self.values
        store[name] = value

    def input(self, *args: Any) -> Any:
        self.calls.append(("input", args))
        if "input" in self.unavailable:
            raise FakeEngineError("SecurityException: METHOD_REJECTED", code="METHOD_REJECTED")
        return list(self.inputs)

    def addInput(self, name: str) -> None:
        self.calls.append(("addInput", (name,)))
        if "addInput" in self.unavailable:
            raise FakeEngineError("SecurityException: METHOD_REJECTED", code="METHOD_REJECTED")
        if name in self.inputs:
            raise FakeEngineError(f"input {name} already exists")
        self.inputs.append(name)

    def removeInput(self, name: str) -> None:
        self.calls.append(("removeInput", (name,)))
        if "removeInput" in self.unavailable:
            raise FakeEngineError("SecurityException: METHOD_REJECTED", code="METHOD_REJECTED")
        if name not in self.inputs:
            raise FakeEngineError(f"input {name} does not exist")
        self.inputs.remove(name)

    def __getattr__(self, name: str) -> Any:
        raise AttributeError(name)


class FMaterial(NodeValuesMixin):
    """``Material`` node: a ``PropFeature`` with property groups and a selection."""

    def __init__(self, tag: str = "mat1", *, material_type: str = "Common",
                 groups: Mapping[str, FPropertyGroup] | None = None,
                 label: str | None = None,
                 entities: list[int] | None = None,
                 dim: int | None = None,
                 geometry: str | None = None,
                 named: str | None = None,
                 inheriting: bool = False,
                 selection_unavailable: bool = False,
                 property_group_unavailable: bool = False,
                 unavailable: tuple[str, ...] = (),
                 remove_error: str | None = None) -> None:
        self._values: dict[str, Any] = {}
        self.tag_ = tag
        self.material_type = material_type
        self.label_ = label if label is not None else f"{tag} label"
        # ``propertyGroup().tags()`` lists the non-default groups; the default
        # ``def`` group exists implicitly and is reached by tag.
        material_groups = dict(groups or {})
        if material_type in {"Common", "Switch", "PorousMedia"}:
            material_groups.setdefault(DEFAULT_GROUP, FPropertyGroup(tag=DEFAULT_GROUP))
        self.group_list = FPropertyGroupList()
        for group_tag, group in material_groups.items():
            self.group_list.items[group_tag] = group
        self.group_list.tags_ = [tag for tag in material_groups if tag != DEFAULT_GROUP]
        self.entities_ = list(entities) if entities is not None else []
        self.dim_ = dim
        self.geometry_tag = geometry
        self.named_ref = named
        self.inheriting_ = inheriting
        self.selection_unavailable = selection_unavailable
        self.property_group_unavailable = property_group_unavailable
        self.unavailable = set(unavailable)
        self.remove_error = remove_error
        self.select_geom_calls: list[tuple[Any, ...]] = []
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.selection_obj = FSelection(self)

    # identity
    def tag(self) -> str:
        return self.tag_

    def label(self, *args: Any) -> Any:
        self.calls.append(("label", args))
        if args:
            self.label_ = args[0]
        return self.label_

    def materialType(self, *args: Any) -> Any:
        self.calls.append(("materialType", args))
        if args:
            self.material_type = args[0]
        return self.material_type

    # property groups
    def propertyGroup(self, *args: Any) -> Any:
        self.calls.append(("propertyGroup", args))
        if self.property_group_unavailable or "propertyGroup" in self.unavailable:
            raise FakeEngineError("SecurityException: METHOD_REJECTED", code="METHOD_REJECTED")
        if not args:
            return self.group_list
        return self.group_list.items[args[0]]

    def feature(self, *args: Any) -> Any:
        self.calls.append(("feature", args))
        if not args:
            return FList(arity=3)
        raise AttributeError("feature")

    # selection surface (``Material.selection()`` -> LocalSelection)
    def selection(self, *args: Any) -> Any:
        self.calls.append(("selection", args))
        if self.selection_unavailable or "selection" in self.unavailable:
            raise FakeEngineError("SecurityException: METHOD_REJECTED", code="METHOD_REJECTED")
        return self.selection_obj

    def model(self, *args: Any) -> Any:
        return None

    def named(self, *args: Any) -> Any:
        if args:
            self.named_ref = args[0]
            return None
        return self.named_ref

    def geom(self, *args: Any) -> Any:
        self.select_geom_calls.append(args)
        if not args:
            return self.geometry_tag
        if len(args) == 1:
            self.dim_ = int(args[0])
            return None
        self.geometry_tag = str(args[0])
        self.dim_ = int(args[1])
        return None

    def set(self, *args: Any) -> None:
        values = list(args[0]) if args and isinstance(args[0], (list, tuple)) else [int(item) for item in args]
        self.entities_ = [int(item) for item in values]

    def all(self) -> None:
        self.entities_ = list(self.entities_)

    def entities(self, *args: Any) -> list[int]:
        return list(self.entities_)

    def inherit(self, *args: Any) -> Any:
        if args:
            self.inheriting_ = bool(args[0])
            return None
        return self.inheriting_

    def isInheriting(self) -> bool:
        return self.inheriting_

    def dim(self) -> int:
        return self.dim_ if self.dim_ is not None else 0

    def dimension(self) -> list[int]:
        return [self.dim()]

    def __getattr__(self, name: str) -> Any:
        raise AttributeError(name)


class FFeature(NodeValuesMixin):
    """A ``PhysicsFeature`` / ``MultiphysicsCoupling`` node."""

    def __init__(self, tag: str = "f1", *, type_id: str = "Feature",
                 children: Mapping[str, "FFeature"] | None = None,
                 values: Mapping[str, Any] | None = None,
                 value_types: Mapping[str, str] | None = None,
                 entities: list[int] | None = None, dim: int | None = None,
                 geometry: str | None = None, named: str | None = None,
                 inheriting: bool = False,
                 unavailable: tuple[str, ...] = (),
                 readback_overrides: Mapping[str, Any] | None = None,
                 settable: bool = True) -> None:
        self._values: dict[str, Any] = {}
        self.tag_ = tag
        self.type_id = type_id
        self.children = dict(children or {})
        self.values = dict(values or {})
        self.value_types = dict(value_types or {})
        self.entities_ = list(entities) if entities is not None else []
        self.dim_ = dim
        self.geometry_tag = geometry
        self.named_ref = named
        self.inheriting_ = inheriting
        self.unavailable = set(unavailable)
        self.readback_overrides = dict(readback_overrides or {})
        self.settable = settable
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.selection_obj = FSelection(self)

    def tag(self) -> str:
        return self.tag_

    def getType(self) -> str:
        return self.type_id

    def properties(self) -> list[str]:
        return sorted(set(self.value_types) | set(self.values))

    def hasProperty(self, name: str) -> bool:
        return name in self.properties()

    def getValueType(self, name: str) -> str | None:
        return self.value_types.get(name)

    def getAllowedPropertyValues(self, name: str) -> list[str] | None:
        return None

    def _read(self, name: str) -> Any:
        if name in self.readback_overrides:
            return self.readback_overrides[name]
        return self.values.get(name)

    def getString(self, name: str) -> Any:
        return self._read(name)

    def getStringArray(self, name: str) -> Any:
        return self._read(name)

    def getDouble(self, name: str) -> Any:
        return self._read(name)

    def getDoubleArray(self, name: str) -> Any:
        return self._read(name)

    def set(self, name: str, value: Any, *rest: Any) -> None:
        self.calls.append(("set", (name, value) + rest))
        if not self.settable:
            raise FakeEngineError("Unsupported property value")
        if "set" in self.unavailable:
            raise FakeEngineError("SecurityException: METHOD_REJECTED", code="METHOD_REJECTED")
        self.values[name] = unwrap_typed(value)

    def feature(self, *args: Any) -> Any:
        self.calls.append(("feature", args))
        if "feature" in self.unavailable:
            raise FakeEngineError("SecurityException: METHOD_REJECTED", code="METHOD_REJECTED")
        if args:
            return self.children[args[0]]
        container = FFeatureList(owner_children=self.children, unavailable=tuple(self.unavailable))
        return container

    def selection(self, *args: Any) -> Any:
        self.calls.append(("selection", args))
        if "selection" in self.unavailable:
            raise FakeEngineError("SecurityException: METHOD_REJECTED", code="METHOD_REJECTED")
        return self.selection_obj

    def named(self, *args: Any) -> Any:
        if args:
            self.named_ref = args[0]
            return None
        return self.named_ref

    def geom(self, *args: Any) -> Any:
        if not args:
            return self.geometry_tag
        if len(args) == 1:
            self.dim_ = int(args[0])
            return None
        self.geometry_tag = str(args[0])
        self.dim_ = int(args[1])
        return None

    def set_entities(self, entities: list[int]) -> None:
        self.entities_ = [int(item) for item in entities]

    def all(self) -> None:
        return None

    def entities(self, *args: Any) -> list[int]:
        return list(self.entities_)

    def inherit(self, *args: Any) -> Any:
        if args:
            if self.inheriting_ is False and args[0] is True and "inherit" in self.unavailable:
                raise FakeEngineError("SecurityException: METHOD_REJECTED", code="METHOD_REJECTED")
            self.inheriting_ = bool(args[0])
            return None
        return self.inheriting_

    def isInheriting(self) -> bool:
        return self.inheriting_

    def dim(self) -> int:
        return self.dim_ if self.dim_ is not None else 0

    def __getattr__(self, name: str) -> Any:
        raise AttributeError(name)


class FPhysicsList(FList):
    """``PhysicsList`` / ``ComponentPhysicsList``."""

    def __init__(self, *, unavailable: tuple[str, ...] = (),
                 factory: Any = None, require_geometry: bool = True) -> None:
        super().__init__(arity=3, unavailable=unavailable, node_type="Physics")
        self.factory = factory or (lambda tag, type_id, geom: FPhysics(tag=tag, type_id=type_id, geometry=geom))
        self.require_geometry = require_geometry
        self.geometry_present = True
        self.geometry_tag = "geom1"

    def create(self, tag: str, type_id: str, default_field_names: Any = (), *extra: Any) -> Any:
        self.calls.append(("create", (tag, type_id, default_field_names) + extra))
        if "create" in self.unavailable:
            raise FakeEngineError("SecurityException: METHOD_REJECTED", code="METHOD_REJECTED")
        if tag in self.items:
            raise FakeEngineError(f"tag {tag} already exists")
        if self.require_geometry and not self.geometry_present:
            raise FakeEngineError(f"physics interface {type_id} requires a geometry")
        node = self.factory(tag, type_id, self.geometry_tag)
        self.items[tag] = node
        return node


class FCouplingList(FList):
    """``MultiphysicsCouplingList`` / ``ComponentMultiphysicsCouplingList``."""

    def __init__(self, *, unavailable: tuple[str, ...] = ()) -> None:
        super().__init__(arity=4, unavailable=unavailable, node_type="MultiphysicsCoupling")

    def create(self, tag: str, type_id: str, *args: Any) -> Any:
        self.calls.append(("create", (tag, type_id) + args))
        if "create" in self.unavailable:
            raise FakeEngineError("SecurityException: METHOD_REJECTED", code="METHOD_REJECTED")
        if tag in self.items:
            raise FakeEngineError(f"tag {tag} already exists")
        if tag == "badcpl":
            raise FakeEngineError(f"unknown coupling type {type_id}")
        node = FFeature(tag=tag, type_id=type_id)
        self.items[tag] = node
        return node


class FPhysics(FFeature):
    """``Physics`` interface node."""

    def __init__(self, tag: str = "ht", *, type_id: str = "HeatTransfer",
                 geometry: str = "geom1", features: Mapping[str, FFeature] | None = None,
                 **kwargs: Any) -> None:
        super().__init__(tag=tag, type_id=type_id, geometry=geometry, children=features, **kwargs)
        self.feature_list = FFeatureList(owner_children=self.children, unavailable=tuple(self.unavailable))
        self.field_list = FList(arity=2, unavailable=tuple(self.unavailable), node_type="PhysicsField")
        self.prop_list = FList(arity=2, unavailable=tuple(self.unavailable), node_type="PhysicsProp")
        self.removed = False

    def feature(self, *args: Any) -> Any:
        self.calls.append(("feature", args))
        if args:
            return self.feature_list.items[args[0]]
        return self.feature_list

    def field(self, *args: Any) -> Any:
        if args:
            return self.field_list.items[args[0]]
        return self.field_list

    def prop(self, *args: Any) -> Any:
        if args:
            return self.prop_list.items[args[0]]
        return self.prop_list


class FComponent:
    """A ``Component`` node."""

    def __init__(self, tag: str = "comp1", *,
                 materials: Mapping[str, FMaterial] | None = None,
                 physics: Mapping[str, FPhysics] | None = None,
                 couplings: Mapping[str, FFeature] | None = None,
                 geometry: str = "geom1",
                 selections: Mapping[str, Any] | None = None,
                 material_factory: Any = None,
                 unavailable: tuple[str, ...] = ()) -> None:
        self.tag_ = tag
        self.material_list = FMaterialList(unavailable=unavailable, material_factory=material_factory)
        for material_tag, material in (materials or {}).items():
            self.material_list.items[material_tag] = material
        self.physics_list = FPhysicsList(unavailable=unavailable)
        for physics_tag, interface in (physics or {}).items():
            self.physics_list.items[physics_tag] = interface
        self.coupling_list = FCouplingList(unavailable=unavailable)
        for coupling_tag, coupling in (couplings or {}).items():
            self.coupling_list.items[coupling_tag] = coupling
        self.geometry_tag = geometry
        self.unavailable = set(unavailable)
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.geom_list = FList(arity=2, node_type="GeomSequence")
        self.geom_list.items[geometry] = FFeature(tag=geometry, type_id="GeomSequence2D",
                                                  geometry=geometry, values={"lengthunit": "m"})
        self.selection_list = FList(arity=2, node_type="SelectionFeature")
        for selection_tag, node in (selections or {}).items():
            self.selection_list.items[selection_tag] = node

    def tag(self) -> str:
        return self.tag_

    def getType(self) -> str:
        return "Component"

    def material(self, *args: Any) -> Any:
        self.calls.append(("material", args))
        if "material" in self.unavailable:
            raise FakeEngineError("SecurityException: METHOD_REJECTED", code="METHOD_REJECTED")
        if args:
            return self.material_list.items[args[0]]
        return self.material_list

    def physics(self, *args: Any) -> Any:
        self.calls.append(("physics", args))
        if "physics" in self.unavailable:
            raise FakeEngineError("SecurityException: METHOD_REJECTED", code="METHOD_REJECTED")
        if args:
            return self.physics_list.items[args[0]]
        return self.physics_list

    def multiphysics(self, *args: Any) -> Any:
        self.calls.append(("multiphysics", args))
        if "multiphysics" in self.unavailable:
            raise FakeEngineError("SecurityException: METHOD_REJECTED", code="METHOD_REJECTED")
        if args:
            return self.coupling_list.items[args[0]]
        return self.coupling_list

    def geom(self, *args: Any) -> Any:
        self.calls.append(("geom", args))
        if "geom" in self.unavailable:
            raise FakeEngineError("SecurityException: METHOD_REJECTED", code="METHOD_REJECTED")
        if args:
            return self.geom_list.items[args[0]]
        return self.geom_list

    def selection(self, *args: Any) -> Any:
        if args:
            return self.selection_list.items[args[0]]
        return self.selection_list

    def __getattr__(self, name: str) -> Any:
        raise AttributeError(name)


class FGlobalMultiphysics(FCouplingList):
    def get(self, tag: str) -> Any:
        return self.items[tag]


class FModel:
    """A ``Model`` node: components plus a global material and coupling list."""

    def __init__(self, *, components: Mapping[str, FComponent] | None = None,
                 materials: Mapping[str, FMaterial] | None = None,
                 couplings: Mapping[str, FFeature] | None = None,
                 products: list[str] | None = None,
                 used_products_unavailable: bool = False,
                 unavailable: tuple[str, ...] = ()) -> None:
        self.components = dict(components or {})
        self.component_list = FList(arity=1, node_type="Component")
        self.component_list.items = self.components
        self.material_list = FMaterialList()
        self.material_list.items = dict(materials or {})
        self.multiphysics_list = FGlobalMultiphysics()
        self.multiphysics_list.items = dict(couplings or {})
        self.products = list(products if products is not None else ["COMSOL Multiphysics"])
        self.used_products_unavailable = used_products_unavailable
        self.unavailable = set(unavailable)
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def component(self, *args: Any) -> Any:
        self.calls.append(("component", args))
        if "component" in self.unavailable:
            raise FakeEngineError("SecurityException: METHOD_REJECTED", code="METHOD_REJECTED")
        if args:
            return self.components[args[0]]
        return self.component_list

    def material(self, *args: Any) -> Any:
        self.calls.append(("material", args))
        if "material" in self.unavailable:
            raise FakeEngineError("SecurityException: METHOD_REJECTED", code="METHOD_REJECTED")
        if args:
            return self.material_list.items[args[0]]
        return self.material_list

    def multiphysics(self, *args: Any) -> Any:
        self.calls.append(("multiphysics", args))
        if "multiphysics" in self.unavailable:
            raise FakeEngineError("SecurityException: METHOD_REJECTED", code="METHOD_REJECTED")
        if args:
            return self.multiphysics_list.items[args[0]]
        return self.multiphysics_list

    def getUsedProducts(self) -> list[str]:
        self.calls.append(("getUsedProducts", ()))
        if self.used_products_unavailable or "getUsedProducts" in self.unavailable:
            raise FakeEngineError("SecurityException: METHOD_REJECTED", code="METHOD_REJECTED")
        return list(self.products)

    def getType(self) -> str:
        return "Model"

    def __getattr__(self, name: str) -> Any:
        raise AttributeError(name)


class FClient:
    def __init__(self, models: Mapping[str, Any]) -> None:
        self.models = dict(models)

    def model(self, tag: str) -> Any:
        if tag not in self.models:
            raise KeyError(tag)
        return self.models[tag]


class FWorker:
    """Fake PersistentJavaWorker: only ``client().model(tag)`` is used."""

    def __init__(self, model: Any, *, generation: int = 11, model_tag: str = "Model") -> None:
        self.generation = generation
        self._client = FClient({model_tag: model})

    def client(self) -> FClient:
        return self._client


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

MATERIAL_PATH = {"segments": [{"collection": "component", "tag": "comp1"}, {"collection": "material", "tag": "mat1"}]}
PHYSICS_PATH = {"segments": [{"collection": "component", "tag": "comp1"}, {"collection": "physics", "tag": "ht"}]}


def feature_path(*segments: tuple[str, str]) -> dict[str, Any]:
    return {"segments": [{"collection": collection, "tag": tag} for collection, tag in segments]}


def def_group(**kwargs: Any) -> FPropertyGroup:
    """A ``def`` property group with the documented heat-transfer properties."""
    base: dict[str, Any] = {
        "value_types": dict(DEF_GROUP_FACTS["value_types"]),
        "values": dict(DEF_GROUP_FACTS["values"]),
        "matrices": {key: value for key, value in DEF_GROUP_FACTS["matrices"].items()},
    }
    for key, value in kwargs.items():
        if key in {"value_types", "values", "matrices", "arrays", "numbers", "inputs", "readback_overrides"} \
                and isinstance(value, Mapping):
            merged = dict(base.get(key) or {})
            merged.update(value)
            base[key] = merged
        else:
            base[key] = value
    return FPropertyGroup(tag="def", **base)


def unwrap_typed(value: Any) -> Any:
    """A worker hands the *raw Java value* to a setter, not the wire envelope."""
    if isinstance(value, Mapping) and "kind" in value and "data" in value:
        return value["data"]
    return value


def material_node(**kwargs: Any) -> FMaterial:
    kwargs.setdefault("groups", {"def": def_group()})
    kwargs.setdefault("entities", [1, 2])
    kwargs.setdefault("dim", 3)
    kwargs.setdefault("geometry", "geom1")
    return FMaterial(**kwargs)


def physics_node(tag: str = "ht", **kwargs: Any) -> FPhysics:
    kwargs.setdefault("features", {
        "solid1": FFeature(tag="solid1", type_id="HeatTransferInSolids",
                           entities=[1, 2], dim=3, geometry="geom1"),
        "init1": FFeature(tag="init1", type_id="InitialValues", entities=[1, 2], dim=3, geometry="geom1",
                          value_types={"Tinit": "String"}, values={"Tinit": "293.15[K]"}),
    })
    kwargs.setdefault("type_id", "HeatTransferInSolids")
    return FPhysics(tag=tag, **kwargs)


def build_model(*, component: FComponent | None = None, global_materials: Mapping[str, FMaterial] | None = None,
                products: list[str] | None = None, used_products_unavailable: bool = False,
                couplings: Mapping[str, FFeature] | None = None) -> FModel:
    return FModel(
        components={"comp1": component if component is not None else FComponent(
            materials={"mat1": material_node()}, physics={"ht": physics_node()})},
        materials=global_materials,
        couplings=couplings,
        products=products,
        used_products_unavailable=used_products_unavailable,
    )


def worker_for(model: Any, **kwargs: Any) -> FWorker:
    return FWorker(model, **kwargs)


def expect_error(code: str, function: Any, *args: Any, **kwargs: Any) -> ExecutionContractError:
    with pytest.raises(ExecutionContractError) as info:
        function(*args, **kwargs)
    assert info.value.code == code, f"expected {code}, got {info.value.code}: {info.value}"
    return info.value


# ---------------------------------------------------------------------------
# aggregator surface
# ---------------------------------------------------------------------------


class TestAggregator:
    def test_all_w15_operations_are_published(self):
        expected = {
            "material.list", "material.create", "material.inspect", "material.set_properties",
            "material.group_manage", "material.selection_set", "material.remove", "material.validate",
            "physics.list", "physics.create", "physics.inspect", "physics.remove",
            "physics.feature_create", "physics.feature_update", "physics.feature_remove",
            "physics.selection_set", "physics.multiphysics_manage", "physics.initial_values_set",
            "physics.validate",
        }
        assert expected == set(w15.OPERATIONS)
        assert expected <= IMPLEMENTED_OPERATIONS
        assert expected <= set(DISPATCH)

    def test_w15_does_not_publish_the_catalogued_non_w15_operations(self):
        assert "material.import" not in w15.OPERATIONS
        assert "physics.pde_manage" not in w15.OPERATIONS

    def test_operation_origins_are_this_module(self):
        for operation_id in w15.OPERATIONS:
            assert OPERATION_ORIGINS[operation_id] == "_g3_w15"

    def test_effects_come_from_the_design_catalog(self):
        assert EFFECTS["material.list"] == "READ"
        assert EFFECTS["material.validate"] == "EVALUATE"
        assert EFFECTS["physics.create"] == "WRITE"
        assert EFFECTS["physics.multiphysics_manage"] == "DYNAMIC"
        assert EFFECTS["physics.validate"] == "EVALUATE"

    def test_dispatch_calls_the_module_function(self):
        model = build_model()
        result = DISPATCH["material.list"](worker_for(model), "Model", {"component": "comp1"})
        assert result["material_count"] == 1

    def test_unverified_paths_are_declared_with_reasons(self):
        paths = {row["path"] for row in w15.UNVERIFIED_PATHS}
        assert "material.import" in paths
        assert "physics.pde_manage" in paths
        assert all(row["reason"] for row in w15.UNVERIFIED_PATHS)


# ---------------------------------------------------------------------------
# licenses / products (T042 software surface)
# ---------------------------------------------------------------------------


class TestLicenseProbe:
    def test_reports_observed_products(self):
        model = build_model(products=["COMSOL Multiphysics", "Heat Transfer Module"])
        probe = w15.used_products(worker_for(model), "Model")
        assert probe["status"] == "OBSERVED"
        assert probe["products"] == ["COMSOL Multiphysics", "Heat Transfer Module"]
        assert probe["source"] == "model.getUsedProducts()"

    def test_missing_required_product_is_blocked_license(self):
        model = build_model(products=["COMSOL Multiphysics"])
        probe = w15.license_probe(worker_for(model), "Model", required_products=("RF",))
        assert probe["status"] == "BLOCKED_LICENSE"
        assert probe["blocked"] is True
        assert probe["missing_products"] == ["RF"]
        assert "simulated" in probe["message"]

    def test_present_required_product_is_ok(self):
        model = build_model(products=["COMSOL Multiphysics", "RF"])
        probe = w15.license_probe(worker_for(model), "Model", required_products=("rf",))
        assert probe["status"] == "OK"
        assert probe["blocked"] is False
        assert probe["missing_products"] == []

    def test_unreadable_product_list_is_unknown_not_fabricated(self):
        model = build_model(used_products_unavailable=True)
        probe = w15.license_probe(worker_for(model), "Model", required_products=("RF",))
        assert probe["status"] == "UNKNOWN"
        assert probe["blocked"] is False
        assert probe["missing_products"] is None
        assert probe["observed"]["allowlist_entry_required"] == "getUsedProducts"

    def test_probe_does_not_allocate_a_license(self):
        model = build_model(products=["COMSOL Multiphysics"])
        probe = w15.license_probe(worker_for(model), "Model", required_products=("HEATTRANSFER",))
        assert probe["read_only"] is True
        assert probe["allocates_license"] is False

    def test_validate_surfaces_a_missing_required_product_as_violation(self):
        model = build_model(products=["COMSOL Multiphysics"])
        result = w15.physics_validate(worker_for(model), "Model",
                                      {"checks": {"required_products": ["RF"]}})
        assert result["license_probe"]["status"] == "BLOCKED_LICENSE"
        assert any(row["rule"] == "license_required_products" for row in result["violations"])
        assert result["verdict"] == "VIOLATION"


# ---------------------------------------------------------------------------
# material.list
# ---------------------------------------------------------------------------


class TestMaterialList:
    def test_lists_component_materials_with_groups_and_domains(self):
        model = build_model()
        result = w15.material_list(worker_for(model), "Model", {"component": "comp1"})
        assert result["scope"] == "component"
        assert result["material_count"] == 1
        row = result["materials"][0]
        assert row["tag"] == "mat1"
        assert row["type_id"] is None  # Material has no getType() in the verified API
        assert row["property_groups"] == ["def"]
        assert row["entities"] == [1, 2]
        assert row["dimension"] == 3
        assert row["geometry"] == "geom1"
        assert row["label"] == "mat1 label"

    def test_lists_global_materials(self):
        model = build_model(global_materials={"gmat1": material_node(tag="gmat1")})
        result = w15.material_list(worker_for(model), "Model", {})
        assert result["scope"] == "model"
        assert [row["tag"] for row in result["materials"]] == ["gmat1"]
        assert result["tag_source"] == "model.material().tags()"

    def test_unknown_component_is_rejected(self):
        model = build_model()
        expect_error("NODE_NOT_FOUND", w15.material_list, worker_for(model), "Model",
                     {"component": "nope"})

    def test_missing_component_field_defaults_to_global_scope(self):
        model = build_model()
        result = w15.material_list(worker_for(model), "Model", {})
        assert result["scope"] == "model"
        assert result["material_count"] == 0

    def test_unknown_field_is_rejected(self):
        model = build_model()
        expect_error("INVALID_REQUEST", w15.material_list, worker_for(model), "Model", {"nope": 1})

    def test_property_group_probe_failure_is_reported_not_hidden(self):
        material = material_node(tag="mat1", property_group_unavailable=True)
        component = FComponent(materials={"mat1": material}, physics={"ht": physics_node()})
        model = build_model(component=component)
        result = w15.material_list(worker_for(model), "Model", {"component": "comp1"})
        row = result["materials"][0]
        assert row["property_groups"] == []
        assert row["property_group_error"]["allowlist_entry_required"] == "propertyGroup"
        assert result["errors"][0]["tag"] == "mat1"


# ---------------------------------------------------------------------------
# material.create
# ---------------------------------------------------------------------------


class TestMaterialCreate:
    def test_creates_a_common_material_with_properties_and_reads_back(self):
        model = build_model()
        result = w15.material_create(worker_for(model), "Model", {
            "component": "comp1", "tag": "mat2", "type_id": "Common",
            "definition": {"properties": {"density": "2700[kg/m^3]"}},
        })
        assert result["status"] == "APPLIED"
        assert result["type_id"] == "Common"
        assert result["readback"]["tags"] == ["mat1", "mat2"]
        assert result["applied"][0]["action"] == "create"
        assert result["applied"][1]["action"] == "set_properties"
        readback = result["applied"][1]["readback_values"]["density"]
        # readback_values carries the typed engine readback (the W13 shape):
        # the text lives under "data", the kind/shape are explicit.
        assert readback["data"] == "2700[kg/m^3]"
        assert readback["kind"] == "string"
        assert result["applied"][1]["properties"][0]["readback_match"] is True

    def test_unverified_type_is_rejected_before_the_write(self):
        model = build_model()
        error = expect_error("API_UNSUPPORTED", w15.material_create, worker_for(model), "Model", {
            "component": "comp1", "tag": "mat2", "type_id": "MatlabMaterial",
        })
        assert "verified COMSOL 6.4" in str(error)
        assert "mat2" not in model.components["comp1"].material_list.items

    def test_existing_tag_is_rejected(self):
        model = build_model()
        expect_error("TAG_CONFLICT", w15.material_create, worker_for(model), "Model", {
            "component": "comp1", "tag": "mat1", "type_id": "Common",
        })

    def test_switch_material_accepts_a_different_type_conflict_code_path(self):
        material = material_node(tag="sw1")
        material.material_type = "Switch"
        component = FComponent(materials={"sw1": material}, physics={})
        model = build_model(component=component)
        # A create of the same tag with a conflicting type is a TYPE_CONFLICT in
        # W13-style collection logic; a Material has no getType() readback, so
        # the layer falls back to a plain TAG_CONFLICT instead of guessing.
        expect_error("TAG_CONFLICT", w15.material_create, worker_for(model), "Model", {
            "component": "comp1", "tag": "sw1", "type_id": "Common",
        })

    def test_global_material_create(self):
        model = build_model()
        result = w15.material_create(worker_for(model), "Model", {"tag": "gmat1", "type_id": "Common"})
        assert result["scope"] == "model"
        assert [row["tag"] for row in w15.material_list(
            worker_for(model), "Model", {})["materials"]] == ["gmat1"]

    def test_unknown_definition_property_is_rejected_before_its_write(self):
        # A brand-new material is the one node whose property metadata cannot be
        # read before it exists, so the refusal lands on the first unverifiable
        # name *before that property is written*: the create stays visible, the
        # property is reported failed, nothing is stringified into the group.
        model = build_model()
        result = w15.material_create(worker_for(model), "Model", {
            "component": "comp1", "tag": "mat2", "type_id": "Common",
            "definition": {"properties": {"notasureproperty": "1"}},
        })
        assert result["status"] == "PARTIAL_FAILURE"
        assert result["applied"][0]["action"] == "create"
        assert result["failed"][0]["error"]["code"] == "API_UNSUPPORTED"
        assert result["failed"][0]["property"] == "notasureproperty"
        created = model.components["comp1"].material_list.items["mat2"]
        assert "notasureproperty" not in created.group_list.items["def"].values

    def test_property_write_failure_is_reported_with_the_create_kept(self):
        mismatching = def_group(readback_overrides={"density": "7800[kg/m^3]"})
        component = FComponent(
            materials={"mat1": material_node(tag="mat1")}, physics={},
            material_factory=lambda tag, type_id: material_node(tag=tag, groups={"def": mismatching}),
        )
        model = build_model(component=component)
        result = w15.material_create(worker_for(model), "Model", {
            "component": "comp1", "tag": "mat2", "type_id": "Common",
            "definition": {"properties": {"density": "2700[kg/m^3]"}},
        })
        assert result["status"] == "EXECUTION_STATE_UNKNOWN"
        assert result["partial_change"] is True
        assert result["execution_state_unknown"] is True
        assert result["applied"][0]["action"] == "create"
        assert result["failed"][0]["property"] == "density"

    def test_fresh_material_definition_writes_documented_names_when_hasproperty_says_no(self):
        """The live chain-A shape: ``create`` + ``propertyGroup("def").set(...)``.

        The documented example assigns density/heatcapacity/thermalconductivity to
        a *fresh* ``Common`` material; the probe answers false there (W15_T017),
        so the definition's properties are dispatched and read back instead of
        being refused before the write.
        """
        fresh_group = def_group(has_property_false=("thermalconductivity", "density", "heatcapacity"))
        component = FComponent(
            materials={"mat1": material_node(tag="mat1")}, physics={},
            material_factory=lambda tag, type_id: material_node(tag=tag, groups={"def": fresh_group}),
        )
        model = build_model(component=component)
        result = w15.material_create(worker_for(model), "Model", {
            "component": "comp1", "tag": "mat2", "type_id": "Common",
            "definition": {"properties": {
                "density": "2700[kg/m^3]",
                "heatcapacity": "900[J/(kg*K)]",
                "thermalconductivity": [["45[W/(m*K)]", "0", "0"],
                                        ["0", "45[W/(m*K)]", "0"],
                                        ["0", "0", "45[W/(m*K)]"]],
            }},
        })
        assert result["status"] == "APPLIED", result
        assert [row["action"] for row in result["applied"]] == ["create", "set_properties"]
        readbacks = result["applied"][1]["readback_values"]
        assert readbacks["density"]["data"] == "2700[kg/m^3]"
        assert readbacks["heatcapacity"]["data"] == "900[J/(kg*K)]"
        assert readbacks["thermalconductivity"]["data"][0][0] == "45[W/(m*K)]"
        assert [row["readback_match"] for row in result["applied"][1]["properties"]] == [True, True, True]
        assert [args[0] for method, args in fresh_group.calls if method == "set"] == [
            "density", "heatcapacity", "thermalconductivity"], fresh_group.calls

    def test_label_is_written_and_read_back(self):
        model = build_model()
        result = w15.material_create(worker_for(model), "Model", {
            "component": "comp1", "tag": "mat3", "type_id": "Common", "label": "Steel AISI 4340",
        })
        assert result["applied"][1] == {"action": "label", "requested": "Steel AISI 4340",
                                       "readback": "Steel AISI 4340"}

    def test_missing_required_field_is_rejected(self):
        model = build_model()
        expect_error("INVALID_REQUEST", w15.material_create, worker_for(model), "Model",
                     {"component": "comp1", "tag": "mat2"})


# ---------------------------------------------------------------------------
# material.inspect
# ---------------------------------------------------------------------------


class TestMaterialInspect:
    def test_reads_groups_properties_and_domain_binding(self):
        model = build_model()
        result = w15.material_inspect(worker_for(model), "Model", {"path": MATERIAL_PATH})
        assert result["tag"] == "mat1"
        assert result["property_groups"][0]["group"] == "def"
        names = {row["name"]: row for row in result["property_groups"][0]["properties"]}
        assert names["density"]["value"] == "7850[kg/m^3]"
        assert names["thermalconductivity"]["value_type"] == "StringMatrix"
        assert names["thermalconductivity"]["value"][0][0] == "44.5[W/(m*K)]"
        assert result["domain_coverage"]["entities"] == [1, 2]
        assert result["domain_coverage"]["entity_count"] == 2
        assert result["selection"]["geometry"] == "geom1"

    def test_group_filter_is_honoured(self):
        model = build_model()
        result = w15.material_inspect(worker_for(model), "Model",
                                      {"path": MATERIAL_PATH, "groups": ["nope"]})
        assert result["property_groups"] == []
        assert result["errors"][0]["error"]["code"] == "NODE_NOT_FOUND"

    def test_property_filter_limits_the_read(self):
        model = build_model()
        result = w15.material_inspect(worker_for(model), "Model",
                                      {"path": MATERIAL_PATH, "properties": ["density"]})
        rows = result["property_groups"][0]["properties"]
        assert [row["name"] for row in rows] == ["density"]
        assert result["property_groups"][0]["property_count"] == 1

    def test_path_must_end_in_a_material_segment(self):
        model = build_model()
        expect_error("INVALID_NODE_PATH", w15.material_inspect, worker_for(model), "Model",
                     {"path": PHYSICS_PATH})

    def test_unresolvable_path_is_a_node_not_found(self):
        model = build_model()
        expect_error("NODE_NOT_FOUND", w15.material_inspect, worker_for(model), "Model",
                     {"path": feature_path(("component", "comp1"), ("material", "nope"))})


# ---------------------------------------------------------------------------
# material.set_properties (T017: k(T)/Cp(T) + anisotropic tensor)
# ---------------------------------------------------------------------------


class TestMaterialSetProperties:
    def test_temperature_dependent_expressions_round_trip_as_text(self):
        group = def_group(value_types={
            "thermalconductivity": "String",
            "heatcapacity": "String",
        }, values={"thermalconductivity": "44.5[W/(m*K)]", "heatcapacity": "475[J/(kg*K)]"})
        component = FComponent(materials={"mat1": material_node(tag="mat1", groups={"def": group})},
                               physics={})
        model = build_model(component=component)
        result = w15.material_set_properties(worker_for(model), "Model", {
            "path": MATERIAL_PATH, "group": "def",
            "properties": {
                "thermalconductivity": "44.5[W/(m*K)]+(0.01[W/(m*K^2)])*(T-293.15[K])",
                "heatcapacity": "475[J/(kg*K)]",
            },
        })
        assert result["status"] == "APPLIED"
        values = result["applied"][0]["readback_values"]
        assert values["thermalconductivity"]["data"] == "44.5[W/(m*K)]+(0.01[W/(m*K^2)])*(T-293.15[K])"
        assert "never converts units" in result["unit_policy"]

    def test_anisotropic_tensor_matrix_is_written_and_read_back(self):
        component = FComponent(materials={"mat1": material_node()}, physics={})
        model = build_model(component=component)
        matrix = [["0.2[W/(m*K)]", "0", "0"],
                  ["0", "44.5[W/(m*K)]", "0"],
                  ["0", "0", "44.5[W/(m*K)]"]]
        result = w15.material_set_properties(worker_for(model), "Model", {
            "path": MATERIAL_PATH, "group": "def", "properties": {"thermalconductivity": matrix},
        })
        assert result["status"] == "APPLIED"
        assert result["applied"][0]["readback_values"]["thermalconductivity"]["data"] == matrix

    def test_missing_property_is_rejected_before_the_write(self):
        model = build_model()
        error = expect_error("API_UNSUPPORTED", w15.material_set_properties, worker_for(model), "Model", {
            "path": MATERIAL_PATH, "group": "def", "properties": {"notaproperty": "1"},
        })
        assert "never creates an unknown material/physics property" in str(error)

    def test_missing_group_is_rejected_without_creating_it(self):
        model = build_model()
        error = expect_error("NODE_NOT_FOUND", w15.material_set_properties, worker_for(model), "Model", {
            "path": MATERIAL_PATH, "group": "nope", "properties": {"density": "1[kg/m^3]"},
        })
        assert "group_manage action 'create'" in str(error)

    def test_readback_mismatch_is_an_execution_state_unknown_partial(self):
        group = def_group(readback_overrides={"heatcapacity": "400[J/(kg*K)]"})
        component = FComponent(materials={"mat1": material_node(tag="mat1", groups={"def": group})},
                               physics={})
        model = build_model(component=component)
        result = w15.material_set_properties(worker_for(model), "Model", {
            "path": MATERIAL_PATH, "group": "def", "properties": {"heatcapacity": "475[J/(kg*K)]"},
        })
        assert result["status"] == "EXECUTION_STATE_UNKNOWN"
        assert result["execution_state_unknown"] is True
        assert result["partial_change"] is True

    def test_provenance_is_caller_declared_only(self):
        model = build_model()
        result = w15.material_set_properties(worker_for(model), "Model", {
            "path": MATERIAL_PATH, "group": "def", "properties": {"density": "7850[kg/m^3]"},
            "provenance": {"source": "matlib", "reference": "steel"},
        })
        assert result["provenance"]["source"] == "matlib"
        assert result["provenance"]["status"] == "caller_declared_not_engine_verified"

    def test_typed_value_form_is_accepted(self):
        model = build_model()
        result = w15.material_set_properties(worker_for(model), "Model", {
            "path": MATERIAL_PATH, "group": "def",
            "properties": {"density": {"kind": "string", "shape": [], "data": "7850[kg/m^3]"}},
        })
        assert result["status"] == "APPLIED"
        assert result["applied"][0]["properties"][0]["value"]["kind"] == "string"
        assert result["applied"][0]["properties"][0]["readback_match"] is True

    def test_wrong_json_type_for_a_declared_kind_is_rejected(self):
        group = def_group(value_types={"density": "Double"})
        component = FComponent(materials={"mat1": material_node(tag="mat1", groups={"def": group})},
                               physics={})
        model = build_model(component=component)
        expect_error("PROPERTY_TYPE_MISMATCH", w15.material_set_properties, worker_for(model), "Model", {
            "path": MATERIAL_PATH, "group": "def", "properties": {"density": "7850[kg/m^3]"},
        })

    def test_quantity_object_requires_a_value_and_a_unit(self):
        model = build_model()
        expect_error("INVALID_REQUEST", w15.material_set_properties, worker_for(model), "Model", {
            "path": MATERIAL_PATH, "group": "def", "properties": {"density": {"value": 7850.0}},
        })
        # A *text* expression is the only spelling for a string-kind property
        # (the layer never converts units), so {"value": "7850[kg/m^3]"} with an
        # explicit unit is accepted while a bare number is refused above.
        ok = w15.material_set_properties(worker_for(model), "Model", {
            "path": MATERIAL_PATH, "group": "def",
            "properties": {"density": {"value": "7850[kg/m^3]", "unit": "kg/m^3"}},
        })
        assert ok["status"] == "APPLIED"

    def test_documented_def_properties_are_written_when_hasproperty_says_no(self):
        """The recorded live shape: ``hasProperty()`` false, engine metadata present.

        W15_T017 (evidence/phase4/runs/20260920T130620Z-g3-live) showed that on a
        freshly created ``Common`` material the probe
        ``propertyGroup("def").hasProperty(...)`` is false for
        thermalconductivity/density/heatcapacity while the documented API assigns
        exactly those properties to that same fresh material, so the probe cannot
        be the write gate: the payload is dispatched and the post-write readback
        decides.
        """
        group = def_group(has_property_false=("thermalconductivity", "density", "heatcapacity"))
        component = FComponent(materials={"mat1": material_node(tag="mat1", groups={"def": group})},
                               physics={})
        model = build_model(component=component)
        thermal = [["10[W/(m*K)]", "0", "0"],
                   ["0", "10[W/(m*K)]", "0"],
                   ["0", "0", "10[W/(m*K)]"]]
        result = w15.material_set_properties(worker_for(model), "Model", {
            "path": MATERIAL_PATH, "group": "def",
            "properties": [
                {"name": "thermalconductivity",
                 "value": {"kind": "expression", "shape": [3, 3], "unit": "W/(m*K)", "data": thermal}},
                {"name": "density",
                 "value": {"kind": "expression", "shape": [], "data": "7850[kg/m^3]"}},
                {"name": "heatcapacity",
                 "value": {"kind": "expression", "shape": [],
                           "data": "500[J/(kg*K)]+0.1[J/(kg*K^2)]*(T-293.15[K])"}},
            ],
        })
        assert result["status"] == "APPLIED", result
        readbacks = result["applied"][0]["readback_values"]
        assert readbacks["thermalconductivity"]["data"] == thermal
        assert readbacks["density"]["data"] == "7850[kg/m^3]"
        assert "0.1[J/(kg*K^2)]" in readbacks["heatcapacity"]["data"]
        assert [row["readback_match"] for row in result["applied"][0]["properties"]] == [True, True, True]
        assert [args[0] for method, args in group.calls if method == "set"] == [
            "thermalconductivity", "density", "heatcapacity"], group.calls

    def test_a_mixed_payload_still_refuses_an_undocumented_name_before_any_write(self):
        """The relaxed probe gate is still fail-closed for an unknown name.

        A documented name in the same payload does not open the write: the
        undocumented name is refused *before* the first setter, so nothing is
        created in the group.
        """
        group = def_group(has_property_false=("density", "notadocumentedproperty"))
        component = FComponent(materials={"mat1": material_node(tag="mat1", groups={"def": group})},
                               physics={})
        model = build_model(component=component)
        error = expect_error("API_UNSUPPORTED", w15.material_set_properties, worker_for(model), "Model", {
            "path": MATERIAL_PATH, "group": "def",
            "properties": {"density": "7850[kg/m^3]", "notadocumentedproperty": "1"},
        })
        assert "hasProperty() is false" in str(error)
        assert "never creates an unknown material/physics property" in str(error)
        assert not [call for call in group.calls if call[0] == "set"], group.calls

    def test_hasproperty_false_without_engine_metadata_keeps_the_frozen_gate(self):
        """No metadata, no readable getter: refused, never reported as a success.

        The documented table admits the name, but the frozen G2 property-set
        discipline can then neither dispatch nor read the property back; that
        refusal is reported before any setter call instead of a fabricated
        success, and it is no longer the ``hasProperty`` refusal.
        """
        group = FPropertyGroup(tag="def", has_property_false=("density",))
        component = FComponent(materials={"mat1": material_node(tag="mat1", groups={"def": group})},
                               physics={})
        model = build_model(component=component)
        error = expect_error("API_UNSUPPORTED", w15.material_set_properties, worker_for(model), "Model", {
            "path": MATERIAL_PATH, "group": "def", "properties": {"density": "7850[kg/m^3]"},
        })
        assert "authoritative value metadata is unavailable" in str(error)
        assert "does not exist on this COMSOL node" not in str(error)
        assert not [call for call in group.calls if call[0] == "set"], group.calls


class TestDocumentedMaterialPropertyTable:
    """The ``def``-group table is typed, bounded and cited (not a wildcard)."""

    def test_documented_def_property_table_is_typed_and_cited(self):
        table = w15.MATERIAL_DEF_GROUP_PROPERTIES
        assert set(table) == {"density", "heatcapacity", "thermalconductivity"}
        for name, row in table.items():
            assert row["kind"] == "string"
            assert row["value_type"] in {"String", "StringArray", "StringMatrix"}
            assert isinstance(row["shape_rank"], int) and row["shape_rank"] >= 0
            assert "Application Programming Guide 6.4" in row["source"]
            assert "Programming Reference 6.4" in row["source"]
        assert table["density"]["shape_rank"] == 0
        assert table["heatcapacity"]["shape_rank"] == 0
        assert table["thermalconductivity"]["shape_rank"] == 2
        assert table["thermalconductivity"]["value_type"] == "StringMatrix"

    def test_the_table_is_bounded_to_the_verified_heat_transfer_names(self):
        # The same three names material.validate already checks; a name outside
        # this set is refused unless the live node's own metadata knows it.
        assert set(w15.MATERIAL_DEF_GROUP_PROPERTIES) == set(w15.MATERIAL_REQUIRED_PROPERTIES["HeatTransfer"]["def"])


# ---------------------------------------------------------------------------
# material.group_manage
# ---------------------------------------------------------------------------


class TestMaterialGroupManage:
    def test_create_group_and_write_properties(self):
        component = FComponent(materials={"mat1": material_node(tag="mat1", groups={})}, physics={})
        model = build_model(component=component)
        result = w15.material_group_manage(worker_for(model), "Model", {
            "path": MATERIAL_PATH, "action": "create", "group": "Anisotropic",
            "definition": {"properties": {}},
        })
        assert result["status"] == "APPLIED"
        assert result["applied"][0]["readback"]["listed_tags"] == ["Anisotropic"]
        assert result["applied"][0]["readback"]["tags"] == ["def", "Anisotropic"]

    def test_create_existing_group_is_rejected(self):
        model = build_model()
        expect_error("TAG_CONFLICT", w15.material_group_manage, worker_for(model), "Model", {
            "path": MATERIAL_PATH, "action": "create", "group": "def",
        })

    def test_remove_group_reads_back_the_removal(self):
        component = FComponent(materials={"mat1": material_node(tag="mat1", groups={
            "def": def_group(), "extra": FPropertyGroup(tag="extra")})}, physics={})
        model = build_model(component=component)
        result = w15.material_group_manage(worker_for(model), "Model", {
            "path": MATERIAL_PATH, "action": "remove", "group": "extra",
        })
        assert result["applied"][0]["readback"]["tags"] == ["def"]

    def test_inspect_group_lists_properties_and_inputs(self):
        group = def_group(inputs=["T"])
        component = FComponent(materials={"mat1": material_node(tag="mat1", groups={"def": group})},
                               physics={})
        model = build_model(component=component)
        result = w15.material_group_manage(worker_for(model), "Model", {
            "path": MATERIAL_PATH, "action": "inspect", "group": "def",
        })
        row = result["applied"][0]
        assert "density" in row["properties"]
        assert row["inputs"] == ["T"]
        assert row["type_id"] == "Common"

    def test_update_adds_and_removes_inputs_with_readback(self):
        group = def_group(inputs=["T"])
        component = FComponent(materials={"mat1": material_node(tag="mat1", groups={"def": group})},
                               physics={})
        model = build_model(component=component)
        result = w15.material_group_manage(worker_for(model), "Model", {
            "path": MATERIAL_PATH, "action": "update", "group": "def",
            "definition": {"inputs": {"add": ["p"], "remove": ["T"]}},
        })
        assert result["status"] == "APPLIED"
        assert result["applied"][0]["inputs"]["readback"] == ["p"]

    def test_update_dropping_a_missing_input_is_rejected(self):
        model = build_model()
        result = w15.material_group_manage(worker_for(model), "Model", {
            "path": MATERIAL_PATH, "action": "update", "group": "def",
            "definition": {"inputs": {"remove": ["nope"]}},
        })
        assert result["status"] == "FAILED"
        assert result["failed"][0]["error"]["code"] == "NODE_NOT_FOUND"

    def test_unsupported_action_is_rejected(self):
        model = build_model()
        expect_error("INVALID_REQUEST", w15.material_group_manage, worker_for(model), "Model", {
            "path": MATERIAL_PATH, "action": "duplicate", "group": "def",
        })

    def test_update_requires_a_definition(self):
        model = build_model()
        expect_error("INVALID_REQUEST", w15.material_group_manage, worker_for(model), "Model", {
            "path": MATERIAL_PATH, "action": "update", "group": "def",
        })


# ---------------------------------------------------------------------------
# material.selection_set / material.remove / material.validate
# ---------------------------------------------------------------------------


class TestMaterialSelectionSet:
    def test_binds_a_named_domain_selection(self):
        selection = FFeature(tag="sel1", type_id="Explicit", entities=[1, 2], dim=3)
        component = FComponent(materials={"mat1": material_node(tag="mat1")}, physics={},
                               selections={"sel1": selection})
        model = build_model(component=component)
        result = w15.material_selection_set(worker_for(model), "Model", {
            "path": MATERIAL_PATH,
            "selection": {"kind": "named", "component": "comp1", "tag": "sel1"},
        })
        assert result["status"] == "APPLIED"
        material = component.material_list.items["mat1"]
        assert material.named_ref == "sel1"
        assert result["applied"][0]["applied"][0]["method"] == "named"

    def test_explicit_selection_requires_a_dimension(self):
        model = build_model()
        expect_error("INVALID_REQUEST", w15.material_selection_set, worker_for(model), "Model", {
            "path": MATERIAL_PATH, "selection": {"kind": "explicit", "entities": [1, 2]},
        })

    def test_explicit_selection_binds_entities_and_reads_back(self):
        model = build_model()
        result = w15.material_selection_set(worker_for(model), "Model", {
            "path": MATERIAL_PATH,
            "selection": {"kind": "explicit", "component": "comp1", "geometry": "geom1",
                          "entity_dimension": 3, "entities": [2, 1]},
        })
        assert result["status"] == "APPLIED"
        assert result["selection_readback"]["entities"] == [1, 2]
        assert result["applied"][0]["applied"][0]["readback"] == [1, 2]

    def test_unavailable_selection_accessor_is_refused_before_writing(self):
        material = material_node(tag="mat1", selection_unavailable=True)
        component = FComponent(materials={"mat1": material}, physics={})
        model = build_model(component=component)
        expect_error("API_UNSUPPORTED", w15.material_selection_set, worker_for(model), "Model", {
            "path": MATERIAL_PATH,
            "selection": {"kind": "named", "component": "comp1", "tag": "sel1"},
        })

    def test_inherit_is_recorded_with_its_readback(self):
        material = material_node(tag="mat1")
        component = FComponent(materials={"mat1": material}, physics={})
        model = build_model(component=component)
        result = w15.material_selection_set(worker_for(model), "Model", {
            "path": MATERIAL_PATH, "selection": {"kind": "inherited", "component": "comp1"},
        })
        assert result["applied"][0]["applied"][0] == {"method": "inherit", "requested": True, "readback": True}


class TestMaterialRemove:
    def test_removes_and_reports_the_domains_that_lose_the_material(self):
        component = FComponent(materials={"mat1": material_node(tag="mat1")}, physics={})
        model = build_model(component=component)
        result = w15.material_remove(worker_for(model), "Model", {"path": MATERIAL_PATH})
        assert result["status"] == "APPLIED"
        assert result["domains_losing_material"]["entities"] == [1, 2]
        assert component.material_list.items == {}

    def test_missing_material_is_rejected(self):
        model = build_model()
        expect_error("NODE_NOT_FOUND", w15.material_remove, worker_for(model), "Model",
                     {"path": feature_path(("component", "comp1"), ("material", "nope"))})

    def test_global_material_can_be_removed(self):
        model = build_model(global_materials={"gmat1": material_node(tag="gmat1")})
        result = w15.material_remove(worker_for(model), "Model", {
            "path": {"segments": [{"collection": "material", "tag": "gmat1"}]},
        })
        assert result["status"] == "APPLIED"
        assert model.material_list.items == {}


class TestMaterialValidate:
    def test_complete_heat_transfer_material_has_no_violation(self):
        model = build_model()
        result = w15.material_validate(worker_for(model), "Model", {"component": "comp1"})
        assert result["verdict"] == "NO_VIOLATION_DETECTED"
        assert result["violations"] == []
        assert result["checked_rules"][0] == "material_selection_non_empty"

    def test_removed_required_property_is_a_violation(self):
        group = FPropertyGroup(tag="def",
                               value_types={"density": "String", "heatcapacity": "String"},
                               values={"density": "7850[kg/m^3]", "heatcapacity": "475[J/(kg*K)]"})
        component = FComponent(materials={"mat1": material_node(tag="mat1", groups={"def": group})},
                               physics={"ht": physics_node()})
        model = build_model(component=component)
        result = w15.material_validate(worker_for(model), "Model", {"component": "comp1"})
        assert result["verdict"] == "VIOLATION"
        violation = result["violations"][0]
        assert violation["rule"] == "material_required_properties"
        assert "thermalconductivity" in violation["detail"]
        assert "mat1" in violation["missing_in"][0]

    def test_missing_material_for_an_enabled_interface_is_a_violation(self):
        component = FComponent(materials={}, physics={"ht": physics_node()})
        model = build_model(component=component)
        result = w15.material_validate(worker_for(model), "Model", {"component": "comp1"})
        assert result["verdict"] == "VIOLATION"
        assert result["violations"][0]["rule"] == "material_definition_present"

    def test_overlapping_domains_are_a_violation(self):
        second = material_node(tag="mat2", entities=[2, 3])
        component = FComponent(materials={"mat1": material_node(tag="mat1"), "mat2": second},
                               physics={"ht": physics_node()})
        model = build_model(component=component)
        result = w15.material_validate(worker_for(model), "Model", {"component": "comp1"})
        rules = {row["rule"] for row in result["violations"]}
        assert "material_domain_conflict" in rules

    def test_empty_selection_is_a_violation(self):
        empty = material_node(tag="mat1", entities=[])
        component = FComponent(materials={"mat1": empty}, physics={})
        model = build_model(component=component)
        result = w15.material_validate(worker_for(model), "Model", {"component": "comp1"})
        rules = {row["rule"] for row in result["violations"]}
        assert "material_selection_non_empty" in rules

    def test_uncovered_physics_family_is_unknown_not_a_pass(self):
        interface = physics_node(tag="es", type_id="Electrostatics")
        component = FComponent(materials={"mat1": material_node(tag="mat1")}, physics={"es": interface})
        model = build_model(component=component)
        result = w15.material_validate(worker_for(model), "Model", {"component": "comp1"})
        assert result["verdict"] == "UNKNOWN"
        assert any(row["rule"] == "material_required_properties" for row in result["unknown"])

    def test_unreadable_selection_is_unknown(self):
        material = material_node(tag="mat1", selection_unavailable=True)
        component = FComponent(materials={"mat1": material}, physics={})
        model = build_model(component=component)
        result = w15.material_validate(worker_for(model), "Model", {"component": "comp1"})
        assert any(row["rule"] == "material_domain_coverage" for row in result["unknown"])

    def test_scope_path_restricts_the_check(self):
        model = build_model()
        result = w15.material_validate(worker_for(model), "Model", {"scope": MATERIAL_PATH})
        assert result["materials"][0]["tag"] == "mat1"
        assert result["scope"] == MATERIAL_PATH

    def test_scope_must_be_a_material(self):
        model = build_model()
        expect_error("INVALID_NODE_PATH", w15.material_validate, worker_for(model), "Model",
                     {"scope": PHYSICS_PATH})


# ---------------------------------------------------------------------------
# physics.list / physics.create
# ---------------------------------------------------------------------------


class TestPhysicsList:
    def test_lists_interfaces_with_type_geometry_features_and_couplings(self):
        model = build_model(couplings={"te1": FFeature(tag="te1", type_id="ThermalExpansion")})
        result = w15.physics_list(worker_for(model), "Model", {"component": "comp1"})
        assert result["component"] == "comp1"
        assert result["interface_count"] == 1
        row = result["interfaces"][0]
        assert row["tag"] == "ht"
        assert row["type_id"] == "HeatTransferInSolids"
        assert row["geometry"] == "geom1"
        assert row["feature_tags"] == ["solid1", "init1"]
        assert row["selection"]["dimension"] == 0  # the fake physics node carries no selection

    def test_single_component_model_is_resolved_without_guessing_the_tag(self):
        model = build_model()
        result = w15.physics_list(worker_for(model), "Model", {})
        assert result["component"] == "comp1"

    def test_multi_component_model_requires_explicit_component(self):
        model = build_model()
        model.components["comp2"] = FComponent(tag="comp2", physics={})
        model.component_list.items["comp2"] = model.components["comp2"]
        expect_error("INVALID_REQUEST", w15.physics_list, worker_for(model), "Model", {})

    def test_global_couplings_are_listed(self):
        model = build_model(couplings={"te1": FFeature(tag="te1", type_id="ThermalExpansion")})
        result = w15.physics_list(worker_for(model), "Model", {"component": "comp1"})
        assert result["multiphysics_couplings"] == ["te1"]

    def test_unknown_component_is_rejected(self):
        model = build_model()
        expect_error("NODE_NOT_FOUND", w15.physics_list, worker_for(model), "Model",
                     {"component": "nope"})


class TestPhysicsCreate:
    def test_creates_an_interface_on_a_verified_geometry(self):
        component = FComponent(materials={}, physics={})
        model = build_model(component=component)
        result = w15.physics_create(worker_for(model), "Model", {
            "component": "comp1", "tag": "ht", "type_id": "HeatTransferInSolids", "geometry": "geom1",
        })
        assert result["status"] == "APPLIED"
        assert result["type_id"] == "HeatTransferInSolids"
        assert result["applied"][0]["readback"]["tags"] == ["ht"]
        assert result["applied"][0]["readback"]["geometry"] == "geom1"

    def test_unverified_interface_type_is_rejected_before_the_write(self):
        component = FComponent(materials={}, physics={})
        model = build_model(component=component)
        error = expect_error("API_UNSUPPORTED", w15.physics_create, worker_for(model), "Model", {
            "component": "comp1", "tag": "xyz", "type_id": "HeatTransferInSolidsX", "geometry": "geom1",
        })
        assert "verified COMSOL 6.4" in str(error)
        assert component.physics_list.items == {}

    def test_missing_geometry_is_rejected(self):
        component = FComponent(materials={}, physics={})
        model = build_model(component=component)
        expect_error("NODE_NOT_FOUND", w15.physics_create, worker_for(model), "Model", {
            "component": "comp1", "tag": "ht", "type_id": "HeatTransferInSolids", "geometry": "geom2",
        })

    def test_existing_interface_tag_is_rejected(self):
        model = build_model()
        expect_error("TAG_CONFLICT", w15.physics_create, worker_for(model), "Model", {
            "component": "comp1", "tag": "ht", "type_id": "HeatTransferInSolids", "geometry": "geom1",
        })

    def test_dependent_variables_are_refused_with_a_documented_reason(self):
        component = FComponent(materials={}, physics={})
        model = build_model(component=component)
        error = expect_error("API_UNSUPPORTED", w15.physics_create, worker_for(model), "Model", {
            "component": "comp1", "tag": "ht", "type_id": "HeatTransferInSolids", "geometry": "geom1",
            "dependent_variables": ["T"],
        })
        assert "variable-name/order contract" in str(error)

    def test_license_hint_is_recorded_from_the_vocabulary(self):
        component = FComponent(materials={}, physics={})
        model = build_model(component=component)
        result = w15.physics_create(worker_for(model), "Model", {
            "component": "comp1", "tag": "chem", "type_id": "Chemistry", "geometry": "geom1",
        })
        assert result["license_hint"]["products"] == ["CHEM"]

    def test_base_product_interface_reports_an_empty_license_hint(self):
        component = FComponent(materials={}, physics={})
        model = build_model(component=component)
        result = w15.physics_create(worker_for(model), "Model", {
            "component": "comp1", "tag": "ht", "type_id": "HeatTransferInSolids", "geometry": "geom1",
        })
        assert result["license_hint"]["products"] == []
        assert result["license_hint"]["source"] == "base_product"

    def test_readback_type_mismatch_is_execution_state_unknown(self):
        class LyingList(FPhysicsList):
            def create(self, tag, type_id, default_field_names=(), *extra):
                node = super().create(tag, type_id, default_field_names, *extra)
                self.items[tag] = FPhysics(tag=tag, type_id="SomethingElse", geometry="")
                return self.items[tag]

        component = FComponent(materials={}, physics={})
        component.physics_list = LyingList()
        model = build_model(component=component)
        expect_error("EXECUTION_STATE_UNKNOWN", w15.physics_create, worker_for(model), "Model", {
            "component": "comp1", "tag": "ht", "type_id": "HeatTransferInSolids", "geometry": "geom1",
        })


# ---------------------------------------------------------------------------
# physics.inspect / physics.remove
# ---------------------------------------------------------------------------


class TestPhysicsInspect:
    def test_reads_the_feature_tree_with_selections(self):
        model = build_model()
        result = w15.physics_inspect(worker_for(model), "Model", {"path": PHYSICS_PATH})
        assert result["tag"] == "ht"
        assert result["type_id"] == "HeatTransferInSolids"
        assert result["geometry"] == "geom1"
        collections = {row["collection"]: row for row in result["children"]}
        assert collections["feature"]["children"][0]["tag"] == "solid1"
        assert collections["feature"]["children"][0]["selection"]["entities"] == [1, 2]

    def test_depth_limits_nested_children(self):
        inner = FFeature(tag="inner", type_id="Nested")
        outer = FFeature(tag="outer", type_id="Container", children={"inner": inner})
        interface = physics_node(tag="ht", features={"outer": outer})
        component = FComponent(materials={}, physics={"ht": interface})
        model = build_model(component=component)
        shallow = w15.physics_inspect(worker_for(model), "Model", {"path": PHYSICS_PATH, "depth": 1})
        outer_row = shallow["children"][0]["children"][0]
        assert "children" not in outer_row
        deep = w15.physics_inspect(worker_for(model), "Model", {"path": PHYSICS_PATH, "depth": 2})
        outer_row = deep["children"][0]["children"][0]
        assert outer_row["children"][0]["tag"] == "inner"

    def test_properties_can_be_included(self):
        model = build_model()
        result = w15.physics_inspect(worker_for(model), "Model",
                                     {"path": PHYSICS_PATH, "properties": True})
        init = [row for row in result["children"][0]["children"] if row["tag"] == "init1"][0]
        assert init["properties"][0]["name"] == "Tinit"
        assert init["properties"][0]["value"] == "293.15[K]"

    def test_depth_out_of_range_is_rejected(self):
        model = build_model()
        expect_error("INVALID_REQUEST", w15.physics_inspect, worker_for(model), "Model",
                     {"path": PHYSICS_PATH, "depth": 9})

    def test_unknown_interface_type_is_reported_not_hidden(self):
        interface = physics_node(tag="ht", type_id="SomethingLocal")
        component = FComponent(materials={}, physics={"ht": interface})
        model = build_model(component=component)
        result = w15.physics_inspect(worker_for(model), "Model", {"path": PHYSICS_PATH})
        assert "not in this layer's local interface vocabulary" in result["type_vocabulary_note"]


class TestPhysicsRemove:
    def test_removes_an_interface_and_reports_coupling_dependents(self):
        coupling = FFeature(tag="te1", type_id="ThermalExpansion",
                            value_types={"solid": "String", "ht": "String"},
                            values={"solid": "solid", "ht": "ht"})
        model = build_model(couplings={"te1": coupling})
        result = w15.physics_remove(worker_for(model), "Model", {"path": PHYSICS_PATH})
        assert result["status"] == "APPLIED"
        assert result["dependent_references"] == [{"kind": "multiphysics", "tag": "te1", "property": "ht",
                                                  "value": "ht"}]
        assert model.components["comp1"].physics_list.items == {}

    def test_feature_path_is_refused_with_a_pointer_to_feature_remove(self):
        model = build_model()
        expect_error("INVALID_NODE_PATH", w15.physics_remove, worker_for(model), "Model",
                     {"path": feature_path(("component", "comp1"), ("physics", "ht"), ("feature", "solid1"))})

    def test_missing_interface_is_rejected(self):
        model = build_model()
        expect_error("NODE_NOT_FOUND", w15.physics_remove, worker_for(model), "Model",
                     {"path": feature_path(("component", "comp1"), ("physics", "nope"))})


# ---------------------------------------------------------------------------
# physics.feature_*
# ---------------------------------------------------------------------------


class TestPhysicsFeatureCreate:
    def test_creates_a_boundary_feature_with_a_dimension(self):
        model = build_model()
        result = w15.physics_feature_create(worker_for(model), "Model", {
            "parent": PHYSICS_PATH, "tag": "temp1", "type_id": "TemperatureBoundary", "entity_dimension": 2,
        })
        assert result["status"] == "APPLIED"
        assert result["applied"][0]["readback"]["tags"] == ["solid1", "init1", "temp1"]
        assert result["applied"][0]["token_source"].startswith("installed 6.4 completion data")

    def test_creates_a_feature_without_a_dimension_when_omitted(self):
        model = build_model()
        result = w15.physics_feature_create(worker_for(model), "Model", {
            "parent": PHYSICS_PATH, "tag": "temp1", "type_id": "TemperatureBoundary",
        })
        assert result["applied"][0]["entity_dimension"] is None

    def test_nested_child_feature_under_a_feature_parent(self):
        model = build_model()
        parent = feature_path(("component", "comp1"), ("physics", "ht"), ("feature", "solid1"))
        result = w15.physics_feature_create(worker_for(model), "Model", {
            "parent": parent, "tag": "sub1", "type_id": "HeatSource",
        })
        assert result["status"] == "APPLIED"
        assert result["path"]["segments"][-2:] == [{"collection": "feature", "tag": "solid1"},
                                                   {"collection": "feature", "tag": "sub1"}]

    def test_parent_without_a_feature_container_is_refused(self):
        model = build_model()
        parent = {"segments": [{"collection": "component", "tag": "comp1"}]}
        expect_error("API_UNSUPPORTED", w15.physics_feature_create, worker_for(model), "Model", {
            "parent": parent, "tag": "f1", "type_id": "HeatSource",
        })

    def test_existing_feature_tag_is_rejected(self):
        model = build_model()
        # The tag exists with a *different* type, so the informative refusal is a
        # TYPE_CONFLICT (the existence + type check the contract requires).
        expect_error("TYPE_CONFLICT", w15.physics_feature_create, worker_for(model), "Model", {
            "parent": PHYSICS_PATH, "tag": "solid1", "type_id": "HeatSource",
        })

    def test_properties_are_written_with_the_feature(self):
        model = build_model()
        result = w15.physics_feature_create(worker_for(model), "Model", {
            "parent": PHYSICS_PATH, "tag": "hs1", "type_id": "HeatSource", "entity_dimension": 3,
            "properties": {"Q0": "1e6[W/m^3]"},
        })
        assert result["failed"] == []
        assert result["applied"][1]["action"] == "set_properties"

    def test_property_failure_keeps_the_created_feature_visible(self):
        model = build_model()
        result = w15.physics_feature_create(worker_for(model), "Model", {
            "parent": PHYSICS_PATH, "tag": "hs1", "type_id": "HeatSource", "entity_dimension": 3,
            "properties": {"notaproperty": "1"},
        })
        assert result["status"] == "PARTIAL_FAILURE"
        assert result["applied"][0]["action"] == "create"
        assert result["failed"][0]["action"] == "set_properties"

    def test_entity_dimension_out_of_range_is_rejected(self):
        model = build_model()
        expect_error("INVALID_REQUEST", w15.physics_feature_create, worker_for(model), "Model", {
            "parent": PHYSICS_PATH, "tag": "temp1", "type_id": "TemperatureBoundary", "entity_dimension": 5,
        })


class TestPhysicsFeatureUpdate:
    def test_updates_properties_and_reads_back(self):
        model = build_model()
        path = feature_path(("component", "comp1"), ("physics", "ht"), ("feature", "init1"))
        result = w15.physics_feature_update(worker_for(model), "Model", {
            "path": path, "properties": {"Tinit": "300[K]"},
        })
        assert result["status"] == "APPLIED"
        assert result["applied"][0]["readback_values"]["Tinit"]["data"] == "300[K]"

    def test_unknown_property_is_rejected_before_the_write(self):
        model = build_model()
        path = feature_path(("component", "comp1"), ("physics", "ht"), ("feature", "init1"))
        expect_error("API_UNSUPPORTED", w15.physics_feature_update, worker_for(model), "Model", {
            "path": path, "properties": {"nope": "1"},
        })

    def test_engine_value_mismatch_is_execution_state_unknown(self):
        feature = FFeature(tag="init1", type_id="InitialValues",
                           value_types={"Tinit": "String"}, values={"Tinit": "293.15[K]"},
                           readback_overrides={"Tinit": "293.15[K]"})
        interface = physics_node(tag="ht", features={"init1": feature})
        component = FComponent(materials={}, physics={"ht": interface})
        model = build_model(component=component)
        path = feature_path(("component", "comp1"), ("physics", "ht"), ("feature", "init1"))
        result = w15.physics_feature_update(worker_for(model), "Model", {
            "path": path, "properties": {"Tinit": "300[K]"},
        })
        assert result["status"] == "EXECUTION_STATE_UNKNOWN"
        assert result["partial_change"] is True


class TestPhysicsFeatureRemove:
    def test_removes_a_child_feature(self):
        model = build_model()
        path = feature_path(("component", "comp1"), ("physics", "ht"), ("feature", "init1"))
        result = w15.physics_feature_remove(worker_for(model), "Model", {"path": path})
        assert result["status"] == "APPLIED"
        assert result["applied"][0]["readback"]["tags_after"] == ["solid1"]

    def test_interface_path_is_refused(self):
        model = build_model()
        expect_error("INVALID_NODE_PATH", w15.physics_feature_remove, worker_for(model), "Model",
                     {"path": PHYSICS_PATH})

    def test_protected_feature_removal_surfaces_the_engine_refusal(self):
        interface = FPhysics(tag="ht", features={})
        interface.feature_list.unavailable = {"remove"}
        component = FComponent(materials={}, physics={"ht": interface})
        model = build_model(component=component)
        interface.feature_list.items["solid1"] = FFeature(tag="solid1", type_id="HeatTransferInSolids")
        path = feature_path(("component", "comp1"), ("physics", "ht"), ("feature", "solid1"))
        error = expect_error("METHOD_REJECTED", w15.physics_feature_remove, worker_for(model), "Model",
                             {"path": path})
        assert "protected" in str(error)


# ---------------------------------------------------------------------------
# physics.selection_set (T007)
# ---------------------------------------------------------------------------


class TestPhysicsSelectionSet:
    def test_physics_level_and_feature_level_are_distinguished(self):
        model = build_model()
        interface = w15.physics_selection_set(worker_for(model), "Model", {
            "path": PHYSICS_PATH,
            "selection": {"kind": "explicit", "component": "comp1", "geometry": "geom1",
                          "entity_dimension": 3, "entities": [1, 2]},
        })
        assert interface["level"] == "auto"
        assert interface["path_collection"] == "physics"
        assert interface["applied"][0]["applied"][0]["method"] == "geom/set"
        assert interface["selection_readback"]["entities"] == [1, 2]

        feature = w15.physics_selection_set(worker_for(model), "Model", {
            "path": feature_path(("component", "comp1"), ("physics", "ht"), ("feature", "solid1")),
            "selection": {"kind": "explicit", "component": "comp1", "geometry": "geom1",
                          "entity_dimension": 3, "entities": [2]},
        })
        assert feature["path_collection"] == "feature"
        assert feature["selection_readback"]["entities"] == [2]

    def test_named_selection_binding_on_a_feature(self):
        selection = FFeature(tag="sel1", type_id="Explicit", entities=[1], dim=3)
        component = FComponent(materials={}, physics={"ht": physics_node()},
                               selections={"sel1": selection})
        model = build_model(component=component)
        result = w15.physics_selection_set(worker_for(model), "Model", {
            "path": feature_path(("component", "comp1"), ("physics", "ht"), ("feature", "solid1")),
            "selection": {"kind": "named", "component": "comp1", "tag": "sel1"},
        })
        assert result["status"] == "APPLIED"
        assert result["selection_readback"]["named"] == "sel1"

    def test_level_interface_requires_a_physics_path(self):
        model = build_model()
        expect_error("INVALID_NODE_PATH", w15.physics_selection_set, worker_for(model), "Model", {
            "path": feature_path(("component", "comp1"), ("physics", "ht"), ("feature", "solid1")),
            "selection": {"kind": "all", "component": "comp1", "geometry": "geom1", "entity_dimension": 3},
            "level": "interface",
        })

    def test_level_feature_requires_a_feature_path(self):
        model = build_model()
        expect_error("INVALID_NODE_PATH", w15.physics_selection_set, worker_for(model), "Model", {
            "path": PHYSICS_PATH,
            "selection": {"kind": "all", "component": "comp1", "geometry": "geom1", "entity_dimension": 3},
            "level": "feature",
        })

    def test_invalid_level_is_rejected(self):
        model = build_model()
        expect_error("INVALID_REQUEST", w15.physics_selection_set, worker_for(model), "Model", {
            "path": PHYSICS_PATH, "selection": {"kind": "all", "component": "comp1", "geometry": "geom1",
                                                "entity_dimension": 3}, "level": "side",
        })

    def test_inherited_selection_is_recorded_as_requested_with_readback(self):
        model = build_model()
        result = w15.physics_selection_set(worker_for(model), "Model", {
            "path": PHYSICS_PATH, "selection": {"kind": "inherited", "component": "comp1"},
        })
        assert result["applied"][0]["status"] == "INHERIT_REQUESTED"
        assert result["applied"][0]["applied"][0]["readback"] is True

    def test_an_engine_refusal_to_write_an_inherited_selection_is_kept_as_failure(self):
        interface = physics_node(tag="ht")
        interface.inheriting_ = True
        interface.unavailable = {"inherit"}
        component = FComponent(materials={}, physics={"ht": interface})
        model = build_model(component=component)
        result = w15.physics_selection_set(worker_for(model), "Model", {
            "path": PHYSICS_PATH,
            "selection": {"kind": "explicit", "component": "comp1", "geometry": "geom1",
                          "entity_dimension": 3, "entities": [1]},
        })
        # The fake accepts entity writes even while inheriting; the truthfulness
        # contract is checked on the inherit path, where the engine refusal must
        # surface as a failed entry rather than a silent success.
        assert result["status"] == "APPLIED"

        inherit_result = w15.physics_selection_set(worker_for(model), "Model", {
            "path": PHYSICS_PATH, "selection": {"kind": "inherited", "component": "comp1"},
        })
        assert inherit_result["status"] == "FAILED"
        assert "inherit" in inherit_result["inheritance_note"]

    def test_unavailable_selection_accessor_is_refused(self):
        interface = physics_node(tag="ht")
        interface.unavailable = {"selection"}
        component = FComponent(materials={}, physics={"ht": interface})
        model = build_model(component=component)
        expect_error("API_UNSUPPORTED", w15.physics_selection_set, worker_for(model), "Model", {
            "path": PHYSICS_PATH,
            "selection": {"kind": "explicit", "component": "comp1", "geometry": "geom1",
                          "entity_dimension": 3, "entities": [1]},
        })


# ---------------------------------------------------------------------------
# physics.multiphysics_manage
# ---------------------------------------------------------------------------


class TestMultiphysicsManage:
    def test_creates_a_documented_coupling_with_a_space_dimension(self):
        model = build_model()
        result = w15.physics_multiphysics_manage(worker_for(model), "Model", {
            "action": "create",
            "definition": {"tag": "te1", "type_id": "ThermalExpansion", "geometry": "geom1",
                           "space_dimension": 3},
        })
        assert result["status"] == "APPLIED"
        assert result["applied"][0]["readback"]["type"] == "ThermalExpansion"
        assert model.multiphysics_list.items["te1"].tag_ == "te1"

    def test_component_scoped_coupling_creation(self):
        model = build_model()
        result = w15.physics_multiphysics_manage(worker_for(model), "Model", {
            "action": "create", "component": "comp1",
            "definition": {"tag": "te1", "type_id": "ThermalExpansion", "geometry": "geom1"},
        })
        assert result["status"] == "APPLIED"
        assert "te1" in model.components["comp1"].coupling_list.items

    def test_unverified_coupling_type_is_rejected_before_the_write(self):
        model = build_model()
        error = expect_error("API_UNSUPPORTED", w15.physics_multiphysics_manage, worker_for(model), "Model", {
            "action": "create",
            "definition": {"tag": "x1", "type_id": "StaticCurrentHeating", "geometry": "geom1"},
        })
        assert "verified COMSOL 6.4 vocabulary" in str(error)
        assert model.multiphysics_list.items == {}

    def test_missing_required_definition_fields_are_rejected(self):
        model = build_model()
        expect_error("INVALID_REQUEST", w15.physics_multiphysics_manage, worker_for(model), "Model",
                     {"action": "create", "definition": {"type_id": "ThermalExpansion"}})

    def test_existing_coupling_tag_is_rejected(self):
        model = build_model(couplings={"te1": FFeature(tag="te1", type_id="ThermalExpansion")})
        expect_error("TAG_CONFLICT", w15.physics_multiphysics_manage, worker_for(model), "Model", {
            "action": "create",
            "definition": {"tag": "te1", "type_id": "ThermalExpansion", "geometry": "geom1"},
        })

    def test_inspect_lists_typed_properties(self):
        coupling = FFeature(tag="te1", type_id="ThermalExpansion",
                            value_types={"Tref": "String", "solid": "String"},
                            values={"Tref": "293.15[K]", "solid": "solid"})
        model = build_model(couplings={"te1": coupling})
        result = w15.physics_multiphysics_manage(worker_for(model), "Model", {
            "action": "inspect",
            "path": {"segments": [{"collection": "multiphysics", "tag": "te1"}]},
        })
        assert result["status"] == "APPLIED"
        row = result["applied"][0]
        assert row["type_id"] == "ThermalExpansion"
        assert {item["name"]: item["value"] for item in row["properties"]}["Tref"] == "293.15[K]"

    def test_update_writes_and_reads_back(self):
        coupling = FFeature(tag="te1", type_id="ThermalExpansion",
                            value_types={"Tref": "String"}, values={"Tref": "293.15[K]"})
        model = build_model(couplings={"te1": coupling})
        result = w15.physics_multiphysics_manage(worker_for(model), "Model", {
            "action": "update",
            "path": {"segments": [{"collection": "multiphysics", "tag": "te1"}]},
            "definition": {"properties": {"Tref": "300[K]"}},
        })
        assert result["status"] == "APPLIED"
        assert result["applied"][0]["readback_values"]["Tref"]["data"] == "300[K]"

    def test_remove_reads_back_the_tag_list(self):
        model = build_model(couplings={"te1": FFeature(tag="te1", type_id="ThermalExpansion")})
        result = w15.physics_multiphysics_manage(worker_for(model), "Model", {
            "action": "remove",
            "path": {"segments": [{"collection": "multiphysics", "tag": "te1"}]},
        })
        assert result["applied"][0]["readback"]["tags_after"] == []
        assert model.multiphysics_list.items == {}

    def test_unsupported_action_is_rejected(self):
        model = build_model()
        expect_error("INVALID_REQUEST", w15.physics_multiphysics_manage, worker_for(model), "Model",
                     {"action": "reorder"})

    def test_engine_failure_is_reported_as_a_failed_entry(self):
        model = build_model()
        result = w15.physics_multiphysics_manage(worker_for(model), "Model", {
            "action": "create",
            "definition": {"tag": "badcpl", "type_id": "ThermalExpansion", "geometry": "geom1"},
        })
        assert result["status"] == "FAILED"
        assert result["failed"][0]["error"]["code"] == "ENGINE_CALL_FAILED"


# ---------------------------------------------------------------------------
# physics.initial_values_set
# ---------------------------------------------------------------------------


class TestPhysicsInitialValuesSet:
    def test_writes_the_existing_initial_values_feature(self):
        model = build_model()
        result = w15.physics_initial_values_set(worker_for(model), "Model", {
            "path": PHYSICS_PATH, "definition": {"properties": {"Tinit": "300[K]"}},
        })
        assert result["status"] == "APPLIED"
        assert result["applied"][0]["tag"] == "init1"
        assert result["applied"][0]["readback_values"]["Tinit"]["data"] == "300[K]"

    def test_creates_the_feature_when_absent_and_allowed(self):
        interface = physics_node(tag="ht", features={
            "solid1": FFeature(tag="solid1", type_id="HeatTransferInSolids"),
        })
        component = FComponent(materials={}, physics={"ht": interface})
        model = build_model(component=component)
        result = w15.physics_initial_values_set(worker_for(model), "Model", {
            "path": PHYSICS_PATH, "definition": {"properties": {}}, "create_missing": True,
        })
        assert result["applied"][0]["action"] == "create_feature"
        assert result["applied"][0]["tag"] == "init1"

    def test_missing_feature_without_create_missing_is_a_node_not_found(self):
        interface = physics_node(tag="ht", features={
            "solid1": FFeature(tag="solid1", type_id="HeatTransferInSolids"),
        })
        component = FComponent(materials={}, physics={"ht": interface})
        model = build_model(component=component)
        result = w15.physics_initial_values_set(worker_for(model), "Model", {
            "path": PHYSICS_PATH, "definition": {"properties": {}}, "create_missing": False,
        })
        assert result["status"] == "FAILED"
        assert result["failed"][0]["error"]["code"] == "NODE_NOT_FOUND"

    def test_explicit_feature_path_is_written(self):
        model = build_model()
        result = w15.physics_initial_values_set(worker_for(model), "Model", {
            "path": feature_path(("component", "comp1"), ("physics", "ht"), ("feature", "init1")),
            "definition": {"properties": {"Tinit": "310[K]"}},
        })
        assert result["status"] == "APPLIED"
        assert result["applied"][0]["readback_values"]["Tinit"]["data"] == "310[K]"

    def test_type_conflict_on_a_named_feature_is_rejected(self):
        model = build_model()
        result = w15.physics_initial_values_set(worker_for(model), "Model", {
            "path": PHYSICS_PATH, "definition": {"properties": {}, "tag": "solid1"},
        })
        assert result["status"] == "FAILED"
        assert result["failed"][0]["error"]["code"] == "TYPE_CONFLICT"

    def test_definition_properties_are_required(self):
        model = build_model()
        expect_error("INVALID_REQUEST", w15.physics_initial_values_set, worker_for(model), "Model",
                     {"path": PHYSICS_PATH, "definition": {}})


# ---------------------------------------------------------------------------
# physics.validate
# ---------------------------------------------------------------------------


class TestPhysicsValidate:
    def test_healthy_interface_reports_no_violation(self):
        model = build_model()
        result = w15.physics_validate(worker_for(model), "Model", {"component": "comp1"})
        assert result["verdict"] == "NO_VIOLATION_DETECTED"
        assert result["violations"] == []
        assert result["interfaces"][0]["feature_count"] == 2
        assert "physics_feature_lifecycle" in result["checked_rules"]

    def test_interface_without_features_is_a_violation(self):
        interface = physics_node(tag="ht", features={})
        component = FComponent(materials={}, physics={"ht": interface})
        model = build_model(component=component)
        result = w15.physics_validate(worker_for(model), "Model", {"component": "comp1"})
        rules = {row["rule"] for row in result["violations"]}
        assert "physics_feature_lifecycle" in rules

    def test_feature_pointing_at_another_geometry_is_a_violation(self):
        feature = FFeature(tag="temp1", type_id="TemperatureBoundary", geometry="geom2", entities=[3], dim=2)
        interface = physics_node(tag="ht", features={"solid1": FFeature(
            tag="solid1", type_id="HeatTransferInSolids", geometry="geom1", entities=[1], dim=3), "temp1": feature})
        component = FComponent(materials={}, physics={"ht": interface})
        model = build_model(component=component)
        result = w15.physics_validate(worker_for(model), "Model", {"component": "comp1"})
        rules = {row["rule"] for row in result["violations"]}
        assert "feature_geometry_mismatch" in rules

    def test_empty_feature_selection_is_unknown_not_a_violation(self):
        feature = FFeature(tag="temp1", type_id="TemperatureBoundary", geometry="geom1", entities=[], dim=2)
        interface = physics_node(tag="ht", features={
            "solid1": FFeature(tag="solid1", type_id="HeatTransferInSolids", geometry="geom1",
                               entities=[1], dim=3), "temp1": feature})
        component = FComponent(materials={}, physics={"ht": interface})
        model = build_model(component=component)
        result = w15.physics_validate(worker_for(model), "Model", {"component": "comp1"})
        assert any(row["rule"] == "feature_selection_non_empty" for row in result["unknown"])
        assert result["verdict"] == "UNKNOWN"

    def test_scope_path_restricts_to_one_interface(self):
        model = build_model()
        result = w15.physics_validate(worker_for(model), "Model", {"scope": PHYSICS_PATH})
        assert [row["tag"] for row in result["interfaces"]] == ["ht"]

    def test_scope_must_be_an_interface_not_a_feature(self):
        model = build_model()
        expect_error("INVALID_NODE_PATH", w15.physics_validate, worker_for(model), "Model",
                     {"scope": feature_path(("component", "comp1"), ("physics", "ht"), ("feature", "solid1"))})

    def test_unknown_interface_type_is_reported_as_unknown(self):
        interface = physics_node(tag="ht", type_id="CustomInterface")
        component = FComponent(materials={}, physics={"ht": interface})
        model = build_model(component=component)
        result = w15.physics_validate(worker_for(model), "Model", {"component": "comp1"})
        assert any(row["rule"] == "physics_type_vocabulary" for row in result["unknown"])


# ---------------------------------------------------------------------------
# cross-cutting invariants
# ---------------------------------------------------------------------------


class TestCrossCutting:
    def test_every_operation_rejects_unknown_arguments(self):
        model = build_model()
        worker = worker_for(model)
        arguments = {
            "material.list": {"component": "comp1"},
            "material.create": {"component": "comp1", "tag": "m9", "type_id": "Common"},
            "material.inspect": {"path": MATERIAL_PATH},
            "material.set_properties": {"path": MATERIAL_PATH, "group": "def",
                                        "properties": {"density": "1[kg/m^3]"}},
            "material.group_manage": {"path": MATERIAL_PATH, "action": "inspect", "group": "def"},
            "material.selection_set": {"path": MATERIAL_PATH,
                                       "selection": {"kind": "inherited", "component": "comp1"}},
            "physics.list": {"component": "comp1"},
            "physics.create": {"component": "comp1", "tag": "s1", "type_id": "SolidMechanics",
                               "geometry": "geom1"},
            "physics.inspect": {"path": PHYSICS_PATH},
            "physics.feature_create": {"parent": PHYSICS_PATH, "tag": "g1", "type_id": "HeatSource"},
            "physics.feature_update": {"path": feature_path(("component", "comp1"), ("physics", "ht"),
                                                           ("feature", "init1")),
                                       "properties": {"Tinit": "1[K]"}},
            "physics.feature_remove": {"path": feature_path(("component", "comp1"), ("physics", "ht"),
                                                            ("feature", "init1"))},
            "physics.selection_set": {"path": PHYSICS_PATH,
                                      "selection": {"kind": "inherited", "component": "comp1"}},
            "physics.multiphysics_manage": {"action": "inspect",
                                            "path": {"segments": [{"collection": "multiphysics",
                                                                   "tag": "te1"}]}},
            "physics.initial_values_set": {"path": PHYSICS_PATH, "definition": {"properties": {"Tinit": "1[K]"}}},
            "physics.validate": {"component": "comp1"},
        }
        for operation_id, payload in arguments.items():
            bad = dict(payload)
            bad["zzz_unknown"] = 1
            expect_error("INVALID_REQUEST", w15.OPERATIONS[operation_id], worker, "Model", bad)

    def test_envelope_fields_pass_through(self):
        model = build_model()
        result = w15.material_list(worker_for(model), "Model", {
            "component": "comp1", "project_id": "p1", "session_id": "s1", "model_ref": "r1",
            "expected_revision": 3, "idempotency_key": "k1", "request_id": "req1",
        })
        assert result["material_count"] == 1

    def test_operations_never_wrap_their_own_success_envelope(self):
        model = build_model()
        for operation_id, payload in {
            "material.list": {"component": "comp1"},
            "physics.list": {"component": "comp1"},
            "material.validate": {"component": "comp1"},
            "physics.validate": {"component": "comp1"},
        }.items():
            data = w15.OPERATIONS[operation_id](worker_for(model), "Model", payload)
            assert "success" not in data
            assert "ok" not in data or isinstance(data.get("ok"), bool)


# ---------------------------------------------------------------------------
# material.set_properties -- tensor storage adapter (six values are not read
# back; the ordered nine-tuple is; and a PBE-style periodic report is not a
# reason to skip the solver's convergence contract)
# ---------------------------------------------------------------------------


def rank1_def_group(**kwargs: Any) -> FPropertyGroup:
    """A ``def`` group whose thermal conductivity is a rank-1 ``StringArray``.

    The bound 6.4.0.293 build on this Mac publishes exactly that storage shape
    (driver5c chain A: ``getValueType(thermalconductivity)`` answers a rank-1
    array while the documented semantic shape is a 3x3 ``StringMatrix``), so the
    adapter is exercised against the recorded live shape rather than only
    against the documented one.
    """
    base = def_group(value_types={"thermalconductivity": "StringArray"},
                     arrays={"thermalconductivity": ["44.5[W/(m*K)]"]})
    return base


class TestMaterialTensorAdapter:
    ANISOTROPIC: list[list[str]] = [
        ["1[W/(m*K)]", "0.5[W/(m*K)]", "0"],
        ["0.5[W/(m*K)]", "2[W/(m*K)]", "0"],
        ["0", "0", "3[W/(m*K)]"],
    ]

    def _write(self, group: FPropertyGroup, value: Any, *, name: str = "thermalconductivity") -> dict[str, Any]:
        component = FComponent(materials={"mat1": material_node(groups={"def": group})}, physics={})
        model = build_model(component=component)
        self.model = model
        self.group = group
        return w15.material_set_properties(worker_for(model), "Model", {
            "path": MATERIAL_PATH, "group": "def", "properties": {name: value},
        })

    @staticmethod
    def _set_data(group: FPropertyGroup, name: str = "thermalconductivity") -> Any:
        """The *data* the worker hands the setter (the wire envelope is unwrapped)."""
        sets = [args for method, args in group.calls if method == "set" and args and args[0] == name]
        assert len(sets) == 1, group.calls
        return unwrap_typed(sets[0][1])

    def test_rank1_isotropic_tensor_is_stored_in_the_documented_canonical_form(self):
        """A nine-entry isotropic tensor is stored as the documented one-entry form.

        The pre-adapter code sent nine entries into a rank-1 property, which the
        recorded live build refuses ("property expects array rank 1, received
        2"), or -- when it is accepted -- reads back collapsed to one entry.
        The adapter emits the canonical form documented by the Application
        Programming Guide instead of relying on that collapse.
        """
        group = rank1_def_group()
        result = self._write(group, [["10[W/(m*K)]", "0", "0"],
                                     ["0", "10[W/(m*K)]", "0"],
                                     ["0", "0", "10[W/(m*K)]"]])
        assert result["status"] == "APPLIED", result["failed"]
        assert self._set_data(group) == ["10[W/(m*K)]"]
        record = result["tensor_adapter"][0]
        assert record["storage_form"] == "isotropic_vector"
        assert record["storage_shape"] == [1]
        assert record["semantic_shape"] == [3, 3]
        assert record["symmetric"] is True
        assert record["positive_definite"] is True
        assert record["readback_check"]["equivalent"] is True
        assert "Programming Reference 6.4 p.152" in record["sources"]["column_wise_readback"]
        assert "Model XML-File Format" in record["sources"]["tensor_vector_convention"]
        assert "a9c03166" in record["sources"]["tensor_vector_convention"]

    def test_rank1_anisotropic_off_diagonals_survive_in_row_major_order(self):
        """A non-zero off-diagonal tensor keeps every entry, in the written order.

        The evidence for the index arrangement is the emitted list itself plus
        the tensor-level readback check: the adapter must not transpose k12/k21
        while converting, and it must not drop the off-diagonals.
        """
        group = rank1_def_group()
        result = self._write(group, self.ANISOTROPIC)
        assert result["status"] == "APPLIED", result["failed"]
        assert self._set_data(group) == [entry for row in self.ANISOTROPIC for entry in row]
        record = result["tensor_adapter"][0]
        assert record["storage_form"] == "full_vector"
        assert record["storage_shape"] == [9]
        assert record["off_diagonals_present"] is True
        assert record["semantic_data"] == self.ANISOTROPIC
        readback = record["readback_check"]
        assert readback["equivalent"] is True
        assert readback["returned_form"] == "full_vector"
        assert readback["off_diagonals_preserved"] is True
        # A column-wise flat readback of the same symmetric tensor is the same
        # nine-tuple, which is exactly why the adapter may use the flat form.
        assert readback["returned_tensor"] == self.ANISOTROPIC

    def test_rank1_diagonal_tensor_is_stored_as_the_documented_three_entry_form(self):
        group = rank1_def_group()
        result = self._write(group, [["0.2[W/(m*K)]", "0", "0"],
                                     ["0", "44.5[W/(m*K)]", "0"],
                                     ["0", "0", "44.5[W/(m*K)]"]])
        assert result["status"] == "APPLIED", result["failed"]
        assert self._set_data(group) == ["0.2[W/(m*K)]", "44.5[W/(m*K)]", "44.5[W/(m*K)]"]
        assert result["tensor_adapter"][0]["storage_form"] == "diagonal_vector"

    def test_rank1_refuses_a_non_symmetric_tensor_before_any_write(self):
        """A non-symmetric tensor is refused instead of being written in a guessed order.

        The flat readback order is documented as column-wise; writing a
        non-symmetric tensor as a flat vector therefore cannot be verified
        offline, and a silent transpose would be worse than a refusal.
        """
        group = rank1_def_group()
        asymmetric = [["1[W/(m*K)]", "2[W/(m*K)]", "0"],
                      ["0.1[W/(m*K)]", "2[W/(m*K)]", "0"],
                      ["0", "0", "3[W/(m*K)]"]]
        error = expect_error("PROPERTY_TENSOR_NOT_SYMMETRIC", w15.material_set_properties,
                             worker_for(build_model(component=FComponent(
                                 materials={"mat1": material_node(groups={"def": group})}, physics={}))),
                             "Model", {"path": MATERIAL_PATH, "group": "def",
                                       "properties": {"thermalconductivity": asymmetric}})
        assert "column-wise" in str(error)
        assert [args for method, args in group.calls if method == "set"] == []

    def test_rank1_refuses_the_six_entry_compact_form(self):
        """Length six is documented as symmetric but its order is not documented here."""
        group = rank1_def_group()
        error = expect_error("PROPERTY_TENSOR_ORDER_UNVERIFIED", w15.material_set_properties,
                             worker_for(build_model(component=FComponent(
                                 materials={"mat1": material_node(groups={"def": group})}, physics={}))),
                             "Model", {"path": MATERIAL_PATH, "group": "def",
                                       "properties": {"thermalconductivity": ["1", "0", "0", "2", "0", "3"]}})
        assert "nine entries in row-major order" in str(error)
        assert [args for method, args in group.calls if method == "set"] == []

    def test_rank2_storage_keeps_the_semantic_matrix(self):
        """The documented ``StringMatrix`` storage still receives the full matrix."""
        group = def_group(value_types={"thermalconductivity": "StringMatrix"},
                          matrices={"thermalconductivity": self.ANISOTROPIC})
        result = self._write(group, self.ANISOTROPIC)
        assert result["status"] == "APPLIED", result["failed"]
        assert self._set_data(group) == self.ANISOTROPIC
        record = result["tensor_adapter"][0]
        assert record["storage_form"] == "matrix"
        assert record["storage_shape"] == [3, 3]
        assert record["storage_rank"] == 2
        assert record["engine_rank_source"] == "getValueType metadata"

    def test_a_lost_readback_is_reported_as_a_tensor_mismatch(self):
        """A readback that cannot carry the requested tensor is never accepted.

        The tensor check is evidence on top of the frozen G2 text readback: it
        names *what* was lost (here the diagonal) instead of only "text differs".
        """
        group = rank1_def_group()
        group.readback_overrides["thermalconductivity"] = ["1[W/(m*K)]"]
        result = self._write(group, [["1[W/(m*K)]", "0", "0"],
                                     ["0", "2[W/(m*K)]", "0"],
                                     ["0", "0", "3[W/(m*K)]"]])
        assert result["status"] == "EXECUTION_STATE_UNKNOWN"
        record = result["tensor_adapter"][0]
        assert record["readback_check"]["equivalent"] is False
        assert record["readback_check"]["returned_form"] == "isotropic_vector"

    def test_constraints_are_declared_without_inventing_a_physics_gate(self):
        """Declared constraints are reported; the product layer adds no gate of its own.

        §7 of the goal requires the constraints to be *declared* per the physics
        model in use, and the adapter declares symmetry and positive
        definiteness with the evaluated evidence.  A symmetric but
        non-positive-definite tensor is still dispatched -- refusing it would be
        a physics judgement this layer has no citation for -- and the record
        says so.
        """
        group = rank1_def_group()
        result = self._write(group, [["-1[W/(m*K)]", "0", "0"],
                                     ["0", "1[W/(m*K)]", "0"],
                                     ["0", "0", "1[W/(m*K)]"]])
        assert result["status"] == "APPLIED", result["failed"]
        record = result["tensor_adapter"][0]
        assert record["declared_constraints"] == ["symmetric", "positive_definite"]
        assert record["positive_definite"] is False
        assert record["symmetric"] is True
        assert record["definiteness_check"] == "Sylvester leading principal minors"
        assert [args for method, args in group.calls if method == "set"]

    def test_an_expression_typed_value_is_accepted_and_converted(self):
        """The typed-value alias ``expression`` reaches the adapter's storage form.

        ``validate_typed_value`` accepts ``expression`` where the engine
        publishes ``string``; the adapter must keep that tolerance or a
        previously valid caller payload would start failing.
        """
        group = rank1_def_group()
        result = self._write(group, {"kind": "expression", "shape": [3, 3], "data": self.ANISOTROPIC})
        assert result["status"] == "APPLIED", result["failed"]
        assert self._set_data(group) == [entry for row in self.ANISOTROPIC for entry in row]
        record = result["tensor_adapter"][0]
        assert record["input_shape_declared"] == [3, 3]
        assert record["input_form"] == "matrix"

    def test_material_create_definition_path_reports_the_adapter_record(self):
        group = rank1_def_group()
        component = FComponent(
            materials={"mat1": material_node()}, physics={},
            material_factory=lambda tag, type_id: material_node(tag=tag, groups={"def": group}),
        )
        model = build_model(component=component)
        result = w15.material_create(worker_for(model), "Model", {
            "component": "comp1", "tag": "mat2", "type_id": "Common",
            "definition": {"group": "def", "properties": {"thermalconductivity": self.ANISOTROPIC}},
        })
        assert result["status"] == "APPLIED", result["failed"]
        applied = [row for row in result["applied"] if row["action"] == "set_properties"][0]
        assert applied["tensor_adapter"][0]["storage_form"] == "full_vector"
        assert self._set_data(group) == [entry for row in self.ANISOTROPIC for entry in row]


# ---------------------------------------------------------------------------
# Default-feature inventory (C06: ins1 / documented defaults are never guessed)
# ---------------------------------------------------------------------------


class TestDefaultFeatureInventory:
    """``physics.inspect`` inventories documented defaults read-only.

    LiveLink for MATLAB User Guide 6.4 p.124 documents the default features of
    the Heat Transfer in Solids interface as solid1, init1, ins1, idi1, os1 and
    cib1.  The product has to *report* which of them the node actually carries
    instead of assuming ``ins1`` is there (or writable), and a caller that needs
    a missing boundary condition has to create it rather than skip it.
    """

    DOCUMENTED = ("solid1", "init1", "ins1", "idi1", "os1", "cib1")

    def _inspect(self, features: Mapping[str, FFeature]) -> dict[str, Any]:
        interface = physics_node(tag="ht", type_id="HeatTransferInSolids", features=dict(features))
        component = FComponent(materials={}, physics={"ht": interface})
        model = build_model(component=component)
        return w15.physics_inspect(worker_for(model), "Model", {"path": PHYSICS_PATH})

    def test_documented_defaults_are_classified_from_the_readback(self):
        features = {tag: FFeature(tag=tag, type_id="Solid" if tag == "solid1" else "Default")
                    for tag in self.DOCUMENTED}
        result = self._inspect(features)
        inventory = result["default_feature_inventory"]
        assert inventory["documented"] is True
        assert inventory["observed_documented_defaults"] == list(self.DOCUMENTED)
        assert inventory["missing_documented_defaults"] == []
        assert inventory["observed_types"]["solid1"] == "Solid"
        assert "LiveLink for MATLAB User Guide 6.4 p.124" in inventory["source"]
        solid = [row for row in inventory["documented_defaults"] if row["tag"] == "solid1"][0]
        assert solid["reuse"] == "addressed_by_observed_tag"

    def test_a_missing_ins1_is_reported_and_never_assumed(self):
        result = self._inspect({"solid1": FFeature(tag="solid1", type_id="Solid")})
        inventory = result["default_feature_inventory"]
        assert inventory["missing_documented_defaults"] == ["init1", "ins1", "idi1", "os1", "cib1"]
        absent = [row for row in inventory["documented_defaults"] if row["tag"] == "ins1"][0]
        assert absent["observed"] is False
        assert absent["reuse"] == "absent_create_explicitly_if_needed"
        assert "never lets a missing boundary condition be skipped silently" in inventory["reuse_policy"]
        # The interface was only read: no feature was created while inspecting.
        interface_features = result["children"]
        assert interface_features

    def test_solid1_role_is_verified_against_the_documented_type(self):
        result = self._inspect({"solid1": FFeature(tag="solid1", type_id="HeatFluxBoundary")})
        row = [row for row in result["default_feature_inventory"]["observed_features"]
               if row["tag"] == "solid1"][0]
        assert row["expected_type_id"] == "Solid"
        assert row["type_matches_documented_role"] is False
        assert row["classification"] == "documented_default"

    def test_an_undocumented_feature_is_not_called_a_default(self):
        result = self._inspect({"solid1": FFeature(tag="solid1", type_id="Solid"),
                                "hf1": FFeature(tag="hf1", type_id="HeatFluxBoundary")})
        inventory = result["default_feature_inventory"]
        row = [row for row in inventory["observed_features"] if row["tag"] == "hf1"][0]
        assert row["classification"] == "undocumented"
        assert "hf1" not in inventory["observed_documented_defaults"]

    def test_an_unknown_interface_type_reports_no_documented_defaults(self):
        interface = physics_node(tag="ht", type_id="SomeUnknownPhysics", features={})
        component = FComponent(materials={}, physics={"ht": interface})
        model = build_model(component=component)
        result = w15.physics_inspect(worker_for(model), "Model", {"path": PHYSICS_PATH})
        inventory = result["default_feature_inventory"]
        assert inventory["documented"] is False
        assert inventory["documented_defaults"] == []
        assert inventory["missing_documented_defaults"] == []
        assert inventory["reuse_policy"]

    def test_the_inventory_does_not_write(self):
        features = {tag: FFeature(tag=tag, type_id="Default") for tag in self.DOCUMENTED}
        interface = physics_node(tag="ht", type_id="HeatTransferInSolids", features=features)
        component = FComponent(materials={}, physics={"ht": interface})
        model = build_model(component=component)
        before = set(features)
        w15.physics_inspect(worker_for(model), "Model", {"path": PHYSICS_PATH})
        for tag, node in features.items():
            writes = [call for call in node.calls if call[0] in {"set", "setIndex"}]
            assert writes == [], (tag, writes)
        assert set(features) == before
        assert [call for call in interface.feature_list.calls if call[0] == "create"] == []
