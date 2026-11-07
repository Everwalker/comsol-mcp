"""T14c: does the public managed route publish the T14 variable-selection gates
as pre-write refusals, or flatten them into EXECUTION_STATE_UNKNOWN?

Runs the two w13 variable-selection entry points through
ManagedBackend._dispatch_with_witness -- the exact shared write path every
managed MCP operation takes -- against the same fake engine the T14 tests use.

Read-only software probe: nothing here touches COMSOL.  With the fake engine
the DispatchWitness is clean by construction (the fake is not behind
_java_worker's transport, the only place record_engine_method fires), so the
write-dispatch proof in these runs is the fake engine's own state printed
after every case: a refused case must show that no variable node exists.
The live-transport witness behaviour (a recorded mutation keeps UNKNOWN) is
already pinned by tests/test_m1_product_repairs.py, not by this script.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from comsol_mcp import _g3_w13 as w13
from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._managed_backend import ManagedBackend

try:
    from tests.test_g3_w13 import FNode, selection_node, variable_model, worker_for
    from tests.test_t14_selection_routes import two_component_model
except ImportError:  # pragma: no cover
    from test_g3_w13 import FNode, selection_node, variable_model, worker_for  # type: ignore
    from test_t14_selection_routes import two_component_model  # type: ignore


def group_path(group_tag: str) -> dict:
    return {"segments": [{"collection": "component", "tag": "comp1"},
                         {"collection": "variable", "tag": group_tag}]}


def engine_state(model) -> str:
    comp1 = model.collections["component"].items["comp1"]
    return (f"comp1 variable tags={sorted(comp1.collections['variable'].items)} "
            f"global variable tags={sorted(model.collections['variable'].items)}")


def run(label: str, operation: str, call, model) -> None:
    print(f"== {label} ==")
    try:
        envelope = ManagedBackend._dispatch_with_witness(operation, call, effect="project_write")
        data = envelope["data"]
        print(f"{label}: envelope status={data.get('status')} success={envelope.get('success')} "
              f"execution_state_unknown={envelope.get('execution_state_unknown')} "
              f"dispatch_stage={data.get('dispatch_stage')} "
              f"witness.mutation_issued={data.get('witness', {}).get('mutation_issued')}")
        if data.get("status") == "REFUSED":
            refusal = data["refusal"]
            print(f"{label}: refusal code={refusal['code']} stage={refusal['stage']} "
                  f"message={refusal['message']}")
    except ExecutionContractError as exc:
        details = exc.details or {}
        print(f"{label}: RAISED {exc.code} stage={exc.stage}")
        if details.get("cause_code"):
            print(f"{label}: cause_code={details['cause_code']} cause_message={details.get('cause_message')}")
        if "did not declare a pre-dispatch stage" in str(exc):
            print(f"{label}: NOTE flattening reason: did not declare a pre-dispatch stage")
    print(f"{label}: {engine_state(model)}")
    print()


def main() -> None:
    # M1: component mismatch, named spec (same tag in comp1 and comp2).
    model, _c1, _c2 = two_component_model()
    run("M1 group_create named component mismatch (comp1 group, comp2 spec)",
        "variable.group_create",
        lambda: w13.variable_group_create(worker_for(model), "Model", {
            "tag": "varX", "component": "comp1",
            "selection": {"kind": "named", "component": "comp2", "tag": "faces"}}),
        model)

    # M2: component mismatch on an existing comp1 group (inherited kind).
    model, comp1, _c2 = two_component_model()
    group = FNode(tag="gv2", expressions={})
    comp1.collections["variable"].items["gv2"] = group
    run("M2 selection_set inherited component mismatch (comp1 group, comp2 spec)",
        "variable.selection_set",
        lambda: w13.variable_selection_set(worker_for(model), "Model", {
            "group": group_path("gv2"),
            "selection": {"kind": "inherited", "component": "comp2"}}),
        model)
    print(f"M2 group state after refusal: named_ref={group.named_ref} model_ref={group.model_ref} "
          f"inheriting_={group.inheriting_}")
    print()

    # M3: stale named dependency (the F3 gate; raises via node_not_found).
    model, _comp1 = variable_model(selections={"faces": selection_node(tag="faces")})
    run("M3 group_create stale named tag 'gone'",
        "variable.group_create",
        lambda: w13.variable_group_create(worker_for(model), "Model", {
            "tag": "varX", "component": "comp1",
            "selection": {"kind": "named", "component": "comp1", "tag": "gone"}}),
        model)

    # M4: unbindable kind at group_create (T14 moved validation before creation).
    model, _comp1 = variable_model(selections={"faces": selection_node(tag="faces")})
    run("M4 group_create unbindable kind spatial (pre-create validation)",
        "variable.group_create",
        lambda: w13.variable_group_create(worker_for(model), "Model", {
            "tag": "varBad",
            "selection": {"kind": "spatial", "component": "comp1", "geometry": "geom1",
                          "entity_dimension": 2, "query": {"kind": "box"}}}),
        model)

    # M5: spec shape refusal from validate_selection_spec (named without tag).
    model, _comp1 = variable_model(selections={"faces": selection_node(tag="faces")})
    run("M5 group_create named spec without tag",
        "variable.group_create",
        lambda: w13.variable_group_create(worker_for(model), "Model", {
            "tag": "varY", "component": "comp1",
            "selection": {"kind": "named", "component": "comp1"}}),
        model)

    # Positive controls: global group taking its component from the spec, and a
    # component-level group whose spec names the same component, stay legal.
    model, _comp1 = variable_model(selections={"faces": selection_node(tag="faces")})
    run("P-OK1 global group, named spec component comp1 (legitimate)",
        "variable.group_create",
        lambda: w13.variable_group_create(worker_for(model), "Model", {
            "tag": "varG",
            "selection": {"kind": "named", "component": "comp1", "tag": "faces"}}),
        model)
    model, _comp1 = variable_model(selections={"faces": selection_node(tag="faces")})
    run("P-OK2 comp1 group, named spec component comp1 (legitimate)",
        "variable.group_create",
        lambda: w13.variable_group_create(worker_for(model), "Model", {
            "tag": "varOk", "component": "comp1",
            "selection": {"kind": "named", "component": "comp1", "tag": "faces"}}),
        model)


if __name__ == "__main__":
    main()
