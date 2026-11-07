"""T14d: does the public managed route publish the global-scope selection gate
(scope == global + explicit/all + no effective_component) as a pre-write
refusal, or flatten it into EXECUTION_STATE_UNKNOWN?

The T14c probe covered the component-mismatch and named-dependency gates; the
global-scope gate in variable_group_create/variable_selection_set still raised
a stage-less ExecutionContractError at the time of that run.  This probe runs
the two w13 variable-selection entry points through
ManagedBackend._dispatch_with_witness -- the exact shared write path every
managed MCP operation takes -- against the same fake engine.

Read-only software probe: nothing here touches COMSOL.  As in T14c the fake
engine is not behind the _java_worker transport, so DispatchWitness is clean
by construction; the write-dispatch proof is the printed engine state (a
refused case must show that no variable node exists / group state unchanged).
The live-transport behaviour (a recorded mutation keeps EXECUTION_STATE_UNKNOWN)
is pinned by tests/test_m1_product_repairs.py.
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
    from tests.test_g3_w13 import FNode, variable_model, worker_for
except ImportError:  # pragma: no cover
    from test_g3_w13 import FNode, variable_model, worker_for  # type: ignore


def global_group_path(group_tag: str) -> dict:
    return {"segments": [{"collection": "variable", "tag": group_tag}]}


def explicit_spec(component: str | None) -> dict:
    spec = {"kind": "explicit", "geometry": "geom1", "entity_dimension": 2, "entities": [1, 2]}
    if component is not None:
        spec["component"] = component
    return spec


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
    # G1: global group creation with a local explicit selection that names no
    # component anywhere (HEAD pinned this gate at the direct-call level only).
    model, _comp1 = variable_model()
    run("G1 group_create global explicit spec without component",
        "variable.group_create",
        lambda: w13.variable_group_create(worker_for(model), "Model", {
            "tag": "varGate",
            "selection": explicit_spec(None)}),
        model)

    # G2: selection_set on an existing global group, same missing component.
    model, _comp1 = variable_model()
    group = FNode(tag="gvGate", expressions={})
    model.collections["variable"].items["gvGate"] = group
    run("G2 selection_set global explicit spec without component",
        "variable.selection_set",
        lambda: w13.variable_selection_set(worker_for(model), "Model", {
            "group": global_group_path("gvGate"),
            "selection": explicit_spec(None)}),
        model)
    print(f"G2 group state after refusal: named_ref={group.named_ref} model_ref={group.model_ref} "
          f"inheriting_={group.inheriting_} entities_={group.entities_}")
    print()

    # P1: the same global-group creation is legitimate when the spec itself
    # carries the component (effective_component resolves).
    model, _comp1 = variable_model()
    run("P1 group_create global explicit spec component comp1 (legitimate)",
        "variable.group_create",
        lambda: w13.variable_group_create(worker_for(model), "Model", {
            "tag": "varOk",
            "selection": explicit_spec("comp1")}),
        model)

    # P2: selection_set on a global group with spec.component set is legitimate.
    model, _comp1 = variable_model()
    model.collections["variable"].items["gvOk"] = FNode(tag="gvOk", expressions={})
    run("P2 selection_set global explicit spec component comp1 (legitimate)",
        "variable.selection_set",
        lambda: w13.variable_selection_set(worker_for(model), "Model", {
            "group": global_group_path("gvOk"),
            "selection": explicit_spec("comp1")}),
        model)


if __name__ == "__main__":
    main()
