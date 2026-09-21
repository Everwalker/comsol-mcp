"""``result.sample_path``: the minimal result-sampling adapter.

The adapter is W16 acceptance infrastructure for the goal document's §8 chains A
and B, so these tests do three things that ordinary unit tests cannot do alone:

* drive the real operation code against a fake COMSOL results tree that mirrors
  the *verified* API surface (``model.result().numerical().create(tag,"Interp")``,
  ``PropFeature.set/setIndex/getString/getStringArray/getDoubleArray/getType``,
  ``NumericalFeature.getData/isComplex``, ``Results.dataset/.numerical/.tags/
  .remove``, ``Model.sol(<tag>).getPVals()/.study()``, ``Study.feature()`` and
  ``StudyFeature.getType()`` with the transient ``tlist``/``tunit``);
* feed the produced payload through the **real** acceptance driver
  (``tools/phase4_run_mcp.py``: ``_extract_samples``, ``_sample_series``,
  ``_check_chain_a``, ``_check_chain_b``) so the row contract is checked by the
  code that will consume it, not by a restatement of it;
* check every engine method name the implementation actually calls against the
  Java worker's ``METHODS``/``MODEL_UTIL`` allow-list parsed from
  ``worker_java/PersistentComsolWorker.java``, and check that the additions this
  module asked for are genuinely missing there (the Java worker is not modified
  by this task).
"""
from __future__ import annotations

from contextlib import contextmanager
import importlib.util
import json
import math
from pathlib import Path
import re
import sys
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

REPO_ROOT = Path(__file__).resolve().parents[1]
JAVA_WORKER = REPO_ROOT / "comsol_mcp" / "worker_java" / "PersistentComsolWorker.java"
DRIVER_PATH = REPO_ROOT / "tools" / "phase4_run_mcp.py"

#: Envelope fields the control plane owns; the domain layer accepts but ignores
#: them (``_g3_common.ENVELOPE_FIELDS``).
_ENVELOPE = {"model_ref": "m1", "session_id": "s1", "project_id": "p1", "idempotency_key": "k1"}


# ---------------------------------------------------------------------------
# fake engine (mirrors the verified API surface the adapter is allowed to use)
# ---------------------------------------------------------------------------


class FakeEngineError(RuntimeError):
    """Structured engine failure; ``code`` feeds ``error_code_of``/``_worker_failure_code``."""

    def __init__(self, message: str, *, code: str = "ENGINE_CALL_FAILED") -> None:
        super().__init__(message)
        self.code = code
        self.reply = {"ok": False, "code": code, "message": message}
        self.failure = {"code": code, "message": message}


def _refusal() -> FakeEngineError:
    return FakeEngineError("SecurityException: METHOD_REJECTED: not on the worker allow-list",
                           code="METHOD_REJECTED")


class Entity:
    """Common call/refusal bookkeeping for the fake list and node objects."""

    def __init__(self, *, unavailable: Sequence[str] = (), raises: Mapping[str, BaseException] | None = None) -> None:
        self.unavailable = set(unavailable)
        self.raises = dict(raises or {})
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def _guard(self, name: str, args: tuple[Any, ...]) -> None:
        self.calls.append((name, args))
        if name in self.unavailable:
            raise _refusal()
        if name in self.raises:
            raise self.raises[name]

    def methods(self) -> set[str]:
        return {name for name, _ in self.calls}


class FList(Entity):
    """Stand-in for a COMSOL ``ModelEntityList``."""

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


class FNode(Entity):
    """Fake COMSOL node: identity, typed property readback, collections."""

    def __init__(self, tag: str = "node", type_id: str = "Node", *,
                 props: Mapping[str, Any] | None = None, collections: Mapping[str, Any] | None = None,
                 **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.tag_ = tag
        self.type_id = type_id
        self.props: dict[str, Any] = dict(props or {})
        self.collections: dict[str, Any] = dict(collections or {})
        self.payloads: dict[str, Any] = {}

    # identity ------------------------------------------------------------
    def tag(self) -> str:
        self._guard("tag", ())
        return self.tag_

    def name(self) -> str:
        self._guard("name", ())
        return self.tag_

    def label(self, *args: Any) -> Any:
        self._guard("label", args)
        if args:
            self.tag_ = str(args[0])
            return None
        return self.tag_

    def getType(self) -> str:
        self._guard("getType", ())
        return self.type_id

    # property metadata / accessors ---------------------------------------
    def properties(self) -> list[str]:
        self._guard("properties", ())
        return sorted(self.props)

    def _read(self, name: str) -> Any:
        if name not in self.props:
            raise FakeEngineError(f"no such property {name!r}")
        return self.props[name]

    def getString(self, name: str) -> Any:
        self._guard("getString", (name,))
        return self._read(name)

    def getStringArray(self, name: str) -> Any:
        self._guard("getStringArray", (name,))
        return self._read(name)

    def getDoubleArray(self, name: str) -> Any:
        self._guard("getDoubleArray", (name,))
        return self._read(name)

    def getDoubleMatrix(self, name: str) -> Any:
        self._guard("getDoubleMatrix", (name,))
        return self._read(name)

    def set(self, name: str, value: Any) -> None:
        self._guard("set", (name, value))
        if isinstance(value, Mapping) and {"kind", "shape", "data"} <= set(value):
            self.payloads[name] = dict(value)
            self.props[name] = value["data"]
            return
        self.props[name] = value

    def setIndex(self, name: str, value: Any, index: int) -> None:
        self._guard("setIndex", (name, value, index))
        current = self.props.get(name)
        items = list(current) if isinstance(current, list) else []
        while len(items) <= int(index):
            items.append(None)
        items[int(index)] = value
        self.props[name] = items

    # collections ---------------------------------------------------------
    def _collection(self, name: str, args: tuple[Any, ...]) -> Any:
        self._guard(name, args)
        container = self.collections.get(name)
        if container is None:
            raise FakeEngineError(f"this node has no {name}()")
        if args:
            return container.get(str(args[0]))
        return container

    def __getattr__(self, name: str) -> Any:
        """Generate the collection accessors (``geom()``, ``feature()``, ...) a node exposes.

        Mirrors the COMSOL nodes: an accessor exists exactly when the fake node
        was registered with that collection, and every call is recorded so the
        allow-list test sees the real method name.
        """
        collections = self.__dict__.get("collections") or {}
        if name in collections:
            def accessor(*args: Any, _name: str = name) -> Any:
                return self._collection(_name, args)
            return accessor
        raise AttributeError(name)


class FModel(FNode):
    def result(self, *args: Any) -> Any:
        return self._collection("result", args)

    def component(self, *args: Any) -> Any:
        return self._collection("component", args)

    def sol(self, *args: Any) -> Any:
        return self._collection("sol", args)

    def study(self, *args: Any) -> Any:
        return self._collection("study", args)


class FResults(FNode):
    def dataset(self, *args: Any) -> Any:
        return self._collection("dataset", args)

    def numerical(self, *args: Any) -> Any:
        return self._collection("numerical", args)


class FSolver(FNode):
    def study(self, *args: Any) -> Any:  # SolverSequence.study() - no arguments
        return self._collection("study", args)

    def getPVals(self, *args: Any) -> Any:  # SolverSequence.getPVals() -> double[]
        self._guard("getPVals", args)
        return self._read("getPVals")


class FInterp(FNode):
    """The ephemeral ``numerical().create(<tag>,"Interp")`` feature."""

    def __init__(self, tag: str, type_id: str = "Interp", *, times: Sequence[float] | None = None,
                 complex_: bool = False, **kwargs: Any) -> None:
        kwargs.setdefault("props", {"data": None, "expr": [], "unit": [], "coord": None, "t": list(times) if times else None})
        super().__init__(tag=tag, type_id=type_id, **kwargs)
        self.complex_ = complex_
        self.values: Any = None

    def getData(self) -> Any:
        self._guard("getData", ())
        return self.values

    def isComplex(self) -> bool:
        self._guard("isComplex", ())
        return self.complex_

    def run(self) -> None:
        self._guard("run", ())


class Tree:
    """A wired fake model plus the handles the tests assert on."""

    def __init__(self, *, model: FModel, results_node: FResults, datasets: FList, numerical: FList,
                 solver: FSolver, studies: FList, components: FList, entities: list[Entity]) -> None:
        self.model = model
        self.results_node = results_node
        self.datasets = datasets
        self.numerical = numerical
        self.solver = solver
        self.studies = studies
        self.components = components
        self.entities = entities

    @property
    def worker(self) -> Any:
        return FWorker(self.model)

    def engine_methods(self) -> set[str]:
        called: set[str] = set()
        for entity in self.entities:
            called |= entity.methods()
        return called

    def interp(self) -> FInterp | None:
        """The last created ephemeral feature (it is removed again by the adapter)."""
        node = getattr(self, "last_interp", None)
        return node if isinstance(node, FInterp) else None


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


def build_tree(*, times: Sequence[float] | None = None, feature_times: Any = None,
               study_steps: Sequence[tuple[str, str]] = (("stat", "Stationary"),),
               dataset_props: Mapping[str, Any] | None = None, solver_values: Any = "auto",
               sdim: int = 3, length_unit: str = "m", interp_kwargs: Mapping[str, Any] | None = None,
               numerical_kwargs: Mapping[str, Any] | None = None, solver_unavailable: Sequence[str] = (),
               dataset_tags: Sequence[str] = ("dset1",), component_tags: Sequence[str] = ("comp1",),
               solution_tag: str = "sol1", complex_: bool = False) -> Tree:
    """Build a fake results tree with one dataset, one solution and one study."""
    entities: list[Entity] = []

    def register(entity: Entity) -> Entity:
        entities.append(entity)
        return entity

    geometry = register(FNode(tag="geom1", type_id="GeomSequence",
                              props={"lengthUnit": length_unit, "getSDim": sdim}))
    geometry.getSDim = lambda *args: geometry._guard("getSDim", args) or sdim  # type: ignore[assignment]
    geometry.lengthUnit = lambda *args: geometry._guard("lengthUnit", args) or length_unit  # type: ignore[assignment]

    geometries = register(FList())
    geometries.items["geom1"] = geometry

    component = register(FNode(tag="comp1", type_id="Component", collections={"geom": geometries}))
    components = register(FList(factory=lambda tag, *args: component))
    for tag in component_tags:
        components.items[tag] = component

    solver = register(FSolver(tag=solution_tag, type_id="SolverSequence",
                              props={"getPVals": solver_values}, unavailable=solver_unavailable))
    solvers = register(FList())
    solvers.items[solution_tag] = solver

    features = register(FList())
    for step_tag, step_type in study_steps:
        features.items[step_tag] = register(FNode(
            tag=step_tag, type_id=step_type,
            props={"tlist": list(times) if times else [0.0, 1.0], "tunit": "s"},
        ))
    study = register(FNode(tag="std1", type_id="Study", collections={"feature": features}))
    studies = register(FList())
    studies.items["std1"] = study
    solver.collections["study"] = FNode(tag="study-ref", props={"value": "std1"})

    def solver_study(*args: Any) -> Any:
        solver._guard("study", args)
        return "std1"

    solver.study = solver_study  # type: ignore[assignment]

    dataset = register(FNode(tag="dset1", type_id="Solution", props=dict(dataset_props or {
        "solution": solution_tag, "comp": "comp1", "geom": "geom1",
    })))
    datasets = register(FList())
    for tag in dataset_tags:
        datasets.items[tag] = dataset

    default_numerical_kwargs = {"unavailable": tuple((numerical_kwargs or {}).get("unavailable", ()))}
    numerical = register(FList(factory=lambda tag, *args: _make_interp(tag, *args),
                               unavailable=default_numerical_kwargs["unavailable"]))

    results_node = register(FResults(tag="results", type_id="Results",
                                     collections={"dataset": datasets, "numerical": numerical}))
    model = register(FModel(tag="Model", type_id="Model", collections={
        "result": results_node, "component": components, "sol": solvers, "study": studies,
    }))

    tree = Tree(model=model, results_node=results_node, datasets=datasets, numerical=numerical,
                solver=solver, studies=studies, components=components, entities=entities)
    tree._times = list(times) if times else None  # type: ignore[attr-defined]
    tree._feature_times = list(feature_times) if feature_times else feature_times  # type: ignore[attr-defined]
    tree._interp_kwargs = dict(interp_kwargs or {})  # type: ignore[attr-defined]
    tree._complex = complex_  # type: ignore[attr-defined]

    def _make_interp(tag: str, *args: Any) -> FInterp:
        node = FInterp(tag, times=tree._feature_times, complex_=tree._complex,  # type: ignore[attr-defined]
                       **tree._interp_kwargs)  # type: ignore[attr-defined]
        entities.append(node)
        tree.last_interp = node  # type: ignore[attr-defined]
        return node

    numerical.factory = _make_interp
    return tree


def default_values(expressions: Sequence[str] = ("T",), *, times: Sequence[float] | None = None,
                   profile: Any = None) -> Any:
    """A value matrix ``[expression][solnum][point]`` built from the coord payload.

    ``times`` is the set of *stored solutions* the fake engine returns: the
    matrix must have exactly one block per stored solution, because the adapter
    reads the time axis per stored solution.  ``tree_values`` below derives it
    from the tree so the fake data and the fake solution axis cannot disagree.
    """
    if profile is None:
        profile = lambda expression, index, x, time_value: 300.0 + 1000.0 * x  # noqa: E731

    def build(node: FInterp, coord: Any) -> Any:
        points = [list(column) for column in zip(*coord)] if coord else []
        solnums = list(times) if times else [0.0]
        out = []
        for expression_index, expression in enumerate(expressions):
            per_solution = []
            for time_value in solnums:
                per_solution.append(
                    [profile(expression, expression_index, point[0], time_value) for point in points]
                )
            out.append(per_solution)
        return out

    return build


def tree_values(tree: Tree, profile: Any = None, expressions: Sequence[str] = ("T",)) -> Any:
    """``default_values`` for ``tree``: one value block per stored solution it declares."""
    return default_values(expressions, times=tree._times, profile=profile)  # type: ignore[attr-defined]


@contextmanager
def install_values(builder: Any):
    """Hook the Interp computation to the coord payload the adapter sets."""
    original_set = FInterp.set

    def patched(self: FInterp, name: str, value: Any) -> None:
        original_set(self, name, value)
        if name == "coord":
            coord = value["data"] if isinstance(value, Mapping) else value
            if self.values is None:
                self.values = builder(self, coord)

    FInterp.set = patched  # type: ignore[assignment]
    try:
        yield
    finally:
        FInterp.set = original_set  # type: ignore[assignment]


def arguments(*, dataset: str = "dset1", solution: Any = "sol1", expressions: Sequence[str] = ("T",),
              start: Sequence[float] = (0.0, 0.005, 0.0025), end: Sequence[float] = (0.01, 0.005, 0.0025),
              samples: int = 21, **extra: Any) -> dict[str, Any]:
    spec: dict[str, Any] = {"expressions": expressions, "dataset": dataset, "solution": solution}
    spec.update(extra.pop("spec", {}))
    body: dict[str, Any] = {"spec": spec,
                            "path_definition": {"kind": "line", "start": list(start), "end": list(end),
                                                "samples": samples}}
    body.update(extra)
    return body


def call_sample(tree: Tree, body: Mapping[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
    payload = arguments(**kwargs) if body is None else dict(body)
    return results.sample_path(tree.worker, "Model", payload)


def expect_error(code: str, body: Mapping[str, Any]) -> ExecutionContractError:
    with pytest.raises(ExecutionContractError) as info:
        results.sample_path(FWorker(FModel(tag="Model")), "Model", dict(body))
    assert info.value.code == code, f"expected {code}, got {info.value.code}: {info.value}"
    return info.value


def x_of(point: Sequence[float]) -> float:
    return float(point[0])


# ---------------------------------------------------------------------------
# registry / wiring
# ---------------------------------------------------------------------------


def test_operation_is_published_with_the_catalogue_effect() -> None:
    assert results.OPERATIONS == {"result.sample_path": results.sample_path}
    assert IMPLEMENTED_OPERATIONS >= {"result.sample_path"}
    assert OPERATION_ORIGINS["result.sample_path"] == "_g3_results"
    assert EFFECTS["result.sample_path"] == "EVALUATE"
    assert "result.sample_path" in REQUIRES_ISOLATION
    assert DISPATCH["result.sample_path"] is results.sample_path


def test_aggregator_dispatch_reaches_the_adapter() -> None:
    tree = build_tree(study_steps=(("stat", "Stationary"),))
    values = tree_values(tree)
    with install_values(values):
        data = dispatch("result.sample_path", tree.worker, "Model", arguments(samples=3))
    assert data["sample_count"] == 3
    assert data["status"]["status"] == "APPLIED"


def test_unknown_operation_is_refused_by_the_aggregator() -> None:
    with pytest.raises(ExecutionContractError) as info:
        dispatch("result.sample_path_typo", FWorker(FModel(tag="Model")), "Model", {})
    assert info.value.code == "UNSUPPORTED_OPERATION"


def test_accepted_spec_keys_match_the_published_evaluation_spec() -> None:
    """``common.schema.json`` owns the spec vocabulary; the adapter must not invent keys.

    The only accepted keys outside ``EvaluationSpec`` are the flat ``dataset`` the
    W16 driver itself sends and the per-solution selection keys that are refused
    explicitly (``API_UNSUPPORTED``) rather than ignored.
    """
    schema = json.loads((REPO_ROOT / "docs" / "comsol_mcp_design_v1" / "common.schema.json")
                        .read_text(encoding="utf-8"))
    evaluation = schema["$defs"]["EvaluationSpec"]
    assert evaluation["additionalProperties"] is False
    published = set(evaluation["properties"])
    extras = set(results.ACCEPTED_SPEC_KEYS) - published
    assert extras == {"dataset"} | set(results.REFUSED_SPEC_KEYS)
    assert set(results.ACCEPTED_SOLUTION_SPEC_KEYS) == set(schema["$defs"]["SolutionSpec"]["properties"])
    assert "dataset" in schema["$defs"]["SolutionSpec"]["required"]


# ---------------------------------------------------------------------------
# argument validation (nothing is written before the refusal)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("body", [
    {"spec": {"expressions": ["T"], "dataset": "dset1", "solution": "sol1"}},
    {"path_definition": {"kind": "line", "start": [0, 0, 0], "end": [1, 1, 1], "samples": 2}},
    {"spec": {}, "path_definition": {"kind": "line", "start": [0, 0, 0], "end": [1, 1, 1], "samples": 2}},
])
def test_missing_required_fields_are_invalid_requests(body: Mapping[str, Any]) -> None:
    tree = build_tree()
    with pytest.raises(ExecutionContractError) as info:
        call_sample(tree, body)
    assert info.value.code == "INVALID_REQUEST"
    assert tree.numerical.calls == []
    assert tree.numerical.items == {}


def test_unknown_argument_key_is_refused() -> None:
    body = arguments()
    body["unexpected"] = 1
    expect_error("INVALID_REQUEST", body)


def test_unknown_spec_field_is_refused() -> None:
    expect_error("INVALID_REQUEST", arguments(spec={"mystery": 1}))


@pytest.mark.parametrize("expressions", [[], ["T", "T"], ["T", 1], "T"])
def test_bad_expression_lists_are_refused(expressions: Any) -> None:
    expect_error("INVALID_REQUEST", arguments(expressions=expressions))


def test_too_many_expressions_are_refused() -> None:
    expressions = [f"e{index}" for index in range(results.MAX_EXPRESSIONS + 1)]
    expect_error("INVALID_REQUEST", arguments(expressions=expressions))


@pytest.mark.parametrize("kind", ["arc", "curve", "polyline", "spiral", ""])
def test_unsupported_path_kinds_are_refused_before_any_engine_call(kind: str) -> None:
    tree = build_tree()
    body = arguments()
    body["path_definition"]["kind"] = kind
    with pytest.raises(ExecutionContractError) as info:
        call_sample(tree, body)
    assert info.value.code == "INVALID_REQUEST"
    assert tree.numerical.calls == []


@pytest.mark.parametrize("samples", [0, 1, -1, 1.5, "5", True, results.MAX_SAMPLE_POINTS + 1])
def test_invalid_sample_counts_are_refused(samples: Any) -> None:
    expect_error("INVALID_REQUEST", arguments(samples=samples))


@pytest.mark.parametrize("start,end", [
    ([0.0, 0.0], [1.0, 1.0, 1.0]),
    ([0.0, 0.0, 0.0], [1.0, 1.0, 1.0, 1.0]),
    ([], [1.0, 1.0, 1.0]),
    ("000", [1.0, 1.0, 1.0]),
    ([0.0, "x", 0.0], [1.0, 1.0, 1.0]),
    ([0.0, float("nan"), 0.0], [1.0, 1.0, 1.0]),
    ([0.0, 0.0, 0.0], [0.0, 0.0, 0.0]),
])
def test_bad_path_coordinates_are_refused(start: Any, end: Any) -> None:
    expect_error("INVALID_REQUEST", arguments(start=start, end=end))


def test_missing_dataset_is_refused() -> None:
    body = arguments()
    del body["spec"]["dataset"]
    expect_error("INVALID_REQUEST", body)


def test_units_count_must_match_the_expression_count() -> None:
    expect_error("INVALID_REQUEST", arguments(spec={"units": ["K", "K"]}))


@pytest.mark.parametrize("spec", [
    {"selection": {"kind": "all"}},
    {"aggregate": "integral"},
    {"complex_mode": "phase"},
    {"weight_expression": "1"},
    {"storage": "artifact"},
    {"inner": [1, 2]},
    {"time": [{"value": 1.0, "unit": "s"}]},
])
def test_unimplemented_evaluation_spec_options_are_refused(spec: Mapping[str, Any]) -> None:
    expect_error("API_UNSUPPORTED", arguments(spec=spec))


def test_aggregate_none_and_storage_inline_are_accepted() -> None:
    tree = build_tree()
    with install_values(tree_values(tree)):
        data = call_sample(tree, arguments(spec={"aggregate": "none", "complex_mode": "real", "storage": "inline"},
                                           samples=3))
    assert data["sample_count"] == 3


def test_solution_may_be_given_as_a_solution_spec_object() -> None:
    tree = build_tree()
    body = arguments()
    body["spec"]["solution"] = {"dataset": "dset1", "solution": "sol1"}
    with install_values(tree_values(tree)):
        data = call_sample(tree, body, samples=3)
    assert data["dataset"] == "dset1"
    assert data["solution"] == "sol1"


def test_solution_spec_object_with_unsupported_inner_is_refused() -> None:
    body = arguments()
    body["spec"]["solution"] = {"dataset": "dset1", "solution": "sol1", "inner": "first"}
    expect_error("API_UNSUPPORTED", body)


@pytest.mark.parametrize("expression", ["x", "y", "z", "solnum", "t", "time", "t "])
def test_expression_names_that_collide_exactly_with_an_owned_column_are_refused(expression: str) -> None:
    """These names are written by the adapter itself, so a value column would overwrite them."""
    tree = build_tree(times=[0.0, 1.0], study_steps=(("time", "Transient"),))
    with install_values(tree_values(tree)):
        with pytest.raises(ExecutionContractError) as info:
            call_sample(tree, arguments(expressions=[expression], samples=3))
    assert info.value.code == "INVALID_REQUEST"
    assert tree.numerical.calls == [], "the refusal must happen before the ephemeral write"


def test_temperature_expression_is_accepted_on_a_time_dependent_dataset() -> None:
    """Chain B samples ``T`` on a transient dataset; a case-folded rule would break it."""
    tree, profile = transient_tree()
    with install_values(tree_values(tree, profile)):
        data = call_sample(tree, samples=3)
    assert data["sample_count"] == 9
    assert "T" in data["samples"][0]


def test_case_variant_expression_collisions_are_refused() -> None:
    expect_error("INVALID_REQUEST", arguments(expressions=["T", "t_2", "T_2"]))


# ---------------------------------------------------------------------------
# identity resolution (still before the ephemeral write)
# ---------------------------------------------------------------------------


def test_unknown_dataset_is_refused_before_creating_anything() -> None:
    tree = build_tree()
    with pytest.raises(ExecutionContractError) as info:
        call_sample(tree, arguments(dataset="dset9"))
    assert info.value.code == "NODE_NOT_FOUND"
    assert tree.numerical.calls == []


def test_unknown_solution_is_refused_before_creating_anything() -> None:
    tree = build_tree()
    body = arguments()
    body["spec"]["solution"] = "sol7"
    tree.datasets.items["dset1"].props["solution"] = "sol7"
    with install_values(tree_values(tree)):
        with pytest.raises(ExecutionContractError) as info:
            call_sample(tree, body, samples=3)
    assert info.value.code == "NODE_NOT_FOUND"
    assert tree.numerical.calls == []


def test_dataset_and_solution_mismatch_is_refused() -> None:
    tree = build_tree()
    with pytest.raises(ExecutionContractError) as info:
        call_sample(tree, arguments(solution="sol2"))
    assert info.value.code == "INVALID_REQUEST"
    assert tree.numerical.calls == []


def test_path_dimension_must_match_the_geometry() -> None:
    tree = build_tree(sdim=3)
    with pytest.raises(ExecutionContractError) as info:
        call_sample(tree, arguments(start=(0.0, 0.0), end=(0.01, 0.0), samples=3))
    assert info.value.code == "INVALID_REQUEST"
    assert tree.numerical.calls == []


def test_a_dataset_without_solution_property_needs_an_explicit_solution() -> None:
    tree = build_tree(dataset_props={"comp": "comp1", "geom": "geom1"})
    with pytest.raises(ExecutionContractError) as info:
        call_sample(tree, arguments(solution=None))
    assert info.value.code == "INVALID_REQUEST"


def test_dataset_without_solution_property_uses_an_explicit_solution() -> None:
    tree = build_tree(dataset_props={"comp": "comp1", "geom": "geom1"}, study_steps=(("stat", "Stationary"),))
    with install_values(tree_values(tree)):
        data = call_sample(tree, arguments(samples=3))
    assert data["solution"] == "sol1"
    assert data["sample_count"] == 3


def test_component_and_geometry_fall_back_to_the_single_component() -> None:
    tree = build_tree(dataset_props={})
    with install_values(tree_values(tree)):
        data = call_sample(tree, arguments(samples=3))
    assert data["points"]["coordinate_unit"] == "m"
    assert data["points"]["space_dimension"] == 3


# ---------------------------------------------------------------------------
# steady-state sampling (chain A shape)
# ---------------------------------------------------------------------------


def steady_tree(*, samples: int = 21, t0: float = 300.0, t1: float = 400.0, length: float = 0.01) -> Tree:
    def profile(expression: str, index: int, x: float, time_value: float) -> float:
        return t0 + (t1 - t0) * (x / length)

    return build_tree(study_steps=(("stat", "Stationary"),)), profile


def test_steady_state_rows_have_coordinates_and_no_time_column() -> None:
    tree, profile = steady_tree()
    with install_values(tree_values(tree, profile)):
        data = call_sample(tree, samples=21)
    rows = data["samples"]
    assert len(rows) == 21
    assert data["sample_count"] == 21
    assert set(rows[0]) == {"x", "y", "z", "solnum", "T"}
    assert "t" not in rows[0] and "time" not in rows[0]
    assert data["time_values"] is None and data["time_steps"] is None
    assert data["solution_axis"]["time_dependent"] is False
    assert [row["x"] for row in rows] == pytest.approx(
        [0.01 * index / 20 for index in range(21)])
    assert all(row["y"] == 0.005 and row["z"] == 0.0025 for row in rows)
    assert all(row["solnum"] == 1 for row in rows)
    assert rows[-1]["T"] == pytest.approx(400.0)


def test_steady_state_coordinate_payload_is_a_row_major_double_matrix() -> None:
    tree, profile = steady_tree()
    with install_values(tree_values(tree, profile)):
        call_sample(tree, samples=3)
    interp = tree.interp()
    assert interp is not None
    payload = interp.payloads["coord"]
    assert payload["java_signature"] == "double[][]"
    assert payload["kind"] == "float64"
    assert payload["shape"] == [3, 3]
    assert payload["data"][0] == [0.0, 0.005, 0.01]      # x row first
    assert payload["data"][1] == [0.005, 0.005, 0.005]   # y row
    assert payload["data"][2] == [0.0025, 0.0025, 0.0025]
    assert interp.props["expr"] == ["T"]
    assert interp.props["data"] == "dset1"


def test_units_echo_the_model_length_unit_and_the_engine_expression_unit() -> None:
    tree, profile = steady_tree()
    interp_unit: dict[str, Any] = {}

    def builder(node: FInterp, coord: Any) -> Any:
        node.props["unit"] = ["K"]
        return tree_values(tree, profile)(node, coord)

    with install_values(builder):
        data = call_sample(tree, samples=3)
    assert data["units"]["x"] == "m"
    assert data["units"]["y"] == "m"
    assert data["units"]["z"] == "m"
    assert data["units"]["T"] == "K"
    assert data["points"]["coordinate_unit"] == "m"


def test_requested_expression_units_are_written_with_set_index() -> None:
    tree, profile = steady_tree()
    with install_values(tree_values(tree, profile)):
        call_sample(tree, spec={"units": ["K"]}, samples=3)
    interp = tree.interp()
    assert interp is not None
    assert interp.props["unit"] == ["K"]
    assert ("setIndex", ("unit", "K", 0)) in interp.calls


def test_ephemeral_feature_is_created_read_and_removed() -> None:
    tree, profile = steady_tree()
    with install_values(tree_values(tree, profile)):
        data = call_sample(tree, samples=21)
    assert tree.numerical.items == {}
    assert data["cleanup"] == {"tag": "cmssp1", "type_id": "Interp", "created": True, "removed": True,
                               "verified_removed": True, "cleanup_failed": False, "error": None}
    assert data["status"]["status"] == "APPLIED"
    assert data["status"]["execution_state_unknown"] is False
    assert data["ephemeral_feature"]["tag"] == "cmssp1"
    assert data["ephemeral_feature"]["property_readback"]["data"] == "dset1"


def test_a_second_call_uses_the_next_free_ephemeral_tag() -> None:
    tree, profile = steady_tree()
    tree.numerical.items["cmssp1"] = FInterp("cmssp1")
    with install_values(tree_values(tree, profile)):
        data = call_sample(tree, samples=3)
    assert data["cleanup"]["tag"] == "cmssp2"
    assert set(tree.numerical.items) == {"cmssp1"}


def test_complex_data_is_flagged_and_the_real_part_is_returned() -> None:
    tree, profile = steady_tree()
    tree._complex = True  # type: ignore[attr-defined]
    with install_values(tree_values(tree, profile)):
        data = call_sample(tree, samples=3)
    assert data["complex"] is True
    assert data["complex_mode"] == "real"
    assert data["samples"][0]["T"] == pytest.approx(300.0)


# ---------------------------------------------------------------------------
# transient sampling (chain B shape)
# ---------------------------------------------------------------------------


def transient_tree(*, times: Sequence[float] = (0.0, 0.5, 1.0), t0: float = 300.0, delta: float = 20.0,
                   length: float = 0.01, alpha: float = 1e-3, solver_values: Any = "auto",
                   feature_times: Any = "auto", study_steps: Sequence[tuple[str, str]] = (("time", "Transient"),),
                   solver_unavailable: Sequence[str] = ()) -> tuple[Tree, Any]:
    def profile(expression: str, index: int, x: float, time_value: float) -> float:
        return t0 + delta * math.sin(math.pi * x / length) * math.exp(
            -alpha * (math.pi / length) ** 2 * time_value)

    tree = build_tree(times=times, study_steps=study_steps,
                      solver_values=list(times) if solver_values == "auto" else solver_values,
                      solver_unavailable=solver_unavailable,
                      feature_times=list(times) if feature_times == "auto" else feature_times)
    return tree, profile


def test_transient_rows_span_every_stored_solution_and_carry_the_time_column() -> None:
    tree, profile = transient_tree()
    with install_values(tree_values(tree, profile)):
        data = call_sample(tree, samples=5)
    rows = data["samples"]
    assert len(rows) == 15  # 3 stored solutions x 5 path points
    assert data["time_steps"] == 3
    assert data["time_values"] == [0.0, 0.5, 1.0]
    assert set(rows[0]) == {"x", "y", "z", "solnum", "time", "T"}
    assert rows[0]["time"] == 0.0
    assert rows[5]["time"] == 0.5
    # C07a: the case-ambiguous 't' alias is gone; the column contract is the
    # published, order-independent way to resolve keys.
    roles = data["roles"]
    assert roles["time"] == "time"
    assert roles["solution_index"] == "solnum"
    assert roles["expressions"] == {"T": "T"}
    assert any(column["name"] == "time" and column["role"] == "time" for column in data["columns"])
    assert any(column["name"] == "T" and column["role"] == "expression" for column in data["columns"])
    assert [row["solnum"] for row in rows] == [1] * 5 + [2] * 5 + [3] * 5
    assert data["units"]["time"] == "s"
    assert data["solution_axis"]["time_dependent"] is True
    assert data["solution_axis"]["source"] == "numerical_feature.t"


def test_transient_time_axis_can_come_from_get_pvals() -> None:
    tree, profile = transient_tree(feature_times=None)
    with install_values(tree_values(tree, profile)):
        data = call_sample(tree, samples=3)
    assert data["solution_axis"]["source"] == "solver_sequence.getPVals"
    assert data["time_values"] == [0.0, 0.5, 1.0]


def test_transient_time_axis_falls_back_to_the_declared_output_times() -> None:
    tree, profile = transient_tree(feature_times=None, solver_values=None)
    with install_values(tree_values(tree, profile)):
        data = call_sample(tree, samples=3)
    assert data["solution_axis"]["source"] == "study_step.tlist"
    assert data["solution_axis"]["status"] == "declared"
    assert data["time_values"] == [0.0, 0.5, 1.0]


def test_transient_dataset_without_a_readable_axis_publishes_no_time_column() -> None:
    tree, profile = transient_tree(feature_times=None, solver_values=None)
    tree.studies.items["std1"].collections["feature"].items["time"].props["tlist"] = None
    with install_values(tree_values(tree, profile)):
        data = call_sample(tree, samples=3)
    assert data["time_values"] is None
    assert data["time_steps"] is None
    assert all("t" not in row and "time" not in row for row in data["samples"])
    assert data["solution_axis"]["status"] == "unavailable"
    assert "no time column is published" in " ".join(data["notes"])


def test_multi_step_study_uses_the_feature_time_property_and_records_the_ambiguity() -> None:
    tree, profile = transient_tree(study_steps=(("stat", "Stationary"), ("time", "Transient")))
    with install_values(tree_values(tree, profile)):
        data = call_sample(tree, samples=3)
    assert data["solution_axis"]["study_steps"] == [{"tag": "stat", "type": "Stationary"},
                                                    {"tag": "time", "type": "Transient"}]
    assert data["solution_axis"]["source"] == "numerical_feature.t"
    assert data["solution_axis"]["time_dependent"] is True


def test_multi_step_study_without_a_feature_time_property_is_unavailable() -> None:
    tree, profile = transient_tree(study_steps=(("stat", "Stationary"), ("time", "Transient")),
                                  feature_times=None)
    with install_values(tree_values(tree, profile)):
        data = call_sample(tree, samples=3)
    assert data["solution_axis"]["status"] == "unavailable"
    assert "cannot attribute" in (data["solution_axis"]["reason"] or "")
    assert data["time_values"] is None


def test_stationary_step_with_a_feature_time_property_records_the_conflict() -> None:
    tree, profile = transient_tree(study_steps=(("stat", "Stationary"),))
    with install_values(tree_values(tree, profile)):
        data = call_sample(tree, samples=3)
    assert data["solution_axis"]["time_dependent"] is False
    assert data["solution_axis"]["conflicts"], "the contradictory t readback must be recorded"
    assert data["time_values"] is None


# ---------------------------------------------------------------------------
# failure and cleanup reporting
# ---------------------------------------------------------------------------


def test_get_data_refused_by_the_worker_is_reported_with_its_allowlist_entry() -> None:
    tree, profile = steady_tree()
    tree._interp_kwargs["unavailable"] = ("getData",)  # type: ignore[attr-defined]
    with install_values(tree_values(tree, profile)):
        data = call_sample(tree, samples=3)
    assert data["samples"] == []
    assert data["status"]["ok"] is False
    assert data["status"]["status"] in {"FAILED", "EXECUTION_STATE_UNKNOWN"}
    assert data["status"]["engine_error"]["code"] in {"ENGINE_CALL_FAILED", "API_UNSUPPORTED"}
    assert data["allowlist_entry_required"] == ["getData"]
    assert tree.numerical.items == {}, "the ephemeral node must still be removed"


def test_an_unknown_expression_rejected_by_the_engine_is_reported_not_invented() -> None:
    """COMSOL refuses an unknown expression; the adapter must report it, never fill rows."""
    tree, profile = steady_tree()
    tree._interp_kwargs["raises"] = {  # type: ignore[attr-defined]
        "getData": FakeEngineError("COMSOL: Unknown variable U", code="ENGINE_CALL_FAILED"),
    }
    with install_values(tree_values(tree, profile)):
        data = call_sample(tree, arguments(expressions=["U"], samples=3))
    assert data["samples"] == []
    assert data["sample_count"] == 0
    assert data["status"]["ok"] is False
    assert data["status"]["status"] in {"FAILED", "EXECUTION_STATE_UNKNOWN"}
    assert data["status"]["engine_error"]["code"] == "ENGINE_CALL_FAILED"
    assert "getData" in data["status"]["engine_error"]["message"]
    assert any(item["method"] == "getData" and item["code"] == "ENGINE_CALL_FAILED" for item in data["read_errors"])
    assert tree.numerical.items == {}, "the ephemeral node is removed even when getData fails"


def test_non_finite_sample_values_are_refused_instead_of_published() -> None:
    tree, profile = steady_tree()
    values = tree_values(tree, lambda expression, index, x, time_value: float("nan"))
    with install_values(values):
        data = call_sample(tree, samples=3)
    assert data["samples"] == []
    assert data["status"]["engine_error"]["code"] == "EXECUTION_STATE_UNKNOWN"
    assert "not finite" in data["status"]["engine_error"]["message"]
    assert tree.numerical.items == {}


def test_a_shorter_coordinate_vector_is_refused() -> None:
    tree, profile = steady_tree()

    def builder(node: FInterp, coord: Any) -> Any:
        return [[[1.0, 2.0]]]  # 2 values for 3 requested points

    with install_values(builder):
        data = call_sample(tree, samples=3)
    assert data["samples"] == []
    assert "coordinates for 3 requested path points" in data["status"]["engine_error"]["message"]
    assert tree.numerical.items == {}


def test_a_removal_failure_is_reported_as_an_unknown_execution_state() -> None:
    tree, profile = steady_tree()
    tree.numerical.unavailable = {"remove"}
    with install_values(tree_values(tree, profile)):
        data = call_sample(tree, samples=3)
    assert data["cleanup"]["cleanup_failed"] is True
    assert data["cleanup"]["verified_removed"] is False
    assert data["status"]["execution_state_unknown"] is True
    assert data["status"]["status"] == "EXECUTION_STATE_UNKNOWN"
    assert data["status"]["partial_change"] is True
    assert data["engine_error"] is None, "the samples were read; only the cleanup failed"
    assert data["samples"], "the real samples are still reported next to the cleanup failure"
    assert "cmssp1" in tree.numerical.items, "the leftover node is visible to the caller"
    assert any(item["method"] == "remove" for item in data["read_errors"])


def test_a_create_failure_is_reported_without_claiming_a_change() -> None:
    tree, profile = steady_tree()
    tree.numerical.unavailable = {"create"}
    with install_values(tree_values(tree, profile)):
        data = call_sample(tree, samples=3)
    assert data["cleanup"]["created"] is False
    assert data["status"]["status"] == "FAILED"
    assert data["status"]["engine_error"]["code"] in {"ENGINE_CALL_FAILED", "API_UNSUPPORTED"}
    assert "result.numerical.create(Interp)" in data["status"]["not_executed"]
    assert data["samples"] == []


def test_a_create_that_fails_but_still_creates_is_reconciled_and_removed() -> None:
    tree, profile = steady_tree()

    original_create = tree.numerical.create

    def failing_create(tag: str, *args: Any) -> Any:
        node = original_create(tag, *args)
        raise FakeEngineError("ENGINE_CALL_FAILED: connection lost after create")

    tree.numerical.create = failing_create  # type: ignore[assignment]
    with install_values(tree_values(tree, profile)):
        data = call_sample(tree, samples=3)
    assert data["cleanup"]["created"] is True
    assert data["cleanup"]["reconciled"] is True
    assert data["cleanup"]["verified_removed"] is True
    assert tree.numerical.items == {}


# ---------------------------------------------------------------------------
# the acceptance driver consumes the payload (chain A and chain B)
# ---------------------------------------------------------------------------

_driver_cache: Any = None


def driver_module() -> Any:
    global _driver_cache
    if _driver_cache is not None:
        return _driver_cache
    if not DRIVER_PATH.is_file():
        pytest.skip("the phase4 acceptance driver is not present in this tree")
    spec = importlib.util.spec_from_file_location("phase4_driver_for_g3_results", DRIVER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    _driver_cache = module
    return module


def envelope(data: Mapping[str, Any]) -> dict[str, Any]:
    """The ActionResult the control plane would wrap around the data."""
    return {"success": True, "data": dict(data), "operation": "result.sample_path"}


def chain_a_reference(driver: Any, tmp_path: Path) -> dict[str, Any]:
    """The driver's own chain A reference, completed with the keys its check reads.

    ``tools/phase4_run_mcp.py`` belongs to the acceptance harness and is not
    edited by this task.  It now publishes ``t0_k``/``length_m`` top-level (the
    two keys ``_check_chain_a()`` reads directly); this helper still completes
    them defensively from the same ``CHAIN_A`` config if an older driver copy
    omits them - nothing about the sampled data changes either way.
    """
    reference = driver._chain_a_reference(tmp_path)
    assert "temperature_difference_k" in reference and "relative_error_limit" in reference
    if "t0_k" not in reference or "length_m" not in reference:
        config = reference["config"]
        reference = {**reference, "t0_k": config["t0_k"], "length_m": config["length_m"]}
        reference["completed_keys"] = ["t0_k", "length_m"]
    return reference


def test_driver_extracts_the_sample_table_and_its_columns() -> None:
    driver = driver_module()
    tree, profile = transient_tree()
    with install_values(tree_values(tree, profile)):
        data = call_sample(tree, samples=5)
    rows = driver._extract_samples(envelope(data))
    assert len(rows) == 15
    x_series = driver._sample_series(rows, ("x", "x_m", "x [m]"))
    t_series = driver._sample_series(rows, ("t", "time", "t_s", "time [s]"))
    temperature_series = driver._sample_series(rows, ("T", "T_k", "T [K]", "temp"))
    assert x_series == pytest.approx([0.0, 0.0025, 0.005, 0.0075, 0.01] * 3)
    assert t_series == [0.0] * 5 + [0.5] * 5 + [1.0] * 5
    assert temperature_series == pytest.approx(
        [profile("T", 0, x, time_value) for time_value in (0.0, 0.5, 1.0)
         for x in (0.0, 0.0025, 0.005, 0.0075, 0.01)])


def test_chain_a_check_passes_on_a_steady_payload(tmp_path: Path) -> None:
    driver = driver_module()
    reference = chain_a_reference(driver, tmp_path)
    config = driver.CHAIN_A
    count = int(config["sample_count"])
    delta = float(config["t1_k"]) - float(config["t0_k"])

    def profile(expression: str, index: int, x: float, time_value: float) -> float:
        return float(config["t0_k"]) + delta * (x / float(config["length_m"]))

    tree = build_tree(study_steps=(("stat", "Stationary"),))
    with install_values(tree_values(tree, profile)):
        data = call_sample(tree, samples=count)
    verdict = driver._check_chain_a(envelope(data), reference)
    assert verdict[0] == "PASS", verdict
    assert verdict[2]["samples"] == count
    assert verdict[2]["max_relative_error"] == pytest.approx(0.0, abs=1e-12)
    assert data["time_values"] is None and "time" not in data["samples"][0]


def test_chain_b_check_passes_on_a_transient_payload(tmp_path: Path) -> None:
    driver = driver_module()
    reference = driver._chain_b_reference(tmp_path)
    config = driver.CHAIN_B
    times = [float(value) for value in config["time_points_s"]]
    length = float(config["length_m"])
    delta = float(reference["temperature_difference_k"])
    alpha = float(reference["alpha_m2_s"])
    t0 = float(reference["t0_k"])

    def profile(expression: str, index: int, x: float, time_value: float) -> float:
        return t0 + delta * math.sin(math.pi * x / length) * math.exp(
            -alpha * (math.pi / length) ** 2 * time_value)

    tree, _ = transient_tree(times=times, t0=t0, delta=delta, length=length, alpha=alpha)
    with install_values(tree_values(tree, profile)):
        data = call_sample(tree, samples=5)
    verdict = driver._check_chain_b(envelope(data), reference)
    assert verdict[0] == "PASS", verdict
    assert verdict[2]["normalized_max_error"] == pytest.approx(0.0, abs=1e-12)
    assert data["time_values"] == times
    assert isinstance(data.get("time_values"), list) and len(data["time_values"]) >= 2


def test_chain_a_column_contract_survives_the_drivers_case_insensitive_lookup(tmp_path: Path) -> None:
    """The driver resolves row keys case-insensitively; ``T`` must win over ``t``/``time``."""
    driver = driver_module()
    reference = chain_a_reference(driver, tmp_path)
    config = driver.CHAIN_A
    count = int(config["sample_count"])
    delta = float(config["t1_k"]) - float(config["t0_k"])

    def profile(expression: str, index: int, x: float, time_value: float) -> float:
        return float(config["t0_k"]) + delta * (x / float(config["length_m"]))

    tree = build_tree(study_steps=(("stat", "Stationary"),))
    with install_values(tree_values(tree, profile)):
        data = call_sample(tree, samples=count)
    row = data["samples"][0]
    assert list(row) == ["x", "y", "z", "solnum", "T"], "column order is part of the contract"
    verdict = driver._check_chain_a(envelope(data), reference)
    assert verdict[0] == "PASS", verdict


# ---------------------------------------------------------------------------
# worker allow-list
# ---------------------------------------------------------------------------


def java_allow_lists() -> tuple[set[str], set[str]]:
    text = JAVA_WORKER.read_text(encoding="utf-8")
    methods_block = re.search(r"METHODS = new HashSet<>\(Arrays\.asList\((.*?)\)\);", text, re.S)
    modelutil_block = re.search(r"MODEL_UTIL = new HashSet<>\(Arrays\.asList\((.*?)\)\);", text, re.S)
    assert methods_block is not None and modelutil_block is not None
    extract = lambda block: set(re.findall(r'"([^"]+)"', block))  # noqa: E731
    return extract(methods_block.group(1)), extract(modelutil_block.group(1))


def test_every_engine_method_the_adapter_calls_is_on_the_worker_allow_list() -> None:
    methods, modelutil = java_allow_lists()
    allowed = methods | modelutil
    tree, profile = steady_tree()
    with install_values(tree_values(tree, profile)):
        call_sample(tree, samples=3)
    transient, transient_profile = transient_tree()
    with install_values(tree_values(transient, transient_profile)):
        call_sample(transient, samples=3)
    called = tree.engine_methods() | transient.engine_methods()
    # the fake's own bookkeeping helpers are not engine methods
    called -= {"_read"}
    unexpected = sorted(name for name in called if name not in allowed)
    assert unexpected == [], f"engine methods outside the worker allow-list: {unexpected}"


@contextmanager
def install_coordinate_readback(matrix: Any) -> Any:
    """Publish ``getCoordinates()`` on the ephemeral Interp for one test.

    The method is *added* for the duration of the block, exactly like the
    engine would publish it once the worker allow-list carries it: with no
    method at all the adapter reports ``UNAVAILABLE`` (the real state of the
    Java worker, see ``ALLOWLIST_ADDITIONS``), so the tests cannot fake
    availability by a base-class stub that returns ``None``.
    """
    original = FInterp.__dict__.get("getCoordinates", None)
    absent = object()
    sentinel = absent if original is None else original

    def getCoordinates(self: FInterp) -> Any:
        self._guard("getCoordinates", ())
        return matrix

    FInterp.getCoordinates = getCoordinates  # type: ignore[attr-defined]
    try:
        yield
    finally:
        if sentinel is absent:
            del FInterp.getCoordinates  # type: ignore[attr-defined]
        else:
            FInterp.getCoordinates = sentinel  # type: ignore[attr-defined]


def path_matrix(start: Sequence[float], end: Sequence[float], samples: int) -> list[list[float]]:
    """``[coordinate][point]`` for the evenly spaced path the adapter requests."""
    points = [[value + (end[axis] - value) * index / (samples - 1) for axis, value in enumerate(start)]
              for index in range(samples)]
    return [[point[axis] for point in points] for axis in range(len(start))]


def test_the_time_column_alias_t_is_refused_while_T_stays_legal() -> None:
    """``T`` and the removed ``t`` alias differ only in case; only ``T`` is an expression."""
    tree, profile = transient_tree()
    with install_values(tree_values(tree, profile)):
        data = call_sample(tree, samples=3)
    assert "T" in data["samples"][0]
    assert "t" not in data["samples"][0] and "time" in data["samples"][0]
    assert data["roles"]["time"] == "time"
    with pytest.raises(ExecutionContractError) as info:
        call_sample(tree, arguments(expressions=["t"], samples=3))
    assert info.value.code == "INVALID_REQUEST"


@pytest.mark.parametrize("expression", ["X", "Y", "Z", "Solnum", "Time", "TIME", " TIME "])
def test_case_variants_of_published_columns_are_refused_before_the_write(expression: str) -> None:
    """A case-insensitive reader would pick whichever key came first in the JSON object."""
    tree, profile = steady_tree()
    with install_values(tree_values(tree, profile)):
        with pytest.raises(ExecutionContractError) as info:
            call_sample(tree, arguments(expressions=[expression], samples=3))
    assert info.value.code == "INVALID_REQUEST"
    assert tree.numerical.calls == [], "the refusal must happen before the ephemeral write"


def test_published_row_keys_are_pairwise_distinct_under_case_folding() -> None:
    """The order-independence invariant: no two keys of a row fold to the same string."""
    tree, profile = transient_tree()
    with install_values(tree_values(tree, profile)):
        data = call_sample(tree, samples=3)
    for row in data["samples"]:
        assert len({key.lower() for key in row}) == len(row), row
    assert [column["name"] for column in data["columns"]] == ["x", "y", "z", "solnum", "time", "T"]
    assert [column["role"] for column in data["columns"]] == ["coordinate", "coordinate", "coordinate",
                                                             "solution_index", "time", "expression"]


@pytest.mark.parametrize("reorder", [
    lambda row: dict(row),
    lambda row: dict(reversed(list(row.items()))),
    lambda row: dict(sorted(row.items())),
])
def test_columns_and_roles_locate_T_t_and_time_under_any_row_key_order(reorder: Any) -> None:
    """Row order is not part of the contract: the column map is."""
    tree, profile = transient_tree()
    with install_values(tree_values(tree, profile)):
        data = call_sample(tree, samples=3)
    roles = data["roles"]
    columns = {column["role"]: column["name"] for column in data["columns"]}
    rows = [reorder(row) for row in data["samples"]]
    assert roles["time"] == columns["time"] == "time"
    assert roles["expressions"]["T"] == columns["expression"] == "T"
    assert [row[roles["time"]] for row in rows] == [0.0] * 3 + [0.5] * 3 + [1.0] * 3
    assert [row[roles["expressions"]["T"]] for row in rows] == pytest.approx(
        [profile("T", 0, x, time_value) for time_value in (0.0, 0.5, 1.0) for x in (0.0, 0.005, 0.01)])


def test_sort_keys_serialisation_keeps_every_column_resolvable() -> None:
    """``json.dumps(..., sort_keys=True)`` must not change what any column means."""
    tree, profile = transient_tree()
    with install_values(tree_values(tree, profile)):
        data = call_sample(tree, spec={"units": ["K"]}, samples=3)
    before = {key: [row[key] for row in data["samples"]] for key in data["samples"][0]}
    payload = json.loads(json.dumps(data, sort_keys=True))
    assert sorted(payload["samples"][0]) == sorted(before), "the key set survived the round trip"
    for row in payload["samples"]:
        assert list(row) == sorted(row), "sort_keys really reordered the object"
        for key, series in before.items():
            assert row[key] == before[key][payload["samples"].index(row)]
    # The consumer resolves through the column contract, not through key order.
    roles = payload["roles"]
    assert [row[roles["time"]] for row in payload["samples"]] == [0.0] * 3 + [0.5] * 3 + [1.0] * 3
    assert payload["units"]["time"] == "s" and payload["units"]["T"] == "K"


def test_the_driver_resolves_a_sort_keys_payload_through_the_column_contract() -> None:
    """The real consumer keeps working after a ``sort_keys`` re-serialisation.

    The driver's ``_sample_series()`` resolves a row key case-insensitively, so a
    candidate set that folds two published keys together (``"t"`` would match the
    expression ``"T"``) is inherently order-dependent.  That is exactly why the
    adapter publishes **one** time key (``time``) and refuses case-only
    collisions: with unambiguous candidate names the driver works under any key
    order, which is what this test pins.
    """
    driver = driver_module()
    tree, profile = transient_tree()
    with install_values(tree_values(tree, profile)):
        data = call_sample(tree, samples=3)
    payload = json.loads(json.dumps(data, sort_keys=True))
    rows = driver._extract_samples(envelope(payload))
    assert driver._sample_series(rows, ("time", "time [s]")) == [0.0] * 3 + [0.5] * 3 + [1.0] * 3
    assert driver._sample_series(rows, ("T", "T_k")) == pytest.approx(
        [profile("T", 0, x, time_value) for time_value in (0.0, 0.5, 1.0) for x in (0.0, 0.005, 0.01)])
    assert driver._sample_series(rows, ("x", "x_m")) == pytest.approx([0.0, 0.005, 0.01] * 3)
    # The removed alias is what makes the table order-dependent for that reader:
    # after a sorted round trip "T" comes before "time", so a "t" candidate
    # resolves the temperature series as if it were the time axis.
    assert driver._sample_series(rows, ("t",)) == [row["T"] for row in payload["samples"]]
    assert list(payload["samples"][0])[0] == "T", "the sorted payload really puts T first"


def test_coordinate_readback_unavailable_is_reported_with_the_missing_allowlist_entry() -> None:
    """The Java worker does not publish ``getCoordinates()``: that is ``UNAVAILABLE``, not a pass."""
    tree, profile = steady_tree()
    with install_values(tree_values(tree, profile)):
        data = call_sample(tree, samples=3)
    readback = data["coordinate_readback"]
    assert readback["status"] == "UNAVAILABLE"
    assert readback["attempted"] is True
    assert readback["coordinates"] is None and readback["max_deviation"] is None
    assert readback["allowlist_entry_required"] == "getCoordinates"
    assert readback["method"] == "result.numerical(<tag>).getCoordinates()"
    assert readback["reason"], "the refusal reason is recorded, never hidden"
    assert readback["error_code"] == "ENGINE_CALL_FAILED"
    assert data["verification_status"] == "NOT_RUN"
    assert data["verification"]["axis"] == "coordinate_readback"
    assert data["verification"]["detail"] == readback
    assert data["status"]["readback_match"] is None
    assert data["status"]["status"] == "APPLIED", "the sample read itself succeeded"
    assert any(item["method"] == "getCoordinates" for item in data["read_errors"])
    assert "getCoordinates" not in data["allowlist_additions"]
    assert data["coordinate_source"].startswith("the requested path_definition points")


def test_a_worker_allowlist_refusal_of_get_coordinates_names_the_missing_entry() -> None:
    """A ``METHOD_REJECTED`` refusal is what the live worker answers: name the entry."""
    tree, profile = steady_tree()

    @contextmanager
    def refusing() -> Any:
        def getCoordinates(self: FInterp) -> Any:
            self._guard("getCoordinates", ())
            raise _refusal()
        FInterp.getCoordinates = getCoordinates  # type: ignore[attr-defined]
        try:
            yield
        finally:
            del FInterp.getCoordinates  # type: ignore[attr-defined]

    with refusing():
        with install_values(tree_values(tree, profile)):
            data = call_sample(tree, samples=3)
    assert data["coordinate_readback"]["status"] == "UNAVAILABLE"
    assert data["coordinate_readback"]["error_code"] == "METHOD_REJECTED"
    assert data["coordinate_readback"]["allowlist_entry_required"] == "getCoordinates"
    assert data["allowlist_entry_required"] == ["getCoordinates"]
    assert tuple(data["read_errors"][-1][name] for name in ("method", "code", "allowlist_entry_required")) == \
        ("getCoordinates", "METHOD_REJECTED", "getCoordinates")
    assert data["verification_status"] == "NOT_RUN"
    assert data["status"]["ok"] is True
    assert tree.numerical.items == {}, "the ephemeral node is still removed"


def test_coordinate_readback_verified_marks_the_verification_passed() -> None:
    tree, profile = steady_tree()
    matrix = path_matrix((0.0, 0.005, 0.0025), (0.01, 0.005, 0.0025), 3)
    with install_coordinate_readback(matrix):
        with install_values(tree_values(tree, profile)):
            data = call_sample(tree, samples=3)
    readback = data["coordinate_readback"]
    assert readback["status"] == "VERIFIED"
    assert readback["coordinates"] == matrix
    assert readback["max_deviation"] == 0.0
    assert readback["allowlist_entry_required"] is None
    assert readback["reason"] is None
    assert data["verification_status"] == "PASSED"
    assert data["status"]["readback_match"] is True
    assert data["status"]["ok"] is True
    assert "result.numerical.getCoordinates(verified)" in data["status"]["applied"]
    assert data["status"]["failed"] == []


def test_coordinate_readback_mismatch_fails_verification_and_stays_in_status_failed() -> None:
    """The engine's own coordinates disagree: a failed *verification*, not a silent difference."""
    tree, profile = steady_tree()
    matrix = path_matrix((0.0, 0.005, 0.0025), (0.01, 0.005, 0.0025), 3)
    shifted = [[matrix[0][0] + 1e-3] + matrix[0][1:], matrix[1], matrix[2]]
    with install_coordinate_readback(shifted):
        with install_values(tree_values(tree, profile)):
            data = call_sample(tree, samples=3)
    readback = data["coordinate_readback"]
    assert readback["status"] == "MISMATCH"
    assert readback["max_deviation"] == pytest.approx(1e-3)
    assert readback["reason"] and "differs from the requested path points" in readback["reason"]
    assert readback["allowlist_entry_required"] is None
    assert data["verification_status"] == "FAILED"
    assert data["status"]["ok"] is False
    assert data["status"]["readback_match"] is False
    assert data["status"]["status"] == "PARTIAL_FAILURE"
    assert data["status"]["partial_change"] is True
    codes = [item["code"] for item in data["status"]["failed"]]
    assert "VERIFICATION_FAILED" in codes
    assert data["samples"], "the samples are still published next to the failed verification"
    assert data["status"]["engine_error"] is None, "the samples were read; only the verification failed"
    assert data["ephemeral_feature"]["property_readback"]["data"] == "dset1"


def test_coordinate_readback_with_the_wrong_shape_is_a_mismatch_with_its_own_code() -> None:
    tree, profile = steady_tree()
    with install_coordinate_readback([[0.0, 0.005], [0.005, 0.005], [0.0025, 0.0025]]):
        with install_values(tree_values(tree, profile)):
            data = call_sample(tree, samples=3)
    assert data["coordinate_readback"]["status"] == "MISMATCH"
    assert data["coordinate_readback"]["error_code"] == "EXECUTION_STATE_UNKNOWN"
    assert "coordinates for 3 path points" in data["coordinate_readback"]["reason"]
    assert data["verification_status"] == "FAILED"


def test_binding_block_binds_dataset_solution_geometry_and_the_stored_solution_axis() -> None:
    """The sampling must be attributable: dataset, solution, component, geometry and times."""
    tree, profile = transient_tree()
    with install_values(tree_values(tree, profile)):
        data = call_sample(tree, spec={"units": ["K"]}, samples=5)
    assert data["binding"] == {
        "dataset": "dset1",
        "solution": "sol1",
        "component": "comp1",
        "geometry": "geom1",
        "length_unit": "m",
        "space_dimension": 3,
        "content_context": "dataset.comp/dataset.geom",
        "stored_solutions": 3,
        "time_axis": {"time_dependent": True, "status": "verified", "source": "numerical_feature.t",
                      "unit": "s", "values": [0.0, 0.5, 1.0]},
        "revision_required": False,
    }
    assert data["binding"]["time_axis"]["values"] == data["time_values"]
    assert data["points"]["count"] == 5 and data["points"]["dimension"] == 3
    assert data["points"]["coordinate_unit"] == "m" and data["points"]["space_dimension"] == 3


def test_binding_records_the_content_context_that_resolved_component_and_geometry() -> None:
    tree = build_tree(dataset_props={"comp": None, "geom": None})
    with install_values(tree_values(tree)):
        data = call_sample(tree, samples=3)
    assert data["binding"]["component"] == "comp1" and data["binding"]["geometry"] == "geom1"
    assert data["binding"]["content_context"] == "single component/geometry of the model"
    tree, profile = transient_tree(feature_times=None, solver_values=None)
    with install_values(tree_values(tree, profile)):
        declared = call_sample(tree, samples=3)
    assert declared["binding"]["time_axis"]["status"] == "declared"
    assert declared["binding"]["time_axis"]["source"] == "study_step.tlist"
    assert declared["binding"]["stored_solutions"] == 3


def test_unit_readback_names_every_unit_source_and_never_converts() -> None:
    tree, profile = transient_tree()
    with install_values(tree_values(tree, profile)):
        data = call_sample(tree, spec={"units": ["K"]}, samples=3)
    assert data["unit_readback"] == {
        "expression_units": {"T": "K"},
        "coordinate_unit": "m",
        "time_unit": "s",
        "source": "model.result().numerical(<tag>).getStringArray(\"unit\") and GeomSequence.lengthUnit()",
    }
    assert data["units"] == {"x": "m", "y": "m", "z": "m", "T": "K", "solnum": "one-based index", "time": "s"}
    assert data["expression_units"] == {"T": "K"}
    assert [column["unit"] for column in data["columns"]] == ["m", "m", "m", "one-based index", "s", "K"]


def test_unit_readback_reports_an_unknown_expression_unit_as_null_instead_of_guessing() -> None:
    tree, profile = steady_tree()
    with install_values(tree_values(tree, profile)):
        data = call_sample(tree, samples=3)
    assert data["unit_readback"]["expression_units"] == {"T": None}
    # The time unit is the *study step's* declared unit (``tunit``); no time
    # column is published for a steady dataset, so the unit is reported without
    # being attached to a column and no time value is invented.
    assert data["unit_readback"]["time_unit"] == "s"
    assert data["time_values"] is None and data["time_steps"] is None
    assert "time" not in data["units"] and all("time" not in row for row in data["samples"])
    assert data["units"]["solnum"] == "one-based index"
    assert [column["unit"] for column in data["columns"] if column["name"] == "T"] == [None]
    assert [column["name"] for column in data["columns"]] == ["x", "y", "z", "solnum", "T"]


def test_the_reported_allowlist_additions_are_really_missing() -> None:
    methods, modelutil = java_allow_lists()
    allowed = methods | modelutil
    missing = [name for name in results.ALLOWLIST_ADDITIONS if name in allowed]
    assert missing == [], f"these additions already exist and must be used instead of reported: {missing}"
    assert "getData" in methods, "getData is the sampling primitive and must stay allow-listed"
    assert "set" in methods and "setIndex" in methods and "getPVals" in methods


def test_the_available_but_unused_allowlist_entries_really_exist() -> None:
    methods, modelutil = java_allow_lists()
    allowed = methods | modelutil
    unknown = [name for name in results.ALLOWLIST_AVAILABLE_UNUSED if name not in allowed]
    assert unknown == [], f"these entries are not on the worker allow-list after all: {unknown}"


def test_allowlist_findings_and_unverified_paths_are_reported_in_the_operation_data() -> None:
    tree, profile = steady_tree()
    with install_values(tree_values(tree, profile)):
        data = call_sample(tree, samples=3)
    assert data["allowlist_additions"] == list(results.ALLOWLIST_ADDITIONS)
    assert data["allowlist_available_unused"] == list(results.ALLOWLIST_AVAILABLE_UNUSED)
    assert any(item["path"].startswith("coordinate readback") for item in data["unverified_paths"])
    assert any("SolutionInfo" in item["path"] or "solution" in item["path"] for item in data["unverified_paths"])
    assert isinstance(data["read_errors"], list) and isinstance(data["allowlist_entry_required"], list)
