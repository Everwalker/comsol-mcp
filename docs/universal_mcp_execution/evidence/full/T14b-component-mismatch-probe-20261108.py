"""T14-b follow-up probe: group component vs selection spec component.

Independent negative control on the same fake engine as tests/test_g3_w13.py.
comp1 and comp2 each own a named selection 'sel' with different entity sets,
so an A/B same-tag mis-binding is observable in the recorded engine state.
"""
import json
import sys

sys.path.insert(0, ".")

from comsol_mcp import _g3_w13 as w13
from comsol_mcp._g3_common import ExecutionContractError
from tests.test_g3_w13 import FNode, FList, selection_node, variable_model, worker_for


def ab_model():
    model, comp1 = variable_model(selections={"sel": selection_node(tag="sel", entities_=[1, 2], dim_=2)})
    comp2 = FNode(tag="comp2", type_id="Component")
    comp2.collections["geom"] = FList(arity=2, node_type="GeomSequence")
    comp2.collections["geom"].items["g2"] = FNode(tag="g2", integers={"sdim": 2})
    comp2.collections["selection"] = FList(arity=2, node_type="SelectionFeature")
    comp2.collections["selection"].items["sel"] = selection_node(tag="sel", entities_=[9], dim_=0)
    comp2.collections["variable"] = FList(arity=1, node_type="Expr")
    comp2.measure_entities = {2: [7], 0: [9]}
    model.collections["component"].items["comp2"] = comp2
    return model, comp1, comp2


def show(case, fn):
    try:
        result = fn()
        print(f"{case}: OK -> {json.dumps(result, default=str)[:300]}")
    except ExecutionContractError as exc:
        print(f"{case}: {exc.code} -> {exc}")


print("== P1 group_create: group in comp1, named spec component comp2 (same tag in both) ==")
model, comp1, comp2 = ab_model()
show("P1", lambda: w13.variable_group_create(worker_for(model), "Model", {
    "tag": "varX", "component": "comp1",
    "selection": {"kind": "named", "component": "comp2", "tag": "sel"}}))
node = comp1.collections["variable"].items.get("varX")
print(f"P1 state: varX exists in comp1={node is not None}, "
      f"model_ref={getattr(node, 'model_ref', None)!r}, named_ref={getattr(node, 'named_ref', None)!r} "
      f"(caller asked for comp2's 'sel'; comp1 model() + tag 'sel' resolves comp1's 'sel' [1,2]/dim2, "
      f"comp2's is [9]/dim0)")

print("== P2 selection_set: group path comp1/variable/var1, named spec component comp2 ==")
model, comp1, comp2 = ab_model()
group = FNode(tag="var1", expressions={})
comp1.collections["variable"].items["var1"] = group
path = {"segments": [{"collection": "component", "tag": "comp1"},
                     {"collection": "variable", "tag": "var1"}]}
show("P2", lambda: w13.variable_selection_set(worker_for(model), "Model", {
    "group": path, "selection": {"kind": "named", "component": "comp2", "tag": "sel"}}))
print(f"P2 state: model_ref={group.model_ref!r}, named_ref={group.named_ref!r} (asked comp2)")

print("== P3 positive control: GLOBAL group, named spec component comp1 (legitimate) ==")
model, comp1, comp2 = ab_model()
ggroup = FNode(tag="varg", expressions={})
model.collections["variable"].items["varg"] = ggroup
gpath = {"segments": [{"collection": "variable", "tag": "varg"}]}
show("P3", lambda: w13.variable_selection_set(worker_for(model), "Model", {
    "group": gpath, "selection": {"kind": "named", "component": "comp1", "tag": "sel"}}))
print(f"P3 state: model_ref={ggroup.model_ref!r}, named_ref={ggroup.named_ref!r}")

print("== P4 explicit: group comp1, explicit spec component comp2 ==")
model, comp1, comp2 = ab_model()
show("P4", lambda: w13.variable_group_create(worker_for(model), "Model", {
    "tag": "varE", "component": "comp1",
    "selection": {"kind": "explicit", "component": "comp2", "geometry": "g2",
                  "entity_dimension": 2, "entities": [7]}}))
nodeE = comp1.collections["variable"].items.get("varE")
print(f"P4 state: varE in comp1={nodeE is not None}, entities_={getattr(nodeE, 'entities_', None)!r}, "
      f"geom={getattr(nodeE, 'geometry_tag', None)!r} (caller asked comp2/g2)")

print("== P5 inherited: group comp1, inherited spec component comp2 ==")
model, comp1, comp2 = ab_model()
show("P5", lambda: w13.variable_group_create(worker_for(model), "Model", {
    "tag": "varI", "component": "comp1",
    "selection": {"kind": "inherited", "component": "comp2"}}))
nodeI = comp1.collections["variable"].items.get("varI")
print(f"P5 state: varI in comp1={nodeI is not None}, inheriting_={getattr(nodeI, 'inheriting_', None)} "
      f"model_ref={getattr(nodeI, 'model_ref', None)!r} (asked comp2)")

print("== P6 reverse asymmetry: group comp1, named spec comp2 but tag only in comp1 ==")
model, comp1, comp2 = ab_model()
del comp2.collections["selection"].items["sel"]
show("P6", lambda: w13.variable_group_create(worker_for(model), "Model", {
    "tag": "varR", "component": "comp1",
    "selection": {"kind": "named", "component": "comp2", "tag": "sel"}}))
print(f"P6 note: the binding would resolve comp1's 'sel' (which exists); the gate refused on comp2, "
      f"showing validation and binding use different components in both directions")

