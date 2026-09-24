"""W16: mesh / study / solver domain operations.

Every test drives the real operation code against a fake COMSOL node tree.  The
fake mirrors the *verified* API surface used by the implementation: a
``ModelEntityList`` container (``tags/get/create/remove/hasTag``), a
``PropFeature`` property surface (``properties/getValueType/
getAllowedPropertyValues`` plus the typed getters/setters), the documented mesh
accessors (``run/stat/clearMesh/automatic/isAutomatic/getNumElem/getTypes/
getMinQuality/isEmpty/isComplete/problems``), the study accessors
(``createAutoSequences/run/getLastComputationTime/.../setSolveFor/solveFor``)
and the solver accessors (``runAll/run/runFromTo/isEmpty/isInitialized/
isAttached/getSequenceType/getPVals/problem``).

Two failure modes are exercised separately:

* a method the fake does not implement raises ``AttributeError`` and surfaces as
  ``API_UNSUPPORTED`` (the API does not exist on this node), and
* a method listed in ``unavailable`` raises the worker allow-list refusal
  (``METHOD_REJECTED``) and must be reported as
  ``allowlist_entry_required`` instead of being converted into a silent empty
  read.
"""
from __future__ import annotations

import json
import copy
from typing import Any, Mapping, Sequence

import pytest

from comsol_mcp._execution_contract import ExecutionContractError

from comsol_mcp import _g3_w16 as w16
from comsol_mcp._g3_ops import DISPATCH, EFFECTS, IMPLEMENTED_OPERATIONS, REQUIRES_ISOLATION

#: Fields owned by the control plane / ActionResult envelope; the domain layer never
#: accepts or returns them (they are stripped before the operation callable runs).
#: The exact operation ids assigned to W16 (the mesh/study/solver slice named in
#: the W16 scope); every one of them must exist in the action catalog with a
#: matching argument surface.
_W16_SCOPE = frozenset({
    "mesh.list", "mesh.create", "mesh.inspect", "mesh.feature_create", "mesh.feature_update",
    "mesh.feature_remove", "mesh.build", "mesh.clear", "mesh.statistics", "mesh.quality",
    "mesh.validate",
    "study.list", "study.create", "study.inspect", "study.remove", "study.step_create",
    "study.step_update", "study.step_remove", "study.physics_activation", "study.solver_generate",
    "study.run",
    "solver.list", "solver.inspect", "solver.create", "solver.feature_create",
    "solver.feature_update", "solver.feature_remove", "solver.run",
})

_ENVELOPE_KEYS = frozenset({
    "success", "operation", "error", "execution_state_unknown", "partial_change",
    "not_executed", "message", "stdout", "stderr", "artifacts",
    "model_ref", "project_id", "session_id", "request_id", "idempotency_key",
    "expected_revision",
})

# ---------------------------------------------------------------------------
# fake engine
# ---------------------------------------------------------------------------

_ENGINE_KINDS = {
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

#: Every method the W16 implementation is allowed to reach through the worker
#: allow-list.  Anything else raises AttributeError in the fake, i.e. it is
#: treated exactly like an API that does not exist on that node.
_KNOWN_METHODS = frozenset({
    # identity / metadata
    "tag", "label", "name", "type", "getType", "active", "isActive", "properties",
    "hasProperty", "getValueType", "getAllowedPropertyValues", "author", "version",
    "uniquetag", "move", "copy", "duplicate", "clear",
    # typed property getters / setters
    "getString", "getStringArray", "getStringMatrix", "getBoolean", "getBooleanArray",
    "getBooleanMatrix", "getInt", "getIntArray", "getIntMatrix", "getDouble",
    "getDoubleArray", "getDoubleMatrix", "set", "setEntry", "getEntryKeys", "removeEntry",
    # list containers
    "tags", "get", "create", "remove", "hasTag", "size", "index", "getContainer",
    # mesh surface
    "run", "stat", "clearMesh", "clearMeshes", "automatic", "isAutomatic", "getSDim",
    "getNumElem", "getNumVertex", "getTypes", "getGeomEntities", "hasSecondOrderElements",
    "getMaxDimension", "getVolume", "getMaxVolume", "getMinVolume", "getMaxGrowthRate",
    "getMeanGrowthRate", "getMinQuality", "getMeanQuality", "getQualityDistr",
    "getQualityMeasure", "setQualityMeasure", "isEmpty", "isComplete", "current",
    "buildTime", "problems", "hasProblems", "status", "message", "lengthUnit",
    "getNumVertex", "moveMesh", "importData", "importVertexData",
    # study surface
    "createAutoSequences", "getSolverSequences", "attach", "detach", "runNoGen",
    "getLastComputationTime", "getLastComputationDate", "getLastComputationVersion",
    "isGenConv", "isGenPlots", "isGenIntermediatePlots", "isPlotUndefVals",
    "isStoreSolution", "isStoreCompleteHistory", "setSolveFor", "solveFor",
    "refresh", "getStudyStep", "getSequence", "getStatus",
    # solver surface
    "runAll", "runFromTo", "runFrom", "createAutoSequence", "createSolution",
    "getDefaultSolnum", "getSequenceType", "isAttached", "isInitialized",
    "getPVals", "getPNames", "getParamVals", "getParamNames", "getNStepsBack",
    "getErrorMessage", "getInformationMessage", "getWarningMessage", "hasError",
    "hasWarning", "hasInformation", "hasProblem", "hasProblemsOrInformation",
    "hasProblemOrInformation", "problem", "problemNames", "getM", "getN", "getNnz",
    "getSolutioninfo", "clearSolution", "clearSolutionData", "updateSolution",
    "getU", "getSolnum", "getStoreSolution",
    # selection surface
    "selection", "named", "geom", "all", "entities", "inherit", "isInheriting",
    "remaining", "dim", "dimension",
})
_COLLECTIONS = frozenset({
    "component", "geom", "mesh", "feature", "study", "sol", "problem", "stat",
    "measure", "physics", "multiphysics", "material", "func", "variable", "cpl",
    "coordSystem", "propertyGroup", "dataset", "numerical", "view", "pair",
    "probe", "batch", "selection", "expr", "model",
})


#: Property metadata a *newly created* node exposes, keyed by the type string.
#: A real COMSOL build answers properties()/getValueType() for every node it
#: creates; without this table the fake would report an empty property list and
#: every property write would look unsupported.
TYPE_PROPS: dict[str, dict[str, dict[str, Any]]] = {
    "FreeTet": {
        "method": {"type": "String", "value": "auto", "allowed": ["auto", "del", "dellegacy52"]},
        "optlevel": {"type": "String", "value": "medium"},
        "optcurved": {"type": "Boolean", "value": True},
        "smoothcontrol": {"type": "String", "value": "auto"},
        "xscale": {"type": "Double", "value": 1.0},
    },
    "FreeTri": {
        "method": {"type": "String", "value": "auto"},
        "smoothcontrol": {"type": "String", "value": "auto"},
        "xscale": {"type": "Double", "value": 1.0},
    },
    "Size": {
        "custom": {"type": "String", "value": "off", "allowed": ["on", "off"]},
        "hauto": {"type": "Int", "value": 5},
        "hmax": {"type": "Double", "value": 0.1},
        "hgrad": {"type": "Double", "value": 1.3},
        "hcurve": {"type": "Double", "value": 0.6},
    },
    "Map": {"smoothcontrol": {"type": "String", "value": "auto"}},
    "Sweep": {"distribution": {"type": "String", "value": "linear"}},
    "Convert": {"keepinput": {"type": "Boolean", "value": False}},
    "Refine": {"nrefine": {"type": "Int", "value": 1}},
    "Distribution": {
        "numelem": {"type": "Int", "value": 8},
        "distribution": {"type": "String", "value": "linear"},
    },
    "CornerRefinement": {"nrefine": {"type": "Int", "value": 1}},
    "BndLayer": {
        "blnlayers": {"type": "Int", "value": 2},
        "blthickness": {"type": "Double", "value": 0.01},
    },
    "EasyClear": {},
    # study steps
    "Study": {"genPlots": {"type": "Boolean", "value": False},
              "useAdvancedDisable": {"type": "Boolean", "value": True}},
    "Stationary": {
        "geometricNonlinearity": {"type": "Boolean", "value": False},
        "stol": {"type": "Double", "value": 0.001},
        "usestol": {"type": "Boolean", "value": True},
        "plot": {"type": "Boolean", "value": True},
    },
    "Transient": {
        "tlist": {"type": "DoubleArray", "value": [0.0, 1.0]},
        "usertol": {"type": "String", "value": "off"},
        "rtol": {"type": "Double", "value": 0.01},
        "tunit": {"type": "String", "value": "s"},
    },
    "Frequency": {"plist": {"type": "DoubleArray", "value": [1.0]},
                  "usestol": {"type": "Boolean", "value": True}},
    "Eigenvalue": {"neigs": {"type": "Int", "value": 6},
                   "shift": {"type": "Double", "value": 0.0}},
    # solver features
    "StudyStep": {"study": {"type": "String", "value": ""},
                  "studystep": {"type": "String", "value": ""}},
    "Time": {"tlist": {"type": "DoubleArray", "value": [0.0, 1.0]},
             "rtol": {"type": "Double", "value": 0.01},
             "tout": {"type": "String", "value": "tlist"}},
    "Direct": {"linsolver": {"type": "String", "value": "pardiso"},
               "pivotthreshold": {"type": "Double", "value": 0.1}},
    "Iterative": {"linsolver": {"type": "String", "value": "gmres"}},
    "FullyCoupled": {"maxiter": {"type": "Int", "value": 25},
                     "damping": {"type": "Double", "value": 0.9}},
    "Segregated": {"maxiter": {"type": "Int", "value": 25}},
    "Multigrid": {"maxiter": {"type": "Int", "value": 25}},
    "Variables": {"initmethod": {"type": "String", "value": "init"}},
    "StoreSolution": {"plot": {"type": "Boolean", "value": False}},
    "Adaption": {"maxiter": {"type": "Int", "value": 3}},
    "AWE": {"nterms": {"type": "Int", "value": 20}},
}


def node_for(tag: str, *type_ids: Any) -> FNode:
    """Create a node the way COMSOL does: the type string carries its settings."""
    type_id = str(type_ids[0]) if type_ids else "Fake"
    return FNode(tag=tag, type_id=type_id,
                 props=copy.deepcopy(TYPE_PROPS.get(type_id, {})))


class FakeEngineError(RuntimeError):
    """Structured worker failure; ``code`` feeds ``_worker_failure_code``."""

    def __init__(self, message: str, *, code: str = "ENGINE_CALL_FAILED",
                 execution_state_unknown: bool = False) -> None:
        super().__init__(message)
        self.reply = {"ok": False, "code": code, "message": message,
                      "execution_state_unknown": execution_state_unknown}
        self.failure = {"code": code, "message": message,
                        "execution_state_unknown": execution_state_unknown}


def _method_refusal() -> FakeEngineError:
    return FakeEngineError("SecurityException: METHOD_REJECTED", code="METHOD_REJECTED")


class FSelection:
    """Fake of a local ``Selection`` node (``named/geom/set/all/inherit``)."""

    def __init__(self, owner: Any) -> None:
        self.owner = owner
        self.selected: list[int] = []
        self.dim_value: int | None = None
        self.geometry_tag: str | None = None
        self.named_tag: str | None = None
        self.inheriting = False
        self.unavailable: set[str] = set()
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def _guard(self, name: str) -> None:
        self.calls.append((name, ()))
        if name in self.unavailable:
            raise _method_refusal()

    def geom(self, *args: Any) -> int:
        self._guard("geom")
        if len(args) == 2:
            self.geometry_tag, dimension = str(args[0]), int(args[1])
            self.dim_value = dimension
        elif len(args) == 1:
            self.dim_value = int(args[0])
        return self.dim_value or 0

    def dimension(self) -> list[int]:
        return [self.dim_value or 0]

    def named(self, *args: Any) -> Any:
        self.calls.append(("named", args))
        if args:
            self.named_tag = str(args[0])
            return None
        return self.named_tag

    def set(self, *args: Any) -> None:
        self.calls.append(("set", args))
        if len(args) == 1 and isinstance(args[0], (list, tuple)):
            self.selected = [int(item) for item in args[0]]
        else:
            self.selected = [int(item) for item in args]

    def all(self) -> None:
        self.calls.append(("all", ()))
        self.selected = [1, 2, 3, 4]

    def entities(self) -> list[int]:
        return list(self.selected)

    def inherit(self, value: bool) -> None:
        self.calls.append(("inherit", (value,)))
        self.inheriting = bool(value)

    def isInheriting(self) -> bool:
        return self.inheriting

    def remaining(self) -> None:
        self.selected = []


class FList:
    """Stand-in for a COMSOL ``ModelEntityList``."""

    def __init__(self, *, arity: int = 1, unavailable: tuple[str, ...] = (),
                 node_type: str = "Fake", factory: Any = None) -> None:
        self.arity = arity
        self.items: dict[str, Any] = {}
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.unavailable = set(unavailable)
        self.node_type = node_type
        self.factory = factory

    def tags(self) -> list[str]:
        self.calls.append(("tags", ()))
        if "tags" in self.unavailable:
            raise _method_refusal()
        return list(self.items)

    def get(self, tag: str) -> Any:
        self.calls.append(("get", (tag,)))
        if "get" in self.unavailable:
            raise _method_refusal()
        return self.items[tag]

    def __call__(self, tag: str) -> Any:
        return self.get(tag)

    def hasTag(self, tag: str) -> bool:
        return tag in self.items

    def size(self) -> int:
        return len(self.items)

    def index(self, tag: str) -> int:
        return list(self.items).index(tag)

    def create(self, tag: str, *args: Any) -> Any:
        self.calls.append(("create", (tag,) + args))
        if "create" in self.unavailable:
            raise _method_refusal()
        if tag in self.items:
            raise FakeEngineError(f"tag {tag} already exists")
        if self.factory is not None:
            node = self.factory(tag, *(str(item) for item in args))
        else:
            node = FNode(tag=tag, type_id=str(args[0]) if args else self.node_type)
        self.items[tag] = node
        return node

    def remove(self, tag: str) -> None:
        self.calls.append(("remove", (tag,)))
        if "remove" in self.unavailable:
            raise _method_refusal()
        if tag not in self.items:
            raise FakeEngineError(f"no such tag {tag}")
        del self.items[tag]


class FNode:
    """Fake COMSOL node: identity, property metadata, collections, readback state."""

    def __init__(self, tag: str = "node", type_id: str = "Node", *, label: str | None = None,
                 unavailable: Sequence[str] = (), absent: Sequence[str] = (),
                 props: Mapping[str, Mapping[str, Any]] | None = None,
                 state: Mapping[str, Any] | None = None,
                 entries: Mapping[str, Mapping[str, Any]] | None = None) -> None:
        self.tag_ = tag
        self.type_id = type_id
        self.label_ = label if label is not None else tag
        self.unavailable = set(unavailable)
        self.absent = set(absent)
        self.props: dict[str, dict[str, Any]] = {name: dict(spec) for name, spec in (props or {}).items()}
        self.state: dict[str, Any] = dict(state or {})
        self.entries: dict[str, dict[str, Any]] = {name: dict(values) for name, values in (entries or {}).items()}
        self.collections: dict[str, FList] = {}
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.raises: dict[str, str] = {}
        self._selection: FSelection | None = None
        self.removed = False
        self.solve_for: dict[str, bool] = {}
        self.last_range: tuple[str, str] | None = None

    # -- identity -----------------------------------------------------------
    def tag(self) -> str:
        return self.tag_

    def name(self) -> str:
        return self.tag_

    def label(self, *args: Any) -> Any:
        self.calls.append(("label", args))
        if args:
            self.label_ = str(args[0])
            return None
        return self.label_

    def getType(self) -> str:
        return self.type_id

    def type(self) -> str:
        return self.type_id

    def active(self) -> bool:
        return True

    def isActive(self) -> bool:
        return True

    # -- property metadata --------------------------------------------------
    def properties(self) -> list[str]:
        if "properties" in self.unavailable:
            raise _method_refusal()
        return sorted(set(self.props) | set(self.entries))

    def hasProperty(self, name: str) -> bool:
        return name in self.properties()

    def getValueType(self, name: str) -> str | None:
        if "getValueType" in self.unavailable:
            raise _method_refusal()
        spec = self.props.get(name)
        return spec.get("type") if spec else None

    def getAllowedPropertyValues(self, name: str) -> list[str] | None:
        spec = self.props.get(name)
        return list(spec["allowed"]) if spec and spec.get("allowed") else None

    def _read(self, name: str, *rest: Any) -> Any:
        if rest:  # String Map entry read: getString(<property>, <key>)
            table = self.entries.get(name) or {}
            return table.get(str(rest[0]))
        spec = self.props.get(name)
        if spec is None:
            raise FakeEngineError(f"no such property {name}")
        if "readback" in spec:
            return spec["readback"]
        return spec.get("value")

    def getString(self, name: str, *rest: Any) -> Any:
        return self._read(name, *rest)

    def getStringArray(self, name: str, *rest: Any) -> Any:
        return self._read(name, *rest)

    def getStringMatrix(self, name: str, *rest: Any) -> Any:
        return self._read(name, *rest)

    def getInt(self, name: str, *rest: Any) -> Any:
        return self._read(name, *rest)

    def getIntArray(self, name: str, *rest: Any) -> Any:
        return self._read(name, *rest)

    def getIntMatrix(self, name: str, *rest: Any) -> Any:
        return self._read(name, *rest)

    def getDouble(self, name: str, *rest: Any) -> Any:
        return self._read(name, *rest)

    def getDoubleArray(self, name: str, *rest: Any) -> Any:
        return self._read(name, *rest)

    def getDoubleMatrix(self, name: str, *rest: Any) -> Any:
        return self._read(name, *rest)

    def getBoolean(self, name: str, *rest: Any) -> Any:
        return self._read(name, *rest)

    def getBooleanArray(self, name: str, *rest: Any) -> Any:
        return self._read(name, *rest)

    def getBooleanMatrix(self, name: str, *rest: Any) -> Any:
        return self._read(name, *rest)

    def set(self, *args: Any) -> None:
        self.calls.append(("set", args))
        if "set" in self.unavailable:
            raise _method_refusal()
        if len(args) == 2 and isinstance(args[1], Mapping):
            name, typed = args
            self._store(name, typed.get("kind"), typed.get("data"))
            return
        if len(args) == 2:
            name, value = args
            spec = self.props.get(name) or {}
            self._store(name, _ENGINE_KINDS.get(str(spec.get("type"))), value)
            return
        raise FakeEngineError("set() received an unsupported argument count")

    def _store(self, name: str, kind: str | None, data: Any) -> None:
        spec = self.props.setdefault(name, {})
        if spec.get("type") is None and kind is not None:
            for candidate, engine_kind in _ENGINE_KINDS.items():
                if engine_kind == kind:
                    spec["type"] = candidate
                    break
        spec["value"] = data
        spec.pop("readback", None)

    def setEntry(self, name: str, key: str, *values: Any) -> None:
        self.calls.append(("setEntry", (name, key) + values))
        if "setEntry" in self.unavailable:
            raise _method_refusal()
        table = self.entries.setdefault(name, {})
        table[str(key)] = values[0] if len(values) == 1 else list(values)

    def getEntryKeys(self, name: str) -> list[str]:
        self.calls.append(("getEntryKeys", (name,)))
        if "getEntryKeys" in self.unavailable:
            raise _method_refusal()
        return list(self.entries.get(name) or {})

    def removeEntry(self, name: str, key: str) -> None:
        (self.entries.get(name) or {}).pop(str(key), None)

    # -- collections --------------------------------------------------------
    def _collection(self, name: str) -> FList:
        container = self.collections.get(name)
        if container is None:
            container = FList(node_type=name)
            self.collections[name] = container
        return container

    def declare(self, name: str, container: FList) -> FList:
        self.collections[name] = container
        return container

    # -- node-level create/remove (MeshSequence/Study/SolverSequence/... all
    #    declare create(String,String) and the matching remove) --------------
    def create(self, tag: str, *args: Any) -> Any:
        self.calls.append(("create", (tag,) + args))
        if "create" in self.unavailable:
            raise _method_refusal()
        return self._collection("feature").create(tag, *args)

    def remove(self, *args: Any) -> None:
        self.calls.append(("remove", args))
        if "remove" in self.unavailable:
            raise _method_refusal()
        if not args:
            self.removed = True
            return None
        self._collection("feature").remove(str(args[0]))
        return None

    def clear(self) -> None:
        self._collection("feature").items.clear()

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__") or name.startswith("_") or name in {
            "tag_", "type_id", "label_", "props", "state", "entries", "collections",
            "calls", "unavailable", "absent", "raises", "items", "factory", "arity",
        }:
            raise AttributeError(name)
        if name in self.absent:
            raise AttributeError(name)
        if name in self.state:
            value = self.state[name]

            def state_method(*args: Any, **kwargs: Any) -> Any:
                if kwargs:
                    raise AttributeError(name)
                if name in self.unavailable:
                    raise _method_refusal()
                if callable(value) and not isinstance(value, (FNode, FList)):
                    return value(*args)
                return value

            return state_method
        if name in _COLLECTIONS:
            container = self._collection(name)

            def accessor(*args: Any, **kwargs: Any) -> Any:
                if kwargs:
                    raise AttributeError(name)
                if args:
                    return container.get(str(args[0]))
                return container

            return accessor
        if name not in _KNOWN_METHODS:
            raise AttributeError(name)

        def method(*args: Any, **kwargs: Any) -> Any:
            if name in self.unavailable:
                raise _method_refusal()
            self.calls.append((name, args))
            if name in self.raises:
                raise FakeEngineError(self.raises[name])
            if name in self.state:
                value = self.state[name]
                return value(*args) if callable(value) and not isinstance(value, FList) else value
            return None

        return method

    # -- study / mesh / solver readback helpers -----------------------------
    def selection(self, *args: Any) -> FSelection:
        self.calls.append(("selection", args))
        if "selection" in self.unavailable:
            raise _method_refusal()
        if self._selection is None:
            self._selection = FSelection(self)
        return self._selection

    # -- compute (mesh build / study run / solver run) ----------------------
    def _sync_stat(self) -> None:
        stat = self.state.get("stat")
        if isinstance(stat, FNode):
            for name, value in self.state.items():
                if isinstance(name, str) and name.startswith(("get", "is", "has")):
                    stat.state[name] = value

    def run(self, *args: Any) -> None:
        self.calls.append(("run", args))
        if "run" in self.unavailable:
            raise _method_refusal()
        if "run" in self.raises:
            failure = self.raises["run"]
            if isinstance(failure, BaseException):
                raise failure
            raise FakeEngineError(str(failure))
        if "geom" in self.state:  # meshing sequence
            self.state.update({
                "isEmpty": False, "isComplete": True, "getNumElem": 2684, "getNumVertex": 512,
                "getTypes": ["tet"], "getMaxDimension": 3, "buildTime": 42.0,
                "getMinQuality": 0.313, "getMeanQuality": 0.812, "getVolume": 1.0,
                "getQualityDistr": [12, 40, 88, 24], "problems": [],
            })
            self._sync_stat()
        if "getLastComputationTime" in self.state:  # study
            self.state.update({"getLastComputationTime": 0.42,
                               "getLastComputationDate": "2026-09-20 12:00:00",
                               "getLastComputationVersion": "6.4"})
        if "isEmpty" in self.state and self.type_id == "SolverSequence":
            self.state.update({"isEmpty": False, "isInitialized": True, "getDefaultSolnum": 1,
                               "getPVals": [1.0], "getPNames": ["p1"]})

    def runAll(self, *args: Any) -> None:
        self.calls.append(("runAll", args))
        if "runAll" in self.unavailable:
            raise _method_refusal()
        self.state.update({"isEmpty": False, "isInitialized": True, "getDefaultSolnum": 1,
                           "getPVals": [1.0], "getPNames": ["p1"]})

    def runFromTo(self, first: str, last: str) -> None:
        self.calls.append(("runFromTo", (first, last)))
        if "runFromTo" in self.unavailable:
            raise _method_refusal()
        self.last_range = (str(first), str(last))
        self.state.update({"isEmpty": False, "isInitialized": True})

    def automatic(self, *args: Any) -> Any:
        """``MeshSequence.automatic(boolean)`` / ``isAutomatic()`` (javap)."""
        self.calls.append(("automatic", args))
        if "automatic" in self.unavailable:
            raise _method_refusal()
        if args:
            self.state["isAutomatic"] = bool(args[0])
            return None
        return self.state.get("isAutomatic")

    def createAutoSequences(self, *args: Any) -> Any:
        """``Study.createAutoSequences(String)``: attach a default solver sequence."""
        self.calls.append(("createAutoSequences", args))
        if "createAutoSequences" in self.unavailable:
            raise _method_refusal()
        kind = str(args[0]) if args else "sol"
        if kind not in {"all", "jobs", "sol"}:
            raise FakeEngineError(f"unknown createAutoSequences type {kind}")
        sol_list = self.state.get("_sol_list")
        if not isinstance(sol_list, FList):
            return None
        name = f"auto_{self.tag_}_{kind}"
        sol_list.items[name] = new_solver(name, study=self.tag_)
        return [name]

    def setQualityMeasure(self, metric: str) -> None:
        self.calls.append(("setQualityMeasure", (metric,)))
        if "setQualityMeasure" in self.unavailable:
            raise _method_refusal()
        if metric not in w16.QUALITY_MEASURES:
            raise FakeEngineError(f"unknown quality measure {metric}")
        self.state["getQualityMeasure"] = str(metric)

    def getQualityMeasure(self) -> Any:
        return self.state.get("getQualityMeasure")

    def setSolveFor(self, entity: str, value: bool) -> None:
        self.calls.append(("setSolveFor", (entity, value)))
        if "setSolveFor" in self.unavailable:
            raise _method_refusal()
        self.solve_for[str(entity)] = bool(value)

    def solveFor(self, entity: str) -> bool:
        self.calls.append(("solveFor", (entity,)))
        if "solveFor" in self.unavailable:
            raise _method_refusal()
        return self.solve_for.get(str(entity), True)

    def model(self, *args: Any) -> Any:
        self.calls.append(("model", args))
        if args:
            self.state["model"] = str(args[0])
            return None
        return self.state.get("model")


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

    def __init__(self, model: FNode, *, model_tag: str = "Model") -> None:
        self._client = FClient({model_tag: model})

    def client(self) -> FClient:
        return self._client


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def mesh_sequence(tag: str = "mesh1", *, geometry: str = "geom1", automatic: bool = True,
                  **kwargs: Any) -> FNode:
    state = {
        "geom": geometry, "isAutomatic": automatic, "getSDim": 3, "isEmpty": True,
        "isComplete": False, "current": "size", "getNumElem": 0, "getNumVertex": 0,
        "getTypes": [], "getGeomEntities": {}, "getMaxDimension": 3, "getVolume": 0.0,
        "getMaxVolume": 0.0, "getMinVolume": 0.0, "getMaxGrowthRate": 0.0,
        "getMeanGrowthRate": 0.0, "getMinQuality": 1.0, "getMeanQuality": 1.0,
        "getQualityMeasure": "volcircum", "getQualityDistr": [], "lengthUnit": "m",
        "hasSecondOrderElements": False, "buildTime": 0.0, "problems": [],
        "hasProblems": False, "status": "built", "message": "",
    }
    state.update(kwargs.pop("state", {}))
    stat = FNode(tag="stat", type_id="MeshStatistics", state={
        name: state[name] for name in (
            "getNumElem", "getNumVertex", "getTypes", "getMinQuality", "getMeanQuality",
            "getVolume", "getMaxVolume", "getMinVolume", "getMaxGrowthRate",
            "getMeanGrowthRate", "getMaxDimension", "getQualityMeasure", "isComplete",
            "isEmpty", "getQualityDistr",
        )
    })
    state["stat"] = stat
    node = FNode(tag=tag, type_id="MeshSequence", state=state, **kwargs)
    node.declare("feature", FList(factory=node_for))
    return node


def sol_list_placeholder() -> FList:
    """The model-level ``sol()`` container a study's createAutoSequences fills."""
    return FList(factory=lambda tag, *args: new_solver(tag, args[0] if args else None))


def build_model() -> FModel:
    """A small but complete model: one component, two meshes, two studies, two solvers."""
    model = FModel()
    comp = FNode(tag="comp1", type_id="Component")
    model.declare("component", FList(factory=lambda tag, *_a: FNode(tag=tag, type_id="Component")))
    model.collections["component"].items["comp1"] = comp

    comp.declare("geom", FList(factory=lambda tag, *_a: FNode(tag=tag, type_id="GeomSequence")))
    comp.collections["geom"].items["geom1"] = FNode(tag="geom1", type_id="GeomSequence")

    mesh_list = FList(factory=lambda tag, *_a: mesh_sequence(tag))
    comp.declare("mesh", mesh_list)
    mesh1 = mesh_sequence("mesh1")
    mesh_list.items["mesh1"] = mesh1

    # mesh1 features: ftet1 (FreeTet) with a local Size attribute
    features = mesh1.collections["feature"]
    ftet = node_for("ftet1", "FreeTet")
    ftet.declare("feature", FList(factory=node_for))
    features.items["ftet1"] = ftet
    size = node_for("size1", "Size")
    ftet.collections["feature"].items["size1"] = size

    # studies
    study_list = FList(factory=node_for)
    model.declare("study", study_list)
    std1 = node_for("std1", "Study")
    std1.state.update({"getLastComputationTime": None, "getLastComputationDate": None,
                       "getLastComputationVersion": None})
    std1.declare("feature", FList(factory=node_for))
    stat = node_for("stat", "Stationary")
    stat.entries["activate"] = {"phys1": "on"}
    stat.state["solveFor"] = True
    std1.collections["feature"].items["stat"] = stat
    study_list.items["std1"] = std1
    std1.state["_sol_list"] = sol_list_placeholder()
    std2 = node_for("std2", "Study")
    std2.declare("feature", FList(factory=node_for))
    study_list.items["std2"] = std2

    # solvers
    sol_list = FList(factory=lambda tag, *args: new_solver(tag, args[0] if args else None))
    model.declare("sol", sol_list)
    sol_list.items["sol1"] = build_solver("sol1", study="std1")
    sol_list.items["sol2"] = build_solver("sol2", study="std2")
    model.collections["study"].items["std1"].state["_sol_list"] = sol_list
    model.collections["study"].items["std2"].state["_sol_list"] = sol_list
    return model


def new_solver(tag: str = "sol_new", study: str | None = None) -> FNode:
    """A freshly created (empty, attached) solver sequence."""
    sol = FNode(tag=tag, type_id="SolverSequence", state={
        "isEmpty": True, "isInitialized": False, "isAttached": study is not None,
        "getSequenceType": "General", "getDefaultSolnum": 0, "study": study,
        "getPVals": [], "getPNames": [], "hasProblems": False, "getErrorMessage": "",
        "getInformationMessage": "", "getWarningMessage": "", "getNStepsBack": 0,
    })
    sol.declare("feature", FList(factory=node_for))
    return sol


def build_solver(tag: str = "sol1", *, study: str | None = "std1") -> FNode:
    sol = FNode(tag=tag, type_id="SolverSequence", state={
        "isEmpty": False, "isInitialized": True, "isAttached": study is not None,
        "getSequenceType": "General", "getDefaultSolnum": 1, "study": study,
        "getPVals": [1.0], "getPNames": ["p1"], "hasProblems": False,
        "getErrorMessage": "", "getInformationMessage": "", "getWarningMessage": "",
        "getNStepsBack": 0,
    })
    sol.declare("feature", FList(factory=node_for))
    features = sol.collections["feature"]
    st = node_for("st1", "StudyStep")
    st.props["study"]["value"] = study or ""
    st.props["studystep"]["value"] = "stat"
    st.state.update({"hasError": False, "hasWarning": False, "hasInformation": False,
                     "hasProblem": False})
    st.declare("feature", FList(factory=node_for))
    sub = node_for("s1", "Stationary")
    sub.state.update({"hasError": False, "hasWarning": False, "hasInformation": False,
                      "hasProblem": False, "getM": 10, "getN": 100, "getNnz": 400})
    sub.declare("feature", FList(factory=node_for))
    direct = node_for("d1", "Direct")
    direct.state.update({"hasError": False, "hasProblem": False})
    sub.collections["feature"].items["d1"] = direct
    fc = node_for("fc1", "FullyCoupled")
    fc.state.update({"hasError": False, "hasProblem": False})
    sub.collections["feature"].items["fc1"] = fc
    st.collections["feature"].items["s1"] = sub
    features.items["st1"] = st
    return sol


def worker_for(model: FNode) -> FWorker:
    return FWorker(model)


def expect_error(code: str, function: Any, *args: Any, **kwargs: Any) -> ExecutionContractError:
    with pytest.raises(ExecutionContractError) as info:
        function(*args, **kwargs)
    assert info.value.code == code, f"expected {code}, got {info.value.code}: {info.value}"
    return info.value


def call(operation_id: str, model: FNode, arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Dispatch exactly like the control plane does."""
    return DISPATCH[operation_id](worker_for(model), "Model", dict(arguments or {}))


MESH_PATH = {"segments": [{"collection": "component", "tag": "comp1"},
                          {"collection": "mesh", "tag": "mesh1"}]}
STUDY_PATH = {"segments": [{"collection": "study", "tag": "std1"}]}
SOLVER_PATH = {"segments": [{"collection": "sol", "tag": "sol1"}]}


def mesh_step(model: FModel, feature: str) -> Mapping[str, Any]:
    return {"segments": list(MESH_PATH["segments"]) + [{"collection": "feature", "tag": feature}]}


def study_step(model: FModel, feature: str) -> Mapping[str, Any]:
    return {"segments": list(STUDY_PATH["segments"]) + [{"collection": "feature", "tag": feature}]}


def solver_step(model: FModel, tag: str) -> Mapping[str, Any]:
    return {"segments": list(SOLVER_PATH["segments"]) + [{"collection": "feature", "tag": tag}]}


# ---------------------------------------------------------------------------
# contract / aggregation
# ---------------------------------------------------------------------------


def test_operations_publish_the_frozen_contract() -> None:
    assert isinstance(w16.OPERATIONS, dict)
    for operation_id, function in w16.OPERATIONS.items():
        assert isinstance(operation_id, str) and operation_id.startswith(("mesh.", "study.", "solver."))
        assert callable(function)
    assert len(w16.OPERATIONS) == 28


def test_operations_are_aggregated_by_the_dispatch_table() -> None:
    for operation_id in w16.OPERATIONS:
        assert operation_id in DISPATCH, f"{operation_id} is missing from the aggregated DISPATCH table"
        assert operation_id in IMPLEMENTED_OPERATIONS
    assert set(w16.OPERATIONS).issubset(set(DISPATCH))
    assert "mesh.build" in REQUIRES_ISOLATION
    assert "study.run" in REQUIRES_ISOLATION
    assert "solver.run" in REQUIRES_ISOLATION
    assert "mesh.build" not in REQUIRES_ISOLATION or EFFECTS["mesh.build"] in {"COMPUTE", "EVALUATE", "WRITE"}
    assert EFFECTS["mesh.list"] == "READ"
    assert EFFECTS["mesh.run" if "mesh.run" in EFFECTS else "mesh.build"] == "COMPUTE"


def test_operation_argument_surfaces_match_the_catalog() -> None:
    from pathlib import Path

    catalog_path = Path(w16.__file__).resolve().parent / "data" / "g2" / "02_ACTION_CATALOG.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    by_id = {}
    for row in catalog["operations"]:
        by_id[row["operation_id"]] = row
    catalog_slice = {row["operation_id"] for row in catalog["operations"]
                     if row["operation_id"].split(".")[0] in {"mesh", "study", "solver"}}
    # W16 implements the catalogued mesh/study/solver operations named in the
    # W16 scope; the catalog also carries further operations in these domains
    # (mesh.import/export/..., solver.solution_*, study.sweep_manage, ...) that
    # are outside the assigned scope and are deliberately NOT published here.
    assert set(w16.OPERATIONS) == _W16_SCOPE
    assert set(w16.OPERATIONS) < catalog_slice
    assert catalog_slice - _W16_SCOPE
    envelope = _ENVELOPE_KEYS
    for operation_id in sorted(_W16_SCOPE):
        schema = by_id[operation_id].get("input_schema") or {}
        properties = set(schema.get("properties") or {})
        required = set(schema.get("required") or [])
        declared = set(w16.OPERATION_ARGUMENTS[operation_id])
        assert declared == properties - envelope, (
            f"{operation_id}: implementation accepts {sorted(declared)} but the catalog declares "
            f"{sorted(properties - envelope)}"
        )
        assert set(required) - _ENVELOPE_KEYS == set(w16.OPERATION_REQUIRED[operation_id]), (
            f"{operation_id}: catalog requires {sorted(set(required) - _ENVELOPE_KEYS)} but the "
            f"implementation requires {sorted(w16.OPERATION_REQUIRED[operation_id])}"
        )


# ---------------------------------------------------------------------------
# mesh
# ---------------------------------------------------------------------------


def test_mesh_list_reports_every_sequence() -> None:
    model = build_model()
    result = call("mesh.list", model, {})
    assert result["mesh_count"] == 1
    row = result["meshes"][0]
    assert row["component"] == "comp1"
    assert row["mesh"] == "mesh1"
    assert row["geometry"] == "geom1"
    assert row["sdim"] == 3
    assert row["physics_controlled"] is True
    assert row["feature_count"] == 1
    assert row["element_count"] == 0
    assert row["path"]["segments"] == MESH_PATH["segments"]


def test_mesh_list_surfaces_a_missing_allowlist_entry() -> None:
    model = build_model()
    mesh = model.collections["component"].items["comp1"].collections["mesh"].items["mesh1"]
    for target in (mesh, mesh.state["stat"]):
        target.unavailable.add("getNumElem")
    result = call("mesh.list", model, {})
    assert "getNumElem" in result["meshes"][0]["allowlist_entry_required"]
    assert result["meshes"][0]["element_count"] is None
    assert "getNumElem" in result["meshes"][0]["read_errors"]


def test_mesh_create_reads_back_tag_and_mode() -> None:
    model = build_model()
    result = call("mesh.create", model, {"component": "comp1", "tag": "mesh2",
                                         "geometry": "geom1", "mode": "user_controlled"})
    assert result["status"] == "APPLIED"
    assert result["ok"] is True
    assert "mesh2" in result["tag_readback"]
    assert result["readback"]["state"]["isAutomatic"] is False
    assert result["readback"]["state"]["geom"] == "geom1"
    created = model.collections["component"].items["comp1"].collections["mesh"].items["mesh2"]
    assert created.type_id == "MeshSequence"


def test_mesh_create_rejects_an_existing_tag() -> None:
    model = build_model()
    error = expect_error("TAG_CONFLICT", call, "mesh.create", model,
                         {"component": "comp1", "tag": "mesh1", "geometry": "geom1"})
    assert "mesh1" in str(error)


def test_mesh_create_rejects_an_unknown_mode() -> None:
    model = build_model()
    expect_error("INVALID_REQUEST", call, "mesh.create", model,
                 {"component": "comp1", "tag": "mesh3", "geometry": "geom1", "mode": "wizard"})


def test_mesh_create_rejects_a_missing_component() -> None:
    model = build_model()
    expect_error("NODE_NOT_FOUND", call, "mesh.create", model,
                 {"component": "comp9", "tag": "mesh3", "geometry": "geom1"})


def test_mesh_create_reports_a_worker_allowlist_refusal() -> None:
    model = build_model()
    container = model.collections["component"].items["comp1"].collections["mesh"]
    container.unavailable.add("create")
    error = expect_error("ENGINE_CALL_FAILED", call, "mesh.create",
                         model, {"component": "comp1", "tag": "mesh3", "geometry": "geom1"})
    assert "create" in str(error)


def test_mesh_inspect_walks_the_feature_tree_with_depth() -> None:
    model = build_model()
    result = call("mesh.inspect", model, {"path": MESH_PATH, "depth": 3})
    assert result["kind"] == "mesh_sequence"
    assert result["feature_count"] == 1
    feature = result["features"][0]
    assert feature["tag"] == "ftet1"
    assert feature["type_id"] == "FreeTet"
    assert [row["tag"] for row in feature["children"]] == ["size1"]
    assert feature["children"][0]["type_id"] == "Size"
    assert set(feature) >= {"tag", "type_id", "label", "status", "problems", "children"}
    assert w16.MESH_FEATURE_TYPE_SOURCES["FreeTet"].startswith("comsol_api_mesh.49.")


def test_mesh_inspect_rejects_a_non_mesh_path() -> None:
    model = build_model()
    expect_error("INVALID_NODE_PATH", call, "mesh.inspect", model,
                 {"path": {"segments": [{"collection": "study", "tag": "std1"}]}})


def test_mesh_feature_create_applies_properties_and_selection() -> None:
    model = build_model()
    result = call("mesh.feature_create", model, {
        "parent": MESH_PATH, "tag": "ftet2", "type_id": "FreeTet",
        "properties": {"method": "del", "optlevel": "high"},
    })
    assert result["status"] == "APPLIED"
    assert result["type_readback"] == "FreeTet"
    assert result["property_source"] == "engine_properties"
    created = model.collections["component"].items["comp1"].collections["mesh"].items["mesh1"]
    node = created.collections["feature"].items["ftet2"]
    assert node.props["method"]["value"] == "del"
    assert node.props["optlevel"]["value"] == "high"


def test_mesh_feature_create_binds_an_explicit_local_selection() -> None:
    model = build_model()
    result = call("mesh.feature_create", model, {
        "parent": MESH_PATH, "tag": "size_dom", "type_id": "Size",
        "properties": {"custom": "on", "hauto": 4},
        "selection": {"kind": "explicit", "entity_dimension": 3, "entities": [1, 2]},
    })
    assert result["status"] == "APPLIED"
    assert result["selection"]["kind"] == "explicit"
    assert result["selection"]["applied"][0]["entities"] == [1, 2]
    created = model.collections["component"].items["comp1"].collections["mesh"].items["mesh1"]
    node = created.collections["feature"].items["size_dom"]
    assert node.selection().entities() == [1, 2]
    assert node.props["hauto"]["value"] == 4


def test_mesh_feature_create_rejects_an_unverified_type_before_writing() -> None:
    model = build_model()
    error = expect_error("API_UNSUPPORTED", call, "mesh.feature_create", model, {
        "parent": MESH_PATH, "tag": "weird", "type_id": "Frobnicate", "properties": {},
    })
    assert "verified COMSOL 6.4" in str(error)
    assert "Frobnicate" in str(error)


def test_mesh_feature_create_refuses_an_unknown_property_without_writing_it() -> None:
    """The node is created first; the refused property must stay unwritten.

    The property *name* is checked against the node's own authoritative
    properties() list before the first setter call, and a refusal after the
    create is reported as a partial failure instead of an exception (the write
    already happened).  What must never happen is a setter call for a name the
    build does not expose.
    """
    model = build_model()
    mesh = model.collections["component"].items["comp1"].collections["mesh"].items["mesh1"]
    result = call("mesh.feature_create", model, {
        "parent": MESH_PATH, "tag": "ftet3", "type_id": "FreeTet",
        "properties": {"notAProperty": 1},
    })
    assert result["status"] == "PARTIAL_FAILURE"
    assert result["ok"] is False
    assert result["partial_change"] is True
    step = [row for row in result["failed"] if row["step"] == "properties"][0]
    assert "notAProperty" in step["error"]["message"]
    created = mesh.collections["feature"].items["ftet3"]
    assert ("set", ("notAProperty", 1)) not in created.calls
    assert "notAProperty" not in created.props


def test_mesh_feature_create_reports_a_type_conflict_on_an_existing_tag() -> None:
    model = build_model()
    expect_error("TYPE_CONFLICT", call, "mesh.feature_create", model, {
        "parent": MESH_PATH, "tag": "ftet1", "type_id": "FreeTri", "properties": {},
    })


def test_mesh_feature_create_rejects_a_parent_that_is_not_a_mesh_node() -> None:
    model = build_model()
    expect_error("INVALID_NODE_PATH", call, "mesh.feature_create", model, {
        "parent": STUDY_PATH, "tag": "ftet4", "type_id": "FreeTet", "properties": {},
    })


def test_mesh_feature_update_writes_and_reads_back() -> None:
    model = build_model()
    path = {"segments": list(MESH_PATH["segments"])
            + [{"collection": "feature", "tag": "ftet1"}, {"collection": "feature", "tag": "size1"}]}
    result = call("mesh.feature_update", model, {"path": path, "properties": {"hauto": 7}})
    assert result["status"] == "APPLIED"
    assert result["properties"]["hauto"]["kind"] == "int32"
    assert result["properties"]["hauto"]["data"] == 7
    assert result["rebuild_required"] is True
    assert "mesh.build" in json.dumps(result)


def test_mesh_feature_update_rejects_a_type_mismatch() -> None:
    model = build_model()
    path = {"segments": list(MESH_PATH["segments"]) + [{"collection": "feature", "tag": "ftet1"}]}
    expect_error("PROPERTY_TYPE_MISMATCH", call, "mesh.feature_update", model,
                 {"path": path, "properties": {"xscale": "big"}})


def test_mesh_feature_update_rejects_an_unknown_property() -> None:
    model = build_model()
    mesh = model.collections["component"].items["comp1"].collections["mesh"].items["mesh1"]
    node = mesh.collections["feature"].items["ftet1"]
    path = {"segments": list(MESH_PATH["segments"]) + [{"collection": "feature", "tag": "ftet1"}]}
    error = expect_error("INVALID_REQUEST", call, "mesh.feature_update", model,
                         {"path": path, "properties": {"nope": 1}})
    assert "unsupported fields" in str(error)
    assert not [call_row for call_row in node.calls if call_row[0] == "set"]


def test_mesh_feature_remove_reads_back_the_tags() -> None:
    model = build_model()
    path = {"segments": list(MESH_PATH["segments"])
            + [{"collection": "feature", "tag": "ftet1"}, {"collection": "feature", "tag": "size1"}]}
    result = call("mesh.feature_remove", model, {"path": path})
    assert result["status"] == "APPLIED"
    assert result["removed"] is True
    assert result["tag_readback_before"] == ["size1"]
    assert result["tag_readback_after"] == []


def test_mesh_feature_remove_rejects_an_unknown_tag() -> None:
    model = build_model()
    expect_error("NODE_NOT_FOUND", call, "mesh.feature_remove", model,
                 {"path": mesh_step(model, "nope")})


def test_mesh_build_runs_and_reads_the_statistics_back() -> None:
    model = build_model()
    result = call("mesh.build", model, {"path": MESH_PATH})
    assert result["status"] == "APPLIED"
    assert result["built_feature_range"] == ["ftet1"]
    assert isinstance(result["duration_s"], float)
    assert result["state_before"]["getNumElem"] == 0
    assert result["state_after"]["getNumElem"] == 2684
    assert result["state_after"]["isComplete"] is True
    assert result["state_changed"] is True
    assert result["readback_allowlist_entry_required"] == []


def test_mesh_build_honours_until_tag() -> None:
    model = build_model()
    mesh = model.collections["component"].items["comp1"].collections["mesh"].items["mesh1"]
    result = call("mesh.build", model, {"path": MESH_PATH, "until_tag": "ftet1"})
    assert ("run", ("ftet1",)) in mesh.calls
    assert result["until_tag"] == "ftet1"


def test_mesh_build_rejects_an_unknown_until_tag() -> None:
    model = build_model()
    expect_error("NODE_NOT_FOUND", call, "mesh.build", model,
                 {"path": MESH_PATH, "until_tag": "ghost"})


def test_mesh_build_reports_an_unverifiable_readback_instead_of_claiming_success() -> None:
    model = build_model()
    mesh = model.collections["component"].items["comp1"].collections["mesh"].items["mesh1"]
    mesh.unavailable.update({"current", "isComplete", "isEmpty", "getNumElem", "getTypes",
                             "getSDim", "hasProblems", "problems", "getVolume", "buildTime"})
    result = call("mesh.build", model, {"path": MESH_PATH})
    assert result["status"] == "DISPATCHED_UNVERIFIED"
    assert result["execution_state_unknown"] is True
    assert result["ok"] is False
    assert "getNumElem" in result["readback_allowlist_entry_required"]
    assert ("run", ()) in mesh.calls  # the build itself was dispatched


def test_mesh_clear_preserves_the_configured_features() -> None:
    model = build_model()
    result = call("mesh.clear", model, {"path": MESH_PATH})
    assert result["status"] == "APPLIED"
    assert result["features_before"] == ["ftet1"]
    assert result["features_after"] == ["ftet1"]
    assert result["features_preserved"] is True
    mesh = model.collections["component"].items["comp1"].collections["mesh"].items["mesh1"]
    assert ("clearMesh", ()) in mesh.calls


def test_mesh_clear_names_the_destructive_variant_it_does_not_use() -> None:
    model = build_model()
    result = call("mesh.clear", model, {"path": MESH_PATH})
    assert "feature().clear()" in json.dumps(result["destructive_variant_excluded"])


def test_mesh_statistics_reports_counts_and_refuses_to_invent_dofs() -> None:
    model = build_model()
    call("mesh.build", model, {"path": MESH_PATH})
    result = call("mesh.statistics", model, {"path": MESH_PATH})
    assert result["element_count"] == 2684
    assert result["vertex_count"] == 512
    assert result["element_types"] == ["tet"]
    assert result["min_quality"] == 0.313
    assert len(result["quality_definition"].split()) > 3
    assert result["dof_estimate"]["status"] == "NOT_AVAILABLE"
    assert result["statistics"]["getNumElem"] == 2684


def test_mesh_statistics_lists_blocked_reads() -> None:
    model = build_model()
    mesh = model.collections["component"].items["comp1"].collections["mesh"].items["mesh1"]
    for target in (mesh, mesh.state["stat"]):
        target.unavailable.add("getNumElem")
    result = call("mesh.statistics", model, {"path": MESH_PATH})
    assert "getNumElem" in result["allowlist_entry_required"]
    assert "getNumElem" in result["read_errors"]


def test_mesh_quality_sets_the_measure_and_reports_the_distribution() -> None:
    model = build_model()
    call("mesh.build", model, {"path": MESH_PATH})
    result = call("mesh.quality", model, {"path": MESH_PATH, "metric": "skewness", "bins": 4})
    assert result["quality_measure_before"] == "volcircum"
    assert result["quality_measure_change"]["requested"] == "skewness"
    assert result["quality_measure_readback"] == "skewness"
    assert result["histogram"]["counts"] == [12, 40, 88, 24]
    assert result["histogram"]["engine_bins"] == 4
    assert result["histogram"]["bin_count_matches_request"] is True
    assert result["worst_element_locations"]["status"] == "NOT_AVAILABLE"


def test_mesh_quality_keeps_the_measure_when_it_already_matches() -> None:
    model = build_model()
    result = call("mesh.quality", model, {"path": MESH_PATH, "metric": "volcircum"})
    assert result["quality_measure_change"] is None
    assert result["quality_measure_readback"] == "volcircum"


def test_mesh_quality_rejects_an_unknown_metric() -> None:
    model = build_model()
    error = expect_error("API_UNSUPPORTED", call, "mesh.quality", model,
                         {"path": MESH_PATH, "metric": "sparkle"})
    assert "sparkle" in str(error)


def test_mesh_quality_rejects_the_custom_metric() -> None:
    model = build_model()
    expect_error("API_UNSUPPORTED", call, "mesh.quality", model,
                 {"path": MESH_PATH, "metric": "custom"})


def test_mesh_validate_evaluates_the_registered_criteria() -> None:
    model = build_model()
    call("mesh.build", model, {"path": MESH_PATH})
    result = call("mesh.validate", model, {"path": MESH_PATH,
                                           "criteria": {"require_complete": True,
                                                        "require_sdim": 3,
                                                        "require_element_types": ["tet"],
                                                        "min_quality": {"value": 0.1}}})
    checks = {row["check"]: row for row in result["checks"]}
    assert checks["require_complete"]["status"] == "PASS"
    assert checks["require_sdim"]["status"] == "PASS"
    assert checks["require_element_types"]["status"] == "PASS"
    assert checks["min_quality"]["status"] == "PASS"
    assert checks["min_quality"]["actual"] == 0.313
    assert result["verdict"] == "PASS"
    assert result["failure_count"] == 0


def test_mesh_validate_reports_a_failed_threshold() -> None:
    model = build_model()
    call("mesh.build", model, {"path": MESH_PATH})
    result = call("mesh.validate", model, {"path": MESH_PATH,
                                           "criteria": {"min_quality": {"value": 0.9}}})
    checks = {row["check"]: row for row in result["checks"]}
    assert checks["min_quality"]["status"] == "FAIL"
    assert result["verdict"] == "FAIL"
    assert result["failure_count"] == 1


def test_mesh_validate_rejects_unknown_criteria() -> None:
    model = build_model()
    expect_error("INVALID_REQUEST", call, "mesh.validate", model,
                 {"path": MESH_PATH, "criteria": {"require_pretty": True}})


def test_mesh_validate_rejects_unverified_element_types() -> None:
    model = build_model()
    expect_error("INVALID_REQUEST", call, "mesh.validate", model,
                 {"path": MESH_PATH, "criteria": {"require_element_types": ["blob"]}})


# ---------------------------------------------------------------------------
# study
# ---------------------------------------------------------------------------


def test_study_list_links_solver_sequences() -> None:
    model = build_model()
    result = call("study.list", model, {})
    assert result["study_count"] == 2
    assert [row["solver"] for row in result["solver_associations"]["std1"]] == ["sol1"]
    assert [row["solver"] for row in result["solver_associations"]["std2"]] == ["sol2"]
    first = result["studies"][0]
    assert first["steps"][0]["tag"] == "stat"
    assert first["steps"][0]["type_id"] == "Stationary"


def test_study_create_sets_the_label_and_reads_back() -> None:
    model = build_model()
    result = call("study.create", model, {"tag": "std3", "label": "Third"})
    assert result["status"] == "APPLIED"
    assert result["tag_readback"] == ["std1", "std2", "std3"]
    assert result["label_readback"] == "Third"
    assert "empty study" in result["definition_note"]


def test_study_create_rejects_an_existing_tag() -> None:
    model = build_model()
    expect_error("TAG_CONFLICT", call, "study.create", model, {"tag": "std1"})


def test_study_inspect_reports_steps_and_activation() -> None:
    model = build_model()
    result = call("study.inspect", model, {"path": STUDY_PATH})
    assert result["study"] == "std1"
    assert result["step_count"] == 1
    step = result["steps"][0]
    assert step["type_id"] == "Stationary"
    assert step["generates_equations"] is True
    assert step["physics_activation"]["activate"] == {"phys1": "on"}
    assert [row["solver"] for row in result["solver_sequences"]] == ["sol1"]
    assert result["attached_solver_count"] == 1


def test_study_inspect_rejects_a_non_study_path() -> None:
    model = build_model()
    expect_error("INVALID_NODE_PATH", call, "study.inspect", model, {"path": MESH_PATH})


def test_study_remove_refuses_while_a_solver_is_attached() -> None:
    model = build_model()
    error = expect_error("SOLVER_SEQUENCE_EXISTS", call, "study.remove", model,
                         {"path": {"segments": [{"collection": "study", "tag": "std2"}]}})
    assert "sol2" in str(error)
    assert "std2" in model.collections["study"].items


def test_study_remove_with_remove_solver_removes_both() -> None:
    model = build_model()
    result = call("study.remove", model, {
        "path": {"segments": [{"collection": "study", "tag": "std2"}]}, "remove_solver": True})
    assert result["status"] == "APPLIED"
    assert result["associated_solvers"] == ["sol2"]
    assert "std2" not in model.collections["study"].items
    assert "sol2" not in model.collections["sol"].items
    steps = {row["step"] for row in result["applied"]}
    assert steps == {"solver_remove", "study_remove"}
    assert "silent orphan" in result["solver_handling"]["policy"]


def test_study_step_create_registers_the_type_source() -> None:
    model = build_model()
    result = call("study.step_create", model, {
        "study": STUDY_PATH, "tag": "time", "type_id": "Transient",
        "properties": {"tlist": [0.0, 1.0]}})
    assert result["status"] == "APPLIED"
    assert result["type_readback"] == "Transient"
    assert "comsol_api_solver.51.81" in result["type_source"]
    assert result["generates_equations"] is True
    assert result["properties"]["tlist"]["data"] == [0.0, 1.0]
    assert "createAutoSequences" in result["solver_generation_note"]


def test_study_step_create_rejects_an_unverified_type() -> None:
    model = build_model()
    expect_error("API_UNSUPPORTED", call, "study.step_create", model, {
        "study": STUDY_PATH, "tag": "x", "type_id": "Teleport", "properties": {}})


def test_study_step_update_reports_the_solver_interaction() -> None:
    model = build_model()
    result = call("study.step_update", model,
                  {"path": study_step(model, "stat"), "properties": {"stol": 1e-5}})
    assert result["status"] == "APPLIED"
    assert result["properties"]["stol"]["data"] == 1e-5
    interaction = result["solver_sequence_interaction"]
    assert interaction["study"] == "std1"
    assert interaction["attached_sequences"] == ["sol1"]
    assert "edited by hand is preserved" in interaction["policy"]


def test_study_step_update_rejects_an_unknown_property() -> None:
    model = build_model()
    expect_error("INVALID_REQUEST", call, "study.step_update", model,
                 {"path": study_step(model, "stat"), "properties": {"teleport": True}})


def test_study_step_remove_reads_back_the_remaining_steps() -> None:
    model = build_model()
    result = call("study.step_remove", model, {"path": study_step(model, "stat")})
    assert result["status"] == "APPLIED"
    assert result["removed"] is True
    assert result["tag_readback_before"] == ["stat"]
    assert result["remaining_steps"] == []


def test_study_physics_activation_sets_and_verifies_solve_for() -> None:
    model = build_model()
    result = call("study.physics_activation", model, {
        "step": study_step(model, "stat"), "activation": {"physics": {"phys1": False}}})
    assert result["status"] == "APPLIED"
    assert result["applied"][0]["method"] == "setSolveFor"
    assert result["applied"][0]["readback"] is False
    step = model.collections["study"].items["std1"].collections["feature"].items["stat"]
    assert ("setSolveFor", ("phys1", False)) in step.calls
    assert step.solveFor("phys1") is False


def test_study_physics_activation_rejects_an_unknown_group() -> None:
    model = build_model()
    expect_error("INVALID_REQUEST", call, "study.physics_activation", model, {
        "step": study_step(model, "stat"), "activation": {"sparkles": {"phys1": True}}})


def test_study_solver_generate_refuses_to_relabel_an_attached_sequence() -> None:
    model = build_model()
    error = expect_error("SOLVER_SEQUENCE_EXISTS", call, "study.solver_generate", model,
                         {"study": STUDY_PATH, "replace_existing": False})
    assert "sol1" in str(error)
    assert "replace_existing" in str(error)


def test_study_solver_generate_requires_an_equation_generating_step() -> None:
    model = build_model()
    model.collections["study"].items["std1"].collections["feature"].items.clear()
    expect_error("INVALID_REQUEST", call, "study.solver_generate", model,
                 {"study": STUDY_PATH, "replace_existing": True})


def test_study_solver_generate_creates_the_attached_sequence() -> None:
    model = build_model()
    study = model.collections["study"].items["std1"]
    result = call("study.solver_generate", model, {"study": STUDY_PATH, "replace_existing": True})
    assert result["status"] == "APPLIED"
    assert result["auto_sequence_type"] == "sol"
    assert ("createAutoSequences", ("sol",)) in study.calls
    policy = result["manual_solver_policy"]
    assert "createAutoSequences" in policy["policy"]
    assert "comsol_api_general.47.60" in policy["source"]
    assert result["created"] == ["auto_std1_sol"]
    assert result["solver_sequences_before"] == ["sol1"]
    assert "auto_std1_sol" in result["solver_sequences_after"]


def test_study_run_records_duration_and_reads_the_computation_stamp() -> None:
    model = build_model()
    result = call("study.run", model, {"study": STUDY_PATH, "timeout_s": 30})
    assert result["status"] == "APPLIED"
    assert result["steps"] == ["stat"]
    assert result["computation_timestamp_changed"] is True
    assert result["computation_after"]["getLastComputationTime"] == 0.42
    assert result["requested_timeout_s"] == 30
    assert result["long_task_semantics"]["owner"] == "control_plane"
    assert result["requested_resources"] is None


def test_study_run_preserves_structured_worker_unknown_with_readable_timestamp() -> None:
    """A readable timestamp cannot clear the worker's explicit unknown bit."""
    model = build_model()
    study = model.collections["study"].items["std1"]
    study.raises["run"] = FakeEngineError(
        "FlException: inner worker execution is unresolved",
        code="ENGINE_CALL_FAILED",
        execution_state_unknown=True,
    )

    result = call("study.run", model, {"study": STUDY_PATH})

    assert result["status"] == "PARTIAL_FAILURE"
    assert result["ok"] is False
    assert result["execution_state_unknown"] is True
    assert result["engine_error"]["execution_state_unknown"] is True
    assert result["readback"]["readable"] is True
    assert result["computation_timestamp_changed"] is False


def test_study_run_rejects_a_study_without_steps() -> None:
    model = build_model()
    expect_error("INVALID_REQUEST", call, "study.run", model,
                 {"study": {"segments": [{"collection": "study", "tag": "std2"}]}})
    study = model.collections["study"].items["std2"]
    assert not any(name == "run" for name, _args in study.calls)


# ---------------------------------------------------------------------------
# solver
# ---------------------------------------------------------------------------


def test_solver_list_filters_by_study() -> None:
    model = build_model()
    result = call("solver.list", model, {})
    assert result["solver_count"] == 2
    assert result["solvers"][0]["study"] == "std1"
    assert result["association_semantics"].startswith("model.sol(<tag>).study()")
    filtered = call("solver.list", model, {"filter": {"study": "std1"}})
    assert [row["solver"] for row in filtered["solvers"]] == ["sol1"]


def test_solver_inspect_walks_the_nested_feature_tree() -> None:
    model = build_model()
    result = call("solver.inspect", model, {"path": SOLVER_PATH, "depth": 3})
    assert result["kind"] == "solver_sequence"
    assert result["study"] == "std1"
    assert result["is_empty"] is False
    assert result["sequence_type"] == "General"
    step = result["features"][0]
    assert step["type_id"] == "StudyStep"
    sub = step["children"][0]
    assert sub["type_id"] == "Stationary"
    assert [child["type_id"] for child in sub["children"]] == ["Direct", "FullyCoupled"]
    assert sub["type_source"].startswith("comsol_api_solver.51.")
    assert result["type_vocabulary"]["verified_count"] > 50


def test_solver_inspect_reads_a_single_feature_with_its_problems() -> None:
    model = build_model()
    result = call("solver.inspect", model, {"path": solver_step(model, "st1")})
    assert result["kind"] == "solver_feature"
    assert result["type_id"] == "StudyStep"
    assert result["settings"]["study"] == "std1"
    assert result["problem_nodes"] == []
    assert result["children_depth"] == 2


def test_solver_inspect_rejects_a_non_solver_path() -> None:
    model = build_model()
    expect_error("INVALID_NODE_PATH", call, "solver.inspect", model,
                 {"path": MESH_PATH})


def test_solver_create_links_the_study_and_reads_it_back() -> None:
    model = build_model()
    result = call("solver.create", model, {"tag": "sol3", "study": STUDY_PATH})
    assert result["status"] == "APPLIED"
    assert result["study_association_readback"] == "std1"
    assert "sol3" in result["tag_readback"]
    assert result["preexisting_study_sequences"] == ["sol1"]


def test_solver_create_rejects_an_existing_tag() -> None:
    model = build_model()
    expect_error("TAG_CONFLICT", call, "solver.create", model,
                 {"tag": "sol1", "study": STUDY_PATH})


def test_solver_create_rejects_an_unknown_study() -> None:
    model = build_model()
    expect_error("NODE_NOT_FOUND", call, "solver.create", model,
                 {"tag": "sol4", "study": {"segments": [{"collection": "study", "tag": "std9"}]}})


def test_solver_feature_create_supports_nested_types() -> None:
    model = build_model()
    result = call("solver.feature_create", model, {
        "parent": SOLVER_PATH, "tag": "t1", "type_id": "Time", "properties": {"tlist": [0.0, 2.0]}})
    assert result["status"] == "APPLIED"
    assert result["type_readback"] == "Time"
    assert "comsol_api_solver.51.51" in result["type_source"]

    nested = call("solver.feature_create", model, {
        "parent": solver_step(model, "st1"), "tag": "s2", "type_id": "Stationary", "properties": {}})
    assert nested["status"] == "APPLIED"
    assert nested["parent_path"]["segments"] == solver_step(model, "st1")["segments"]


def test_solver_feature_create_rejects_an_unverified_type() -> None:
    model = build_model()
    expect_error("API_UNSUPPORTED", call, "solver.feature_create", model,
                 {"parent": SOLVER_PATH, "tag": "x1", "type_id": "Quantum", "properties": {}})


def test_solver_feature_update_applies_and_reads_back() -> None:
    model = build_model()
    path = {"segments": list(SOLVER_PATH["segments"])
            + [{"collection": "feature", "tag": "st1"}, {"collection": "feature", "tag": "s1"}]}
    result = call("solver.feature_update", model, {"path": path, "properties": {"stol": 1e-6}})
    assert result["status"] == "APPLIED"
    assert result["properties"]["stol"]["data"] == 1e-6
    assert result["type_id"] == "Stationary"


def test_solver_feature_update_requires_at_least_one_property() -> None:
    model = build_model()
    expect_error("INVALID_REQUEST", call, "solver.feature_update", model,
                 {"path": solver_step(model, "st1"), "properties": {}})


def test_solver_feature_remove_reads_back() -> None:
    model = build_model()
    result = call("solver.feature_remove", model, {"path": solver_step(model, "st1")})
    assert result["status"] == "APPLIED"
    assert result["removed"] is True
    assert result["tag_readback_before"] == ["st1"]
    assert result["tag_readback_after"] == []


def test_solver_run_supports_all_and_feature_ranges() -> None:
    model = build_model()
    sol = model.collections["sol"].items["sol1"]
    all_run = call("solver.run", model, {"path": SOLVER_PATH})
    assert all_run["method"] == "runAll"
    assert ("runAll", ()) in sol.calls

    ranged = call("solver.run", model, {"path": SOLVER_PATH, "range": {"from": "st1", "to": "st1"}})
    assert ranged["method"] == "runFromTo"
    assert ranged["range"] == {"mode": "from_to", "from": "st1", "to": "st1"}
    assert sol.last_range == ("st1", "st1")


def test_solver_run_rejects_an_unknown_range_feature() -> None:
    model = build_model()
    error = expect_error("NODE_NOT_FOUND", call, "solver.run", model,
                         {"path": SOLVER_PATH, "range": {"feature": "ghost"}})
    assert "st1" in str(error)


def test_solver_run_reports_the_readback_only_when_readable() -> None:
    model = build_model()
    sol = model.collections["sol"].items["sol1"]
    sol.unavailable.update({"isEmpty", "isInitialized", "getDefaultSolnum", "hasProblems",
                            "getErrorMessage", "getInformationMessage", "getWarningMessage",
                            "getSequenceType"})
    result = call("solver.run", model, {"path": SOLVER_PATH})
    assert result["status"] == "DISPATCHED_UNVERIFIED"
    assert result["execution_state_unknown"] is True
    assert "isEmpty" in result["readback_allowlist_entry_required"]


# ---------------------------------------------------------------------------
# C06: solver NodePath conflicts and the mesh build preconditions
# ---------------------------------------------------------------------------


def test_solver_inspect_refusal_names_the_models_real_solver_paths() -> None:
    """A guessed solver tag is refused with this model's actual solver tags.

    The operation never assumes ``sol1``/``st1``: the refusal carries the solver
    sequences the model really exposes and the study each one is attached to, so
    a caller can correct the path from the readback instead of guessing again.
    """
    model = build_model()
    error = expect_error("NODE_NOT_FOUND", call, "solver.inspect", model,
                         {"path": {"segments": [{"collection": "component", "tag": "comp1"},
                                                {"collection": "study", "tag": "std1"},
                                                {"collection": "sol", "tag": "sol9"}]}})
    message = str(error)
    assert "sol:<tag>" in message
    assert "['sol1', 'sol2']" in message
    assert "study_scoped" in message and "'std1': ['sol1']" in message
    assert "study:<stag>" in message


def test_solver_inspect_refuses_a_study_feature_path_as_a_solver_feature() -> None:
    """A study feature path is not read as if it were a solver step.

    The input schema admits any NodePath, so the operation has to refuse the
    shape it does not mean instead of answering from a study node.
    """
    model = build_model()
    error = expect_error("INVALID_NODE_PATH", call, "solver.inspect", model,
                         {"path": {"segments": [{"collection": "study", "tag": "std1"},
                                                {"collection": "feature", "tag": "stat"}]}})
    message = str(error)
    assert "sol:<tag>/feature:<ftag>" in message
    assert "['sol1', 'sol2']" in message


def test_solver_inspect_remediation_reaches_a_study_relative_solver_path() -> None:
    """A study-relative solver path gets this model's real solver paths appended."""
    model = build_model()
    error = expect_error("NODE_NOT_FOUND", call, "solver.inspect", model,
                         {"path": {"segments": [{"collection": "component", "tag": "comp1"},
                                                {"collection": "study", "tag": "std1"},
                                                {"collection": "feature", "tag": "stat"}]}})
    assert "'std1': ['sol1']" in str(error)


def test_mesh_build_reads_back_the_geometry_it_is_bound_to() -> None:
    """The build records the geometry binding and the post-build counts/quality."""
    model = build_model()
    model.collections["component"].items["comp1"].collections["geom"].items["geom1"].state["problems"] = []
    result = call("mesh.build", model, {"path": MESH_PATH})
    assert result["status"] == "APPLIED"
    precondition = result["geometry_precondition"]
    assert precondition["geometry_tag"] == "geom1"
    assert precondition["geometry_exists"] is True
    assert precondition["geometry_problems"] == []
    assert precondition["geometry_problems_source"] == "geom(<tag>).problems()"
    post = result["post_build_readback"]
    assert post["elements"] is not None and post["vertices"] is not None
    assert post["min_quality"] is not None and post["mean_quality"] is not None


def test_mesh_build_refuses_a_geometry_that_is_not_present() -> None:
    """A sequence bound to a geometry the component does not have is refused."""
    model = build_model()
    model.collections["component"].items["comp1"].collections["mesh"].items["mesh1"].state["geom"] = "geom2"
    error = expect_error("NODE_NOT_FOUND", call, "mesh.build", model, {"path": MESH_PATH})
    assert "geom2" in str(error) and "create and build the geometry" in str(error)


def test_mesh_build_refuses_a_geometry_that_reports_problems() -> None:
    """A geometry that reports problems is not meshed as if it were fine."""
    model = build_model()
    model.collections["component"].items["comp1"].collections["geom"].items["geom1"].state["problems"] = [
        "Geometry could not be built: feature v1 is not supported",
    ]
    error = expect_error("EXECUTION_STATE_UNKNOWN", call, "mesh.build", model, {"path": MESH_PATH})
    assert "is not supported" in str(error)
