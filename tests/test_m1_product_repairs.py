"""M1 product-side repairs (fix A), from the ``g3_1-m1`` live evidence.

Live evidence: ``evidence/phase4_1/runs/20260920T235502Z-g3_1-m1``.

* **W13_T006** ``variable.group_create`` answered a missing ``comp1`` with
  ``NODE_NOT_FOUND`` *and* ``EXECUTION_STATE_UNKNOWN`` ("the callback did not
  declare a pre-dispatch stage"), even though the dispatch witness saw no
  mutation-class engine call.  The lookup-only refusals now declare the
  ``validation`` stage; the refusal semantics (code, "nothing was written") are
  unchanged and the witness still has to agree.
* **W13_T015** ``physics.feature_update`` accepted ``1e5[W/m^2]`` into the
  volumetric ``HeatSource.Q0`` (documented SI unit ``W/m^3``) without a word.
  ``comsol_mcp._g3_units`` now compares the declared ``[unit]`` against the
  write point's documented dimension and refuses a *proven* mismatch before the
  write; unitless and unresolvable expressions are recorded and admitted.
* **GUARD_T033** ``evaluate_expressions`` reported ``ephemeral_mutation: true``
  without publishing which temporary nodes it owned or whether their cleanup was
  verified.  The inventory is now part of the reply, and a failed cleanup
  reaches the shared C01 ``UNKNOWN`` through ``cleanup.cleanup_failed``.
* **GUARD_T010** the retry/identity line compares the product's own
  ``execution.operation_id``: a verbatim replay under the same idempotency key
  must return the *same* identity without re-executing, and the same key with a
  different body must be answered with ``IDEMPOTENCY_CONFLICT``.
* **m1e W13_T006** ``evaluate_expressions`` read ``[]`` for the component
  variables ``q1``/``q2``: the model publishes no dataset, so the feature's
  documented *First compatible dataset* default resolved to nothing, and the
  engine's *global parameter* evaluator then refused every spelling
  (``FlException: Unknown_model_parameter``) - a component variable is not a
  parameter of that collection.  The results node now carries the documented
  ``data`` state explicitly (*``none``* when the engine's own dataset readback
  proves there is no dataset, the named dataset otherwise), so the results route
  resolves the variable itself; the reply publishes the route, the readback, the
  engine's own messages and the corpus citations, and an empty read still fails
  truthfully.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Mapping

import pytest

from comsol_mcp import _g3_w13 as w13
from comsol_mcp import _g3_w15 as w15
from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._domain_outcome import STATE_UNKNOWN, final_state
from comsol_mcp._execution_contract import (
    ExecutionContractError,
    PreWriteRefusal,
    SessionLedger,
)
from comsol_mcp._execution_service import ExecutionService
from comsol_mcp._managed_backend import ManagedBackend
from comsol_mcp._state import ToolExecutionError

try:  # pragma: no cover - import shim for direct execution
    from tests.test_g3_w13 import FList, FModel, expect_error as w13_expect_error, param_model, rows, worker_for
    from tests.test_g3_w14 import call as w14_call, expect_error as w14_expect_error, world as w14_world
    from tests.test_g3_w15 import (
        FComponent,
        FFeature,
        build_model,
        expect_error as w15_expect_error,
        feature_path,
        physics_node,
        worker_for as w15_worker_for,
    )
except ImportError:  # pragma: no cover
    from test_g3_w13 import (  # type: ignore[no-redef]
        FList,
        FModel,
        expect_error as w13_expect_error,
        param_model,
        rows,
        worker_for,
    )
    from test_g3_w14 import call as w14_call, expect_error as w14_expect_error, world as w14_world  # type: ignore[no-redef]
    from test_g3_w15 import (  # type: ignore[no-redef]
        FComponent,
        FFeature,
        build_model,
        expect_error as w15_expect_error,
        feature_path,
        physics_node,
        worker_for as w15_worker_for,
    )

PHYSICS_PATH: dict[str, Any] = {
    "segments": [
        {"collection": "component", "tag": "comp1"},
        {"collection": "physics", "tag": "ht"},
    ]
}
MATERIAL_PATH: dict[str, Any] = {
    "segments": [
        {"collection": "component", "tag": "comp1"},
        {"collection": "material", "tag": "mat1"},
    ]
}


# ---------------------------------------------------------------------------
# 1. NODE_NOT_FOUND: the refusal declares the pre-dispatch stage
# ---------------------------------------------------------------------------


def test_missing_component_refusal_declares_the_validation_stage():
    """W13_T006: the refusal keeps NODE_NOT_FOUND *and* proves its stage."""
    model = FModel(collections={"component": FList(arity=1), "variable": FList(arity=1)})
    with pytest.raises(ExecutionContractError) as info:
        w13.variable_group_create(worker_for(model), "Model", {"tag": "v1", "component": "comp1"})
    refusal = info.value
    assert isinstance(refusal, PreWriteRefusal)
    assert refusal.code == "NODE_NOT_FOUND"
    assert refusal.stage == "validation"
    assert "comp1" in str(refusal)


def test_the_other_lookup_only_refusals_declare_the_stage_too():
    """Every site the sweep converted declares the same explicit stage."""
    model, _collection = param_model()
    with pytest.raises(PreWriteRefusal) as w13_param:
        w13.parameter_list(worker_for(model), "Model", {"group": "missing"})
    assert w13_param.value.code == "NODE_NOT_FOUND"

    material_model = build_model()
    missing_material = {"segments": [
        {"collection": "component", "tag": "comp1"},
        {"collection": "material", "tag": "nope"},
    ]}
    with pytest.raises(PreWriteRefusal) as w15_material:
        w15.material_remove(w15_worker_for(material_model), "Model", {"path": missing_material})
    assert w15_material.value.code == "NODE_NOT_FOUND"

    physics_model = build_model()
    with pytest.raises(PreWriteRefusal) as w15_feature:
        w15.physics_feature_update(w15_worker_for(physics_model), "Model", {
            "path": feature_path(("component", "comp1"), ("physics", "ht"), ("feature", "nope")),
            "properties": {"Tinit": "300[K]"},
        })
    assert w15_feature.value.code == "NODE_NOT_FOUND"
    assert w15_feature.value.stage == "validation"


def test_a_refusal_with_the_stage_and_a_clean_witness_is_published_as_a_refusal():
    model = FModel(collections={"component": FList(arity=1), "variable": FList(arity=1)})
    worker = worker_for(model)
    envelope = ManagedBackend._dispatch_with_witness(
        "variable.group_create",
        lambda: w13.variable_group_create(worker, "Model", {"tag": "v1", "component": "comp1"}),
        effect="project_write",
    )
    assert envelope["success"] is False
    assert envelope["error"]["code"] == "NODE_NOT_FOUND"
    assert envelope["data"]["status"] == "REFUSED"
    assert envelope["data"]["dispatch_stage"] == "validation"
    assert envelope["data"]["witness"]["mutation_issued"] is False
    assert envelope["execution_state_unknown"] is False


def test_a_witness_that_saw_a_mutation_keeps_the_fail_closed_unknown():
    """The stage declaration alone is never the proof."""
    from comsol_mcp._domain_outcome import record_engine_method

    model = FModel(collections={"component": FList(arity=1), "variable": FList(arity=1)})
    worker = worker_for(model)

    def call() -> Mapping[str, Any]:
        record_engine_method("create")  # what a mutation-class engine call looks like
        return w13.variable_group_create(worker, "Model", {"tag": "v1", "component": "comp1"})

    with pytest.raises(ExecutionContractError) as info:
        ManagedBackend._dispatch_with_witness("variable.group_create", call, effect="project_write")
    assert info.value.code == "EXECUTION_STATE_UNKNOWN"
    assert info.value.stage == "post_dispatch"
    assert info.value.details["unproven_pre_dispatch"] is True
    assert info.value.details["cause_code"] == "NODE_NOT_FOUND"


def test_a_raise_without_the_stage_stays_unknown():
    """The pre-fix shape (no declared stage) is still the live M1 failure."""
    def call() -> Mapping[str, Any]:
        raise ExecutionContractError("NODE_NOT_FOUND", "component 'comp1' does not exist")

    with pytest.raises(ExecutionContractError) as info:
        ManagedBackend._dispatch_with_witness("variable.group_create", call, effect="project_write")
    assert info.value.code == "EXECUTION_STATE_UNKNOWN"
    assert info.value.details["unproven_pre_dispatch"] is False
    assert "did not declare a pre-dispatch stage" in str(info.value)


def test_every_node_not_found_raise_in_the_package_declares_the_stage():
    """Package-wide guard: a new raise must not silently lose the stage again.

    The one non-declaring site is the property-group *input removal* in
    ``_g3_w15``: its loop runs after an ``addInput`` may already have happened,
    so it cannot prove a pre-dispatch raise and must stay fail-closed.
    """
    import pathlib

    root = pathlib.Path(w13.__file__).resolve().parent
    offenders: list[tuple[str, str, list[str]]] = []
    declaring = 0
    for path in sorted(root.glob("_g3_*.py")):
        lines = path.read_text().splitlines()
        for index, line in enumerate(lines):
            if "node_not_found(" in line and "def node_not_found" not in line:
                declaring += 1
            if not line.strip().startswith('"NODE_NOT_FOUND"'):
                continue
            context = lines[max(0, index - 8):index + 3]
            if not any("raise ExecutionContractError(" in row for row in context):
                continue
            offenders.append((path.name, line.strip(), context))

    assert declaring >= 30, "the sweep must keep every lookup refusal in the helper"
    assert len(offenders) == 1, [row[0] for row in offenders]
    name, line, context = offenders[0]
    assert name == "_g3_w15.py"
    assert line == '"NODE_NOT_FOUND", f"property group input {name!r} does not exist"', line
    assert any("Deliberately *not* a PreWriteRefusal" in row for row in context), context


# ---------------------------------------------------------------------------
# 1b. TAG_CONFLICT: the "already exists" refusal declares the pre-dispatch stage
# ---------------------------------------------------------------------------


def test_the_existing_component_refusal_declares_the_validation_stage():
    """W13_T015 (M1c): ``create comp1`` on a model that already has it keeps TAG_CONFLICT + stage."""
    worker, model, _ = w14_world()
    before = list(model.collections["component"].mutation_calls())
    error = w14_expect_error("TAG_CONFLICT", w14_call, "definition.component_manage", worker,
                             {"action": "create", "tag": "comp1"})
    assert isinstance(error, PreWriteRefusal)
    assert error.stage == "validation"
    assert "comp1" in str(error)
    assert model.collections["component"].mutation_calls() == before


def test_the_dispatch_envelope_publishes_the_tag_conflict_as_a_refusal():
    """The M1c answer ("failed with TAG_CONFLICT ... did not declare a pre-dispatch stage") is gone.

    Same callback, same read-then-refuse existence check, but the refusal now reaches the wire as
    the REFUSED envelope the caller can retry/attribute instead of the fail-closed unknown that
    recorded the case as an unresolved engine state.
    """
    worker, _model, _ = w14_world()
    envelope = ManagedBackend._dispatch_with_witness(
        "definition.component_manage",
        lambda: w14_call("definition.component_manage", worker, {"action": "create", "tag": "comp1"}),
        effect="project_write",
    )
    assert envelope["success"] is False
    assert envelope["error"]["code"] == "TAG_CONFLICT"
    assert envelope["error"]["stage"] == "validation"
    assert envelope["data"]["status"] == "REFUSED" and envelope["data"]["refused"] is True
    assert envelope["data"]["dispatch_stage"] == "validation"
    assert envelope["data"]["witness"]["mutation_issued"] is False
    assert envelope["execution_state_unknown"] is False
    assert "did not declare a pre-dispatch stage" not in str(envelope["error"])


def test_a_tag_conflict_behind_a_mutation_keeps_the_fail_closed_unknown():
    """The stage declaration is never the proof: a witness that saw a mutation keeps the unknown."""
    from comsol_mcp._domain_outcome import record_engine_method

    worker, _model, _ = w14_world()

    def call_after_a_write() -> Mapping[str, Any]:
        record_engine_method("create")  # what a mutation-class engine call looks like
        return w14_call("definition.component_manage", worker, {"action": "create", "tag": "comp1"})

    with pytest.raises(ExecutionContractError) as info:
        ManagedBackend._dispatch_with_witness("definition.component_manage", call_after_a_write,
                                              effect="project_write")
    assert info.value.code == "EXECUTION_STATE_UNKNOWN"
    assert info.value.details["unproven_pre_dispatch"] is True
    assert info.value.details["cause_code"] == "TAG_CONFLICT"


def test_the_other_existence_refusals_declare_the_stage_too():
    """Representatives of the swept sites: physics/material creates and a parameter rename."""
    physics_model = build_model()
    error = w15_expect_error("TAG_CONFLICT", w15.physics_create, w15_worker_for(physics_model), "Model", {
        "component": "comp1", "tag": "ht", "type_id": "HeatTransferInSolids", "geometry": "geom1",
    })
    assert isinstance(error, PreWriteRefusal) and error.stage == "validation"

    material_model = build_model()
    error = w15_expect_error("TAG_CONFLICT", w15.material_create, w15_worker_for(material_model), "Model", {
        "component": "comp1", "tag": "mat1", "type_id": "Common",
    })
    assert isinstance(error, PreWriteRefusal) and error.stage == "validation"

    param, _collection = param_model(default=rows(a="1", b="2"))
    error = w13_expect_error("TAG_CONFLICT", w13.parameter_group_manage, worker_for(param), "Model",
                            {"action": "rename", "tag": "a", "arguments": {"new_name": "b"}})
    assert isinstance(error, PreWriteRefusal) and error.stage == "validation"


def test_the_multi_geometry_physics_refusal_proves_its_stage_too():
    """M1c #61: ``API_UNSUPPORTED`` for a multi-geometry component is a pre-write judgement."""
    component = FComponent(materials={}, physics={})
    component.geom_list.items["geom2"] = FFeature(tag="geom2", type_id="GeomSequence3D",
                                                  geometry="geom2", values={"lengthunit": "m"})
    model = build_model(component=component)
    before = list(component.physics_list.calls)

    def create() -> Mapping[str, Any]:
        return w15.physics_create(w15_worker_for(model), "Model", {
            "component": "comp1", "tag": "ht", "type_id": "HeatTransferInSolids", "geometry": "geom1"})

    error = w15_expect_error("API_UNSUPPORTED", create)
    assert isinstance(error, PreWriteRefusal) and error.stage == "validation"
    assert "no geometry-tag overload" in str(error)
    assert component.physics_list.calls == before

    envelope = ManagedBackend._dispatch_with_witness("physics.create", create, effect="project_write")
    assert envelope["error"]["code"] == "API_UNSUPPORTED"
    assert envelope["data"]["refused"] is True and envelope["data"]["dispatch_stage"] == "validation"
    assert envelope["data"]["witness"]["mutation_issued"] is False


def test_every_tag_conflict_raise_in_the_package_declares_the_stage():
    """Package-wide guard: a new "already exists" raise must not silently lose the stage again.

    Every site reads the owning container's tag list and refuses before its first
    mutation-class engine call, so all of them go through ``tag_conflict``.  The one
    non-declaring site is the property-group *input* loop in ``_g3_w15``: its
    ``addInput`` may already have happened when the loop reaches a later tag, so it
    cannot prove a pre-dispatch raise and must stay fail-closed.
    """
    import pathlib

    root = pathlib.Path(w13.__file__).resolve().parent
    offenders: list[tuple[str, str, list[str]]] = []
    declaring = 0
    for path in sorted(root.glob("_g3_*.py")):
        lines = path.read_text().splitlines()
        for index, line in enumerate(lines):
            if "tag_conflict(" in line and "def tag_conflict" not in line:
                declaring += 1
            if not line.strip().startswith('"TAG_CONFLICT"'):
                continue
            context = lines[max(0, index - 6):index + 3]
            if not any("raise ExecutionContractError(" in row for row in context):
                continue
            offenders.append((path.name, line.strip(), context))

    assert declaring >= 17, "the sweep must keep every existence refusal in the helper"
    assert len(offenders) == 1, [row[0] for row in offenders]
    name, line, context = offenders[0]
    assert name == "_g3_w15.py"
    assert "already exists" in line, line
    assert any("Deliberately *not* a PreWriteRefusal" in row for row in context), context


# ---------------------------------------------------------------------------
# 2. volumetric heat source: unit-dimension guard
# ---------------------------------------------------------------------------


def heat_source_model() -> tuple[Any, Any]:
    """A model with a 3D ``HeatSource`` feature carrying the documented unit."""
    feature = FFeature(
        tag="hs1", type_id="HeatSource", value_types={"Q0": "String"},
        values={"Q0": "1e6[W/m^3]"}, entities=[1, 2], dim=3, geometry="geom1",
    )
    interface = physics_node(tag="ht", features={"hs1": feature})
    component = FComponent(materials={}, physics={"ht": interface})
    return build_model(component=component), feature


def test_the_volumetric_heat_source_refuses_an_area_unit_before_the_write():
    """W13_T015: ``1e5[W/m^2]`` must never reach ``HeatSource.Q0``."""
    model, feature = heat_source_model()
    path = feature_path(("component", "comp1"), ("physics", "ht"), ("feature", "hs1"))
    with pytest.raises(PreWriteRefusal) as info:
        w15.physics_feature_update(w15_worker_for(model), "Model", {
            "path": path, "properties": {"Q0": "1e5[W/m^2]"},
        })
    refusal = info.value
    assert refusal.code == "UNIT_DIMENSION_MISMATCH"
    assert refusal.stage == "validation"
    check = refusal.details["unit_check"]
    assert check["status"] == "MISMATCH"
    assert check["documented_si_unit"] == "W/m^3"
    assert check["declared_unit"] == "W/m^2"
    assert check["write_point"] == "HeatSource.Q0"
    assert "Reference Manual" in check["evidence"]
    # Nothing was dispatched: the feature's own setter was never called.
    assert [call for call in feature.calls if call[0] == "set"] == []
    assert feature.values["Q0"] == "1e6[W/m^3]"


def test_the_documented_unit_is_written_and_the_check_is_recorded():
    model, feature = heat_source_model()
    path = feature_path(("component", "comp1"), ("physics", "ht"), ("feature", "hs1"))
    result = w15.physics_feature_update(w15_worker_for(model), "Model", {
        "path": path, "properties": {"Q0": "1e5[W/m^3]"},
    })
    assert result["status"] == "APPLIED"
    assert [call for call in feature.calls if call[0] == "set"] and feature.values["Q0"] == "1e5[W/m^3]"
    assert [row["status"] for row in result["unit_checks"]] == ["COMPATIBLE"]
    assert result["unit_checks"][0]["write_point"] == "HeatSource.Q0"


def test_an_equal_dimension_written_differently_is_accepted():
    """``1e5[W/m^3]`` in another legal spelling is not a mismatch."""
    model, _feature = heat_source_model()
    path = feature_path(("component", "comp1"), ("physics", "ht"), ("feature", "hs1"))
    result = w15.physics_feature_update(w15_worker_for(model), "Model", {
        "path": path, "properties": {"Q0": "0.1[mW/(mm^3)]"},
    })
    assert result["status"] == "APPLIED"
    assert result["unit_checks"][0]["status"] == "COMPATIBLE"


def test_unitless_and_unresolvable_values_are_admitted_and_recorded():
    """A legitimate write path is never narrowed by the guard."""
    model, _feature = heat_source_model()
    path = feature_path(("component", "comp1"), ("physics", "ht"), ("feature", "hs1"))
    plain = w15.physics_feature_update(w15_worker_for(model), "Model", {
        "path": path, "properties": {"Q0": "1e2"},
    })
    assert plain["status"] == "APPLIED"
    assert [row["status"] for row in plain["unit_checks"]] == ["NOT_DECLARED"]

    expression_model, _feature = heat_source_model()
    symbolic = w15.physics_feature_update(w15_worker_for(expression_model), "Model", {
        "path": path, "properties": {"Q0": "k(T)*1e3"},
    })
    assert symbolic["status"] == "APPLIED"
    assert [row["status"] for row in symbolic["unit_checks"]] == ["NOT_DECLARED"]


def test_a_unit_the_local_corpus_cannot_value_is_recorded_not_refused():
    model, _feature = heat_source_model()
    path = feature_path(("component", "comp1"), ("physics", "ht"), ("feature", "hs1"))
    result = w15.physics_feature_update(w15_worker_for(model), "Model", {
        "path": path, "properties": {"Q0": "1e5[BTU/(h*ft^3)]"},
    })
    assert result["status"] == "APPLIED"
    assert [row["status"] for row in result["unit_checks"]] == ["UNRESOLVED"]


def test_feature_create_refuses_a_proven_mismatch_before_the_feature_exists():
    model = build_model()
    interface = model.components["comp1"].physics_list.items["ht"]
    before = set(interface.feature_list.items)
    with pytest.raises(PreWriteRefusal) as info:
        w15.physics_feature_create(w15_worker_for(model), "Model", {
            "parent": PHYSICS_PATH, "tag": "hs1", "type_id": "HeatSource", "entity_dimension": 3,
            "properties": {"Q0": "1e5[W/m^2]"},
        })
    assert info.value.code == "UNIT_DIMENSION_MISMATCH"
    assert set(interface.feature_list.items) == before
    assert [call for call in interface.feature_list.calls if call[0] == "create"] == []


def test_a_boundary_heat_flux_takes_the_area_unit_and_not_the_volume_one():
    feature = FFeature(
        tag="hf1", type_id="HeatFluxBoundary", value_types={"q0": "String"},
        values={"q0": "100[W/m^2]"}, entities=[1], dim=2, geometry="geom1",
    )
    interface = physics_node(tag="ht", features={"hf1": feature})
    component = FComponent(materials={}, physics={"ht": interface})
    model = build_model(component=component)
    path = feature_path(("component", "comp1"), ("physics", "ht"), ("feature", "hf1"))
    with pytest.raises(PreWriteRefusal) as info:
        w15.physics_feature_update(w15_worker_for(model), "Model", {
            "path": path, "properties": {"q0": "1e5[W/m^3]"},
        })
    assert info.value.code == "UNIT_DIMENSION_MISMATCH"
    assert info.value.details["unit_check"]["documented_si_unit"] == "W/m^2"

    accepted = w15.physics_feature_update(w15_worker_for(model), "Model", {
        "path": path, "properties": {"q0": "600[kW/m^2]"},
    })
    assert accepted["status"] == "APPLIED"
    assert accepted["unit_checks"][0]["status"] == "COMPATIBLE"


def test_a_write_point_without_a_verified_row_is_left_alone():
    """A material property is not in the vocabulary, so nothing changes there."""
    model = build_model()
    result = w15.material_set_properties(w15_worker_for(model), "Model", {
        "path": MATERIAL_PATH, "group": "def",
        "properties": {"density": "8000[kg/m^3]"},
    })
    assert result["status"] == "APPLIED"


# ---------------------------------------------------------------------------
# 3. temporary-node ownership inventory (C04 / GUARD_T033)
# ---------------------------------------------------------------------------


class _TinyFeature:
    def __init__(self) -> None:
        self.values = ((3.0,),)

    def set(self, *_args: Any) -> None:
        return None

    def setIndex(self, *_args: Any) -> None:
        return None

    def run(self) -> None:
        return None

    def getReal(self) -> Any:
        return self.values

    def getData(self) -> Any:
        return self.values

    def isComplex(self) -> bool:
        return False


class _TinyNumerical:
    """The user's own Derived Values plus one removable MCP-owned node."""

    def __init__(self, *, remove_error: bool = False) -> None:
        self.features = {"gev1": _TinyFeature(), "user_table_bound": _TinyFeature()}
        self.remove_error = remove_error

    def create(self, tag: str, _type: str) -> None:
        self.features[tag] = _TinyFeature()

    def remove(self, tag: str) -> None:
        if self.remove_error:
            raise RuntimeError("remove denied")
        del self.features[tag]

    def __call__(self, tag: str) -> Any:
        return self.features[tag]

    def tags(self) -> list[str]:
        return list(self.features)


class _TinyModel:
    def __init__(self, numerical: _TinyNumerical) -> None:
        self.numerical = numerical
        self.java = type("Java", (), {"result": lambda _self: type(
            "Result", (), {"numerical": lambda _s, tag=None: numerical if tag is None else numerical(tag)}
        )()})()


def _run_tool_directly(tool: str, impl: Any) -> Any:
    """Drive a ``_tools_params`` tool body without the MCP transport."""
    return impl()


@pytest.fixture()
def tiny_tools(monkeypatch: pytest.MonkeyPatch):
    from comsol_mcp import _tools_params

    model_holder: dict[str, Any] = {}
    monkeypatch.setattr(_tools_params, "_run_tool", _run_tool_directly)
    monkeypatch.setattr(_tools_params, "_require_visible_main", lambda _name: model_holder["model"])
    monkeypatch.setattr(_tools_params, "_safe_model_label", lambda _model: "tiny")
    return model_holder


def test_evaluation_publishes_the_owned_temporary_node_inventory(tiny_tools):
    numerical = _TinyNumerical()
    tiny_tools["model"] = _TinyModel(numerical)
    from comsol_mcp._tools_params import evaluate_expressions

    payload = evaluate_expressions('[{"name": "T", "expression": "T"}]')

    assert payload["ephemeral_mutation"] is True
    inventory = payload["temporary_nodes"]
    assert isinstance(inventory, list) and len(inventory) == 1
    row = inventory[0]
    assert row["tag"].startswith("mcp_eval_") and len(row["tag"]) == len("mcp_eval_") + 32
    assert row["owner"]["operation"] == "evaluate_expressions"
    assert row["owner"]["policy"] == "ephemeral_mutation"
    assert row["owner"]["scope"] == "mcp_owned_result_numerical_node"
    assert row["tags_before"] == ["gev1", "user_table_bound"]
    assert row["tags_after"] == ["gev1", "user_table_bound"]
    assert row["created"] is True and row["removed"] is True
    assert row["verified_removed"] is True
    cleanup = payload["cleanup"]
    assert cleanup["cleanup_failed"] is False
    assert cleanup["created_count"] == 1 and cleanup["verified_removed_count"] == 1
    assert cleanup["owner_operations"] == ["evaluate_expressions"]
    assert set(numerical.tags()) == {"gev1", "user_table_bound"}


def test_a_failed_cleanup_publishes_the_leaked_node_and_reaches_unknown_through_c01(tiny_tools):
    numerical = _TinyNumerical(remove_error=True)
    tiny_tools["model"] = _TinyModel(numerical)
    from comsol_mcp._tools_params import evaluate_expressions

    with pytest.raises(ToolExecutionError) as info:
        evaluate_expressions('[{"name": "T", "expression": "T"}]')
    data = info.value.data
    assert data["cleanup_failed"] is True
    assert data["engine_state_unknown"] is True
    assert data["cleanup"]["cleanup_failed"] is True
    assert data["cleanup"]["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
    leaked = data["cleanup"]["unremoved_tags"]
    assert len(leaked) == 1 and leaked == [data["temporary_nodes"][0]["tag"]]
    assert data["temporary_nodes"][0]["verified_removed"] is False
    # The shared contract is what turns it into an unresolved engine state.
    assert final_state({"success": False, "data": data})[0] == STATE_UNKNOWN
    assert json.loads(json.dumps(data))["cleanup"]["cleanup_failed"] is True


def test_metric_evaluation_publishes_the_same_inventory(tiny_tools, monkeypatch):
    from comsol_mcp import _tools_params
    from comsol_mcp import _model_ops

    numerical = _TinyNumerical()
    tiny_tools["model"] = _TinyModel(numerical)
    monkeypatch.setattr(_tools_params, "_find_initialized_solution_tag", lambda _model: "sol1")

    def aggregate(model, expression, *_args):
        from comsol_mcp._g3_units import unit_dimension  # noqa: F401  (import sanity)
        with _model_ops._temporary_numerical_feature(model, "EvalGlobal") as node:
            return node.getData()

    monkeypatch.setattr(_tools_params, "_evaluate_aggregate", aggregate)
    payload = _tools_params.get_core_metrics('[{"name": "u", "expression": "u", "aggregate": "avg"}]')

    assert payload["temporary_nodes"][0]["owner"]["operation"] == "get_core_metrics"
    assert payload["temporary_nodes"][0]["verified_removed"] is True
    assert payload["cleanup"]["cleanup_failed"] is False
    assert set(numerical.tags()) == {"gev1", "user_table_bound"}


# ---------------------------------------------------------------------------
# 4. GUARD_T010: the replay identity is the product's own operation_id
#
# The driver's ``execution_identity`` reads ``operation_id``/``request_hash``/
# ``idempotency_key`` from the envelope's ``execution`` block, which the control
# daemon stamps in ``_finish``.  These tests drive the daemon's own
# ``dispatch``/``begin``/``_finish`` code (the live pair used
# ``node.property_set``; an offline daemon has no connected Worker, so the
# registered legacy operation carries the identical identity contract).
# ---------------------------------------------------------------------------


class _Adapter:
    def model_snapshot(self, tag: str) -> dict[str, Any]:
        return {"model_tag": tag, "server_instance_id": "server", "fingerprint": "fp",
                "external_event_counter": 0}


def _daemon(tmp_path, calls: list[Any]) -> ControlDaemon:
    service = ExecutionService(SessionLedger("session", "server"), _Adapter(), project_root=tmp_path)
    ref = service.bind_model("model")["execution"]["model_ref"]

    def setter(arguments: Mapping[str, Any]) -> dict[str, Any]:
        calls.append(dict(arguments))
        return {"success": True, "data": {"applied": [{"name": "x"}]}}

    daemon = ControlDaemon(tmp_path, service=service, registry={"set_parameters": setter})
    daemon.bound_ref = ref  # type: ignore[attr-defined]
    return daemon


def _write_request(daemon: ControlDaemon, *, key: str, value: str) -> dict[str, Any]:
    return {
        "operation": "set_parameters",
        "arguments": {"value": value},
        "execution": {"model_ref": daemon.bound_ref,  # type: ignore[attr-defined]
                      "expected_revision": 0,
                      "idempotency_key": key, "request_id": f"{key}-request"},
    }


def test_a_verbatim_replay_returns_the_same_operation_identity_without_re_executing(tmp_path):
    calls: list[Any] = []
    daemon = _daemon(tmp_path, calls)
    try:
        request = _write_request(daemon, key="guard-t010-repeat", value="1")
        first = daemon.dispatch(request)
        retry = daemon.dispatch(json.loads(json.dumps(request)))

        assert first["success"] is True
        assert first["execution"]["operation_id"]
        assert retry["execution"]["operation_id"] == first["execution"]["operation_id"]
        assert retry["execution"]["request_hash"] == first["execution"]["request_hash"]
        assert retry["execution"]["idempotency_key"] == "guard-t010-repeat"
        assert retry["execution"]["job_id"] == first["execution"]["job_id"]
        assert retry["success"] is True
        assert calls == [{"value": "1"}]
    finally:
        daemon.close()


def test_the_same_key_with_a_different_body_is_a_conflict_and_keeps_the_first_identity(tmp_path):
    calls: list[Any] = []
    daemon = _daemon(tmp_path, calls)
    try:
        first = daemon.dispatch(_write_request(daemon, key="guard-t010-conflict", value="1"))
        conflict = daemon.dispatch(_write_request(daemon, key="guard-t010-conflict", value="2"))

        assert conflict["success"] is False
        assert conflict["error"]["code"] == "IDEMPOTENCY_CONFLICT"
        assert conflict["error"]["safe_retry"] is False
        assert calls == [{"value": "1"}]
        rows = daemon.store.db.execute(
            "SELECT operation_id FROM operations WHERE idempotency_key=?",
            ("guard-t010-conflict",),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == first["execution"]["operation_id"]
        assert daemon.store.get_operation(first["execution"]["operation_id"])["result"]["success"] is True
    finally:
        daemon.close()


# ---------------------------------------------------------------------------
# 5. the evaluated value's provenance and the fail-closed empty read
#    (C04 shared data contract; m1d W13_T006 ``expression_evaluates_after_modification``)
#
# The m1d run's ``evaluate_expressions`` reply published ``ok: true`` next to
# ``value: []``/``last_value: None`` for ``q1``/``q2``: the value was never
# published at all, and the dashboard could only say "observed None".  C04 makes
# the reply carry the value's dataset/solution and the result shape, and an empty
# read is a business failure that names the route it took - never a success.
# ---------------------------------------------------------------------------


class _ScriptedFeature:
    """One engine numerical feature: records the property writes, answers the read."""

    def __init__(self, script: dict[str, Any], feature_type: str = "EvalGlobal") -> None:
        self.script = script
        self.feature_type = feature_type
        self.values: Any = script.get("read", ((3.0,),))
        self.data: Any = None
        self.expression: Any = None

    def set(self, name: str, value: Any) -> None:
        self.script["sets"].append([name, value])
        if name == "data":
            self.script["data"] = value
            self.data = value
        if name == "expr" and isinstance(value, (list, tuple)) and value:
            self.expression = str(value[0])
            self.script.setdefault("expressions", []).append(self.expression)

    def setIndex(self, name: str, value: Any, index: int) -> None:
        self.script["sets"].append([f"{name}[{index}]", value])

    def run(self) -> None:
        self.script["runs"] = self.script.get("runs", 0) + 1

    def _read(self) -> Any:
        """The values this feature answers for *the data state it was set to* and its expression."""
        by_data = self.script.get("read_by_data") or {}
        if self.data in by_data:
            per_data = by_data[self.data]
            if isinstance(per_data, Mapping):
                return per_data.get(self.expression, self.values)
            return per_data
        return self.values

    def getReal(self) -> Any:
        return self._read()

    def getData(self) -> Any:
        return self._read()

    def isComplex(self) -> bool:
        return False

    def getString(self, name: str) -> Any:
        return self.data if name == "data" else None

    def getType(self) -> str:
        return self.feature_type

    def getValueType(self, name: str) -> Any:
        """The engine's declared value type of one property (``String`` for ``data``)."""
        return (self.script.get("value_types") or {}).get(name)

    def getAllowedPropertyValues(self, name: str) -> Any:
        allowed = self.script.get("allowed_values")
        if allowed is None:
            raise RuntimeError(f"getAllowedPropertyValues({name}) is not available on this build")
        return list(allowed)


class _ScriptedNumerical:
    def __init__(self, script: dict[str, Any]) -> None:
        self.script = script
        self.features: dict[str, _ScriptedFeature] = {}

    def create(self, tag: str, feature_type: str) -> None:
        self.features[tag] = _ScriptedFeature(self.script, feature_type)

    def remove(self, tag: str) -> None:
        del self.features[tag]

    def __call__(self, tag: str) -> Any:
        return self.features[tag]

    def tags(self) -> list[str]:
        return list(self.features)


class _ScriptedDataset:
    def __init__(self, row: Mapping[str, Any]) -> None:
        self.row = row

    def getType(self) -> str:
        return str(self.row.get("type", "Solution"))

    def getString(self, name: str) -> Any:
        return self.row.get("solution") if name == "solution" else None


class _ScriptedCollection:
    def __init__(self, rows: Mapping[str, Any]) -> None:
        self.rows = dict(rows)

    def tags(self) -> list[str]:
        return list(self.rows)

    def __call__(self, tag: str) -> Any:
        return _ScriptedDataset(self.rows[tag]) if isinstance(self.rows[tag], Mapping) else self.rows[tag]


class _ScriptedParam:
    def __init__(self, script: dict[str, Any]) -> None:
        self.script = script

    def evaluate(self, expression: str) -> float:
        error = self.script.get("param_errors", {}).get(expression)
        if error is not None:
            raise error
        if expression not in self.script.get("param_values", {}):
            raise RuntimeError(f"Unknown variable {expression}.")
        return float(self.script["param_values"][expression])

    def varnames(self) -> Any:
        """The *global* model parameters this collection holds (what this evaluator can resolve)."""
        return list(self.script.get("param_names", ()))


def _script(**overrides: Any) -> dict[str, Any]:
    script: dict[str, Any] = {"sets": [], "read": ((3.0,),), "datasets": {}, "solutions": (),
                              "components": (), "param_values": {}, "param_errors": {},
                              "param_names": (), "read_by_data": None, "allowed_values": None,
                              "value_types": None}
    script.update(overrides)
    return script


_DRIVER: Any = None


def _driver_module():
    """The acceptance driver, loaded offline.

    Only its *pure* checks are used (a reply payload in, a verdict out): the driver is never run
    here, so no live case, engine or evidence directory is touched.
    """
    global _DRIVER
    if _DRIVER is None:
        path = Path(__file__).resolve().parents[1] / "tools" / "phase4_run_mcp.py"
        spec = importlib.util.spec_from_file_location("phase4_driver_m1e_route", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[str(spec.name)] = module
        spec.loader.exec_module(module)
        _DRIVER = module
    return _DRIVER


class _ScriptedModel:
    """A model facade with the engine accessors the C04 provenance reads."""

    def __init__(self, script: dict[str, Any]) -> None:
        self.script = script
        self.numerical = _ScriptedNumerical(script)
        holder = self

        class _Result:
            def numerical(self_inner, tag: str | None = None) -> Any:
                return holder.numerical if tag is None else holder.numerical(tag)

            def dataset(self_inner) -> Any:
                return _ScriptedCollection(script["datasets"])

        class _Java:
            def result(self_inner) -> Any:
                return _Result()

            def sol(self_inner) -> Any:
                return _ScriptedCollection({tag: {} for tag in script["solutions"]})

            def param(self_inner) -> Any:
                return _ScriptedParam(script)

            def component(self_inner) -> Any:
                return _ScriptedCollection({tag: {} for tag in script["components"]})

        self.java = _Java()


def test_an_evaluation_publishes_the_value_with_its_binding_and_result_shape(tiny_tools):
    script = _script(datasets={"dset1": {"type": "Solution", "solution": "sol1"}}, solutions=("sol1",))
    tiny_tools["model"] = _ScriptedModel(script)
    from comsol_mcp._tools_params import evaluate_expressions

    payload = evaluate_expressions('[{"name": "q1_probe", "expression": "q1"}]')
    row = payload["results"][0]

    assert row["ok"] is True
    assert row["value"] == [[3.0]] and row["last_value"] == 3.0
    assert row["shape"] == [1, 1]
    assert row["dataset"] == "dset1" and row["solution"] == "sol1"
    assert row["route"] == "results:EvalGlobal"
    # The binding is not just claimed: the documented ``data`` property was set and read back.
    assert ["data", "dset1"] in script["sets"]
    assert payload["dataset"] == "dset1" and payload["solution"] == "sol1"
    assert payload["binding"]["bound"] == "dset1" and payload["binding"]["bound_verified"] is True
    assert [entry["tag"] for entry in payload["dataset_inventory"]["datasets"]] == ["dset1"]
    # The published dataset is the whole story here: the data-independent state is never requested,
    # and the property's allowed-value set is not probed (m1d behaviour, unchanged).
    assert ["data", "none"] not in script["sets"]
    assert row["data_mode"] == "dataset"
    assert "allowed_values" not in row["diagnosis"]["binding"]
    assert "expression_evaluator" not in row["diagnosis"]


def test_a_model_without_a_dataset_is_evaluated_in_the_documented_data_none_state(tiny_tools):
    """m1e W13_T006: no dataset at all - ``data`` is set to the documented ``none``, not defaulted.

    The results route then answers the component variable itself (``q1``/``q2``), so the reply
    publishes the value together with the state it was read in, the engine's read-back of that state,
    the values the engine allows for the property and the corpus citation for the route.
    """
    script = _script(read=[], read_by_data={"none": {"q1": ((2.0,),), "q2": ((3.0,),)}},
                     allowed_values=("none", "parent"), param_names=("T0", "L"),
                     value_types={"data": "String"})
    tiny_tools["model"] = _ScriptedModel(script)
    from comsol_mcp._tools_params import evaluate_expressions

    payload = evaluate_expressions('[{"name": "q1_probe", "expression": "q1"},'
                                   ' {"name": "q2_probe", "expression": "q2"}]')
    rows = {row["name"]: row for row in payload["results"]}

    assert rows["q1_probe"]["last_value"] == 2.0 and rows["q2_probe"]["last_value"] == 3.0
    assert ["data", "none"] in script["sets"], "the documented data state must be set explicitly"
    for row in rows.values():
        assert row["ok"] is True
        assert row["route"] == "results:EvalGlobal"
        assert row["data_mode"] == "none"
        assert row["dataset"] is None and row["solution"] is None
        # The value came from the results node itself: the global-parameter leg was never needed.
        assert "expression_evaluator" not in row["diagnosis"]
        assert [entry["route"] for entry in row["diagnosis"]["attempts"]] == ["results:EvalGlobal"]
        assert row["diagnosis"]["attempts"][0]["data"] == "none"
        assert ("result().numerical(<owned tag>).set('data', 'none')"
                in row["diagnosis"]["engine_calls"])
        binding = row["diagnosis"]["binding"]
        assert binding["data_mode"] == "none"
        assert binding["bound"] == "none" and binding["bound_verified"] is True
        assert binding["bind_error"] is None
        # The engine itself was asked which values the property allows on this build, through the
        # shared property-metadata vocabulary: the record also names the getter and Java signature.
        assert binding["allowed_values"] == ["none", "parent"]
        assert binding["allowed_values_include_none"] is True
        assert binding["allowed_values_error"] is None
        metadata = binding["data_property"]
        assert metadata["name"] == "data" and metadata["value_type"] == "String"
        assert metadata["kind"] == "string" and metadata["shape_rank"] == 0
        assert metadata["getter"] == "getString" and metadata["java_signature"] == "java.lang.String"
        assert metadata["metadata_status"] == "KNOWN" and metadata["type_id"] == "EvalGlobal"
        citations = binding["data_citation"]
        primary = next(row_ for row_ in citations if row_["doc_id"] == "4454")
        assert primary["path"] == ("doc/help/wtpwebapps/ROOT/doc/com.comsol.help.comsol/"
                                  "comsol_api_results.52.051.html")
        assert primary["sha256"] == "17429de155e56bb06014875e959117db215db23bb12baf7bb95daffa7e77d445"
        assert "none" in primary["claim"] and primary["chunk_id"] == 17235
        assert {"4452", "4469", "4080"} <= {row_["doc_id"] for row_ in citations}
        assert all(row_["sha256"] for row_ in citations)


def test_an_unreadable_dataset_collection_is_never_pinned_to_data_none(tiny_tools):
    """A state we could not read is not a state we may claim: the documented default is kept."""
    script = _script(datasets=None)
    tiny_tools["model"] = _ScriptedModel(script)
    from comsol_mcp._tools_params import evaluate_expressions

    payload = evaluate_expressions('[{"name": "q1_probe", "expression": "q1"}]')
    row = payload["results"][0]
    binding = row["diagnosis"]["binding"]

    # The engine could not be read at all, so the value still comes from the results route...
    assert row["ok"] is True and row["last_value"] == 3.0
    assert row["route"] == "results:EvalGlobal"
    # ... and no ``data`` state was claimed: neither a dataset nor ``none`` was set.
    assert [name for name, _value in script["sets"] if name == "data"] == []
    assert row["data_mode"] is None and binding["data_mode"] is None
    assert binding["bound"] is None and binding["bound_verified"] is None
    assert "does not prove" in binding["data_reason"]
    assert "no dataset collection" in binding["reason"]
    assert "data_citation" not in binding


def test_the_data_none_state_is_reported_even_when_the_engine_cannot_declare_allowed_values(tiny_tools):
    """The engine's allowed-value list is a probe: its refusal is recorded, never fatal."""
    script = _script(read=[], read_by_data={"none": ((2.0,),)})
    tiny_tools["model"] = _ScriptedModel(script)
    from comsol_mcp._tools_params import evaluate_expressions

    payload = evaluate_expressions('[{"name": "q1_probe", "expression": "q1"}]')
    row = payload["results"][0]
    binding = row["diagnosis"]["binding"]

    assert row["ok"] is True and row["last_value"] == 2.0 and row["data_mode"] == "none"
    assert binding["bound_verified"] is True
    assert binding["allowed_values"] is None
    assert binding["allowed_values_include_none"] is None
    assert "getAllowedPropertyValues" in binding["allowed_values_error"]


def test_a_data_none_read_that_is_still_empty_stays_a_truthful_failure(tiny_tools):
    """The m1e shape kept honest: the documented state is set, the engine still reads nothing - FAIL.

    Nothing is published as a value, the row names the route *and* the data state it ran in, the
    global-parameter evaluator's scope says it could never have resolved ``q1``, and the citations
    for both legs ride along.
    """
    script = _script(read=[], read_by_data={"none": []}, allowed_values=("none",), param_names=("T0",))
    tiny_tools["model"] = _ScriptedModel(script)
    from comsol_mcp._tools_params import evaluate_expressions

    with pytest.raises(ToolExecutionError) as info:
        evaluate_expressions('[{"name": "q1_probe", "expression": "q1"}]')
    data = info.value.data
    row = data["results"][0]

    assert row["ok"] is False
    assert row["error_code"] == "NO_VALUES_RETURNED"
    assert "no dataset" in row["error"]
    assert row["dataset"] is None and row["solution"] is None
    assert row["data_mode"] == "none"
    assert row["route"] == "results:EvalGlobal"
    assert row["last_value"] is None
    assert ["data", "none"] in script["sets"]
    diagnosis = row["diagnosis"]
    assert [entry["route"] for entry in diagnosis["attempts"]] == ["results:EvalGlobal",
                                                                  "engine:model.param().evaluate"]
    assert diagnosis["attempts"][0]["ok"] is True and diagnosis["attempts"][0]["value_shape"] == [0]
    assert diagnosis["attempts"][0]["data"] == "none"
    assert "Unknown variable q1." in diagnosis["attempts"][1]["error"]
    empty = diagnosis["empty_results_read"]
    assert empty["data"] == "none" and empty["data_mode"] == "none"
    assert empty["result_shape"] == [0] and empty["dataset"] is None
    assert "global" in empty["reason"]
    # The failure block carries the data route's own citations; the evaluator leg's citations are
    # recorded with that leg's scope below.
    assert {row_["doc_id"] for row_ in empty["citations"]} >= {"4454", "4452", "4080"}
    scope = diagnosis["expression_evaluator"]["scope"]
    assert scope["expression_is_global_parameter"] is False
    assert scope["global_parameters"] == ["T0"]
    assert scope["resolves"].startswith("global model parameters")
    assert {row_["doc_id"] for row_ in scope["citations"]} == {"4121", "3961"}
    assert data["status"] == "partial" and data["partial_change"] is False
    assert data["safe_retry"] is True


def test_the_documented_engine_evaluator_carries_a_data_independent_value(tiny_tools):
    """A constant/variable needs no dataset: the engine's own ``evaluate`` answers, and says so."""
    script = _script(read=[], param_values={"q1": 2.0, "q2": 3.0}, param_names=("q1", "q2"),
                     read_by_data={"none": []})
    tiny_tools["model"] = _ScriptedModel(script)
    from comsol_mcp._tools_params import evaluate_expressions

    payload = evaluate_expressions('[{"name": "q1_probe", "expression": "q1"},'
                                   ' {"name": "q2_probe", "expression": "q2"}]')
    rows = {row["name"]: row for row in payload["results"]}

    assert rows["q1_probe"]["last_value"] == 2.0 and rows["q2_probe"]["last_value"] == 3.0
    for row in rows.values():
        assert row["ok"] is True
        assert row["route"] == "engine:model.param().evaluate"
        assert row["dataset"] is None and row["solution"] is None
        assert row["shape"] == []
        assert row["diagnosis"]["attempts"][0]["route"] == "results:EvalGlobal"
        assert row["diagnosis"]["attempts"][0]["data"] == "none"
        assert row["diagnosis"]["attempts"][0]["value_shape"] == [0]
        assert row["diagnosis"]["attempts"][1]["route"] == "engine:model.param().evaluate"
        assert row["diagnosis"]["attempts"][1]["evaluated_expression"] == row["expression"]
        # The scope says *why* this leg resolves a name: q1/q2 are global parameters here.
        scope = row["diagnosis"]["expression_evaluator"]["scope"]
        assert scope["expression_is_global_parameter"] is True
        assert scope["global_parameters"] == ["q1", "q2"]
        assert {entry["doc_id"] for entry in scope["citations"]} == {"4121", "3961"}


def test_the_component_qualified_retry_is_recorded_when_the_short_name_is_not_resolved(tiny_tools):
    """A component variable is addressed with its component qualifier at the global scope."""
    script = _script(read=[], components=("comp1",), param_names=("T0",),
                     param_errors={"q1": RuntimeError("Unknown variable q1.")},
                     param_values={"comp1.q1": 3.0})
    tiny_tools["model"] = _ScriptedModel(script)
    from comsol_mcp._tools_params import evaluate_expressions

    payload = evaluate_expressions('[{"name": "q1_probe", "expression": "q1"}]')
    row = payload["results"][0]

    assert row["ok"] is True and row["last_value"] == 3.0
    assert row["route"] == "engine:model.param().evaluate"
    assert row["expression"] == "q1", "the requested expression text is published unchanged"
    assert row["diagnosis"]["evaluated_expression"] == "comp1.q1"
    assert [entry["expression"] for entry in row["diagnosis"]["expression_evaluator_attempts"]] == ["q1", "comp1.q1"]
    # Both spellings were tried *and* the scope records that neither is a global parameter: if the
    # engine answers a qualified component variable here, the reply still says which leg answered.
    scope = row["diagnosis"]["expression_evaluator"]["scope"]
    assert scope["expression_is_global_parameter"] is False
    assert scope["global_parameters"] == ["T0"]


def test_the_w13_t006_acceptance_check_reads_the_data_none_reply_as_a_pass(tiny_tools):
    """The acceptance line's own check, driven offline over this route's reply.

    ``W13_T006.expression_evaluates_after_modification`` is judged by
    ``_check_expression_values({"q1_probe": 2.0, "q2_probe": 3.0})``; here the reply this route
    publishes is fed to that very check, so the fix is verified against the acceptance criterion
    instead of against a restatement of it - and the same check on an empty read stays a FAIL whose
    recorded row names the route and the data state it ran in.
    """
    driver = _driver_module()
    check = driver._check_expression_values({"q1_probe": 2.0, "q2_probe": 3.0})

    script = _script(read=[], read_by_data={"none": {"q1": ((2.0,),), "q2": ((3.0,),)}},
                     allowed_values=("none",), param_names=("T0",))
    tiny_tools["model"] = _ScriptedModel(script)
    from comsol_mcp._tools_params import evaluate_expressions

    payload = evaluate_expressions('[{"name": "q1_probe", "expression": "q1"},'
                                   ' {"name": "q2_probe", "expression": "q2"}]')
    verdict, reason, bundle = check({"success": True, "data": payload}, {})

    assert (verdict, reason) == ("PASS", None)
    assert [row_["route"] for row_ in bundle["results"]] == ["results:EvalGlobal", "results:EvalGlobal"]
    assert all(row_["data_mode"] == "none" for row_ in bundle["results"])

    empty = _script(read=[], read_by_data={"none": []}, allowed_values=("none",))
    tiny_tools["model"] = _ScriptedModel(empty)
    with pytest.raises(ToolExecutionError) as info:
        evaluate_expressions('[{"name": "q1_probe", "expression": "q1"},'
                             ' {"name": "q2_probe", "expression": "q2"}]')
    failure = info.value.data
    verdict, reason = check({"success": False, "error": {"code": "NO_VALUES_RETURNED"},
                             "data": failure}, {})

    assert verdict == "FAIL" and reason == "expression evaluation returned NO_VALUES_RETURNED"
    refused = failure["results"][0]
    assert refused["ok"] is False and refused["route"] == "results:EvalGlobal"
    assert refused["data_mode"] == "none" and refused["error_code"] == "NO_VALUES_RETURNED"
    # The driver quotes the product's own reason verbatim, route included, for the row it refused.
    assert "[route results:EvalGlobal]" in driver._row_refusal(refused)


# ---------------------------------------------------------------------------
# 6. T015: the source write points state verbatim use and no implicit factor
#
# The acceptance forbids multiplying a source by a thickness or an absorptivity
# factor, and the driver's old check read a field the product never published
# (``implicit_thickness_applied``), so "no implicit factor" was never evidenced.
# Every unit-check record now carries the documented entity, ``verbatim`` and the
# ``implicit_factors`` policy that says so.
# ---------------------------------------------------------------------------


def test_source_write_points_are_reported_verbatim_without_implicit_factors():
    from comsol_mcp import _g3_units as units

    volume = units.unit_check("HeatSource", "Q0", "1e5[W/m^3]")
    surface = units.unit_check("HeatFluxBoundary", "q0", "1e5[W/m^2]")

    assert volume["status"] == units.COMPATIBLE
    assert surface["status"] == units.COMPATIBLE
    assert volume["documented_entity"] == "domain" and volume["documented_si_unit"] == "W/m^3"
    assert surface["documented_entity"] == "boundary" and surface["documented_si_unit"] == "W/m^2"
    for record in (volume, surface):
        assert record["verbatim"] is True
        assert record["implicit_factors"]["applied"] is False
        assert record["implicit_factors"]["thickness"] is False
        assert record["implicit_factors"]["absorptivity"] is False
        assert record["implicit_factors"]["policy"] == units.VERBATIM_WRITE_POLICY
        assert "never multiplies" in record["implicit_factors"]["policy"]
    # A W/m^2 expression on the volumetric write point is a *refusal*, never a thickness conversion.
    mismatch = units.unit_check("HeatSource", "Q0", "1e5[W/m^2]")
    assert mismatch["status"] == units.MISMATCH
    assert mismatch["implicit_factors"]["applied"] is False
    assert mismatch["documented_entity"] == "domain"

    vocabulary = units.write_point_vocabulary()
    assert vocabulary["verbatim_policy"] == units.VERBATIM_WRITE_POLICY
    published = {(row["feature_type"], row["property"]): row for row in vocabulary["rows"]}
    assert published[("HeatFluxBoundary", "q0")]["documented_entity"] == "boundary"
    assert published[("HeatFluxBoundary", "q0")]["implicit_factors"] == {"applied": False, "thickness": False,
                                                                        "absorptivity": False}
    assert published[("HeatSource", "Q0")]["documented_entity"] == "domain"
