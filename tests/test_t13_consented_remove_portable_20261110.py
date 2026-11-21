"""T13 portable regression tests for the reviewed consented-remove candidate V3.

This is a single standalone module: the exact bodies and decorators of the nine
selected candidate tests, plus the smallest exact fake constants, classes and
helper definitions they call, copied verbatim from the three reviewed candidate
test files (test_t13_consent_delete_candidate_20261109,
test_t13_retention_honesty_regressions_20261109 and
test_t13_field_cleanup_qwen_20261109).  It imports only real product modules;
the fake worker, FakeNode/FakeList and helpers are local to this module and
need no task-specific environment variables or pytest tmp fixtures.

These are in-process fake tests: every receiver is a deterministic Python
double living entirely in memory.  They explicitly provide NO native COMSOL
evidence, NO project-authorization guarantee, NO daemon/transport coverage,
NO hidden-object-identity guarantee and NO external-reference guarantee.  This
module cannot independently validate native COMSOL behaviour and cannot
guarantee non-target preservation beyond the compared fields the fake route
itself declares.
"""
from __future__ import annotations

import copy as _copy

import pytest

from comsol_mcp import _g2_public_api as api
from comsol_mcp import _g2_registry
from comsol_mcp._execution_contract import PreWriteRefusal
from comsol_mcp._g2_engine import execute_node_action, prepare_node_action

MODEL_ENTITY_LIST = "com.comsol.model.ModelEntityList"
ACKNOWLEDGEMENTS = [
    "external_reference_impact_unverified",
    "descendant_scope_follows_engine_semantics",
    "enumeration_scope_candidate_vocabulary",
]
MODEL_REF = {"schema_version": 1, "session_id": "session", "server_instance_id": "server",
             "model_tag": "m", "generation": 3}
TARGET = {"segments": [{"collection": "component", "tag": "c1"},
                       {"collection": "geom", "tag": "g1"},
                       {"collection": "feature", "tag": "f1"}]}


def consent(path=TARGET, scope="target_subtree", acknowledge=None):
    return {"scope": scope, "target_path": _copy.deepcopy(path),
            "acknowledge": list(ACKNOWLEDGEMENTS if acknowledge is None else acknowledge)}


class FakeList:
    def __init__(self, items=()):
        self.items = {item.tag_value: item for item in items}
        self.order = [item.tag_value for item in items]
        self.calls = []

    def tags(self):
        return list(self.order)

    def remove(self, tag):
        self.calls.append(("remove", tag))
        del self.items[tag]
        self.order.remove(tag)


class FakeNode:
    def __init__(self, tag, type_id="Rectangle", collections=None):
        self.tag_value = tag
        self.type_id = type_id
        self.display = f"label-{tag}"
        for name, listing in (collections or {}).items():
            def accessor(tag=None, listing=listing):
                return listing if tag is None else listing.items[tag]
            setattr(self, name, accessor)

    def tag(self):
        return self.tag_value

    def getType(self):
        return self.type_id

    def label(self):
        return self.display


def make_worker():
    """m: feature[a,b], component[c1]; c1: geom[g1]; g1: feature[f1,f2]."""
    f1 = FakeNode("f1")
    f2 = FakeNode("f2")
    geom_list = FakeList([f1, f2])
    geom_node = FakeNode("g1", "Geometry", {"feature": geom_list})
    geom_wrap = FakeList([geom_node])
    comp = FakeNode("c1", "Component2D", {"geom": geom_wrap})
    comp_wrap = FakeList([comp])
    root = FakeNode("m", "Model", {"feature": FakeList([FakeNode("a"), FakeNode("b")]),
                                   "component": comp_wrap})

    class Worker:
        generation = 7

        def __init__(self):
            self.described = []
            self.model_calls = 0

        def client(self):
            return self

        def model(self, tag):
            assert tag == "m"
            self.model_calls += 1
            return root

        def describe_public(self, node):
            self.described.append(node)
            if isinstance(node, FakeList):
                rows = [{"interface": MODEL_ENTITY_LIST, "method": "remove",
                         "parameters": ["java.lang.String"], "returns": "void"},
                        {"interface": MODEL_ENTITY_LIST, "method": "tags",
                         "parameters": [], "returns": "[Ljava.lang.String;"}]
                interfaces = [MODEL_ENTITY_LIST, "com.comsol.model.PrimitiveModelEntity"]
            else:
                rows = [{"interface": api.MODEL_ENTITY, "method": name, "parameters": [],
                         "returns": "java.lang.String"} for name in ("tag", "getType", "label")]
                interfaces = [api.MODEL_ENTITY, "com.comsol.model.PrimitiveModelEntity"]
            return {"runtime_version": api.VERSION, "interfaces": interfaces, "methods": rows,
                    "metadata_complete": True,
                    "metadata_limits": {"interfaces": 1024, "methods": 8192}}

    return Worker(), root, geom_list, f1, f2


def prepared_for(worker, body):
    return prepare_node_action(worker, "m", "node.remove", body, model_revision=0)


def body_for(path=TARGET, authorization=..., cascade=None):
    body = {"path": _copy.deepcopy(path)}
    if cascade is not None:
        body["cascade"] = cascade
    if authorization is not ...:
        body["authorization"] = _copy.deepcopy(authorization)
    return body


def _remove_then_replace(geom_list, f1_tag, new_f2):
    """Engine-side behaviour: authorized remove of f1 plus collateral swap of f2."""
    def remove_then_replace(tag):
        geom_list.calls.append(("remove", tag))
        del geom_list.items[tag]
        geom_list.order.remove(tag)
        del geom_list.items["f2"]
        geom_list.order.remove("f2")
        geom_list.items["f2"] = new_f2
        geom_list.order.append("f2")
    geom_list.remove = remove_then_replace


MISLEADING_FIELD = "new_paths_inside_authorized_subtree"

# Closed key set of the retention block after the vacuous field is removed.
POST_RETENTION_KEYS = {
    "observation_scope", "compared_fields", "unchanged_claim", "unverified_dimensions",
    "non_target_paths_before", "non_target_paths_absent_after", "missing_non_target_paths",
    "non_target_paths_metadata_changed", "metadata_changes",
    "non_target_paths_unchanged_on_compared_fields", "unexpected_new_paths",
    "target_absent", "subtree_rows_before", "subtree_rows_absent_after",
    "subtree_rows_still_observed", "snapshot_pages_pre", "snapshot_pages_post",
    "enumeration_scope",
}


def _run(worker, body=...):
    prepared = prepare_node_action(
        worker, "m", "node.remove",
        body_for(authorization=consent()) if body is ... else body, model_revision=0)
    return prepared, execute_node_action(worker, "m", "node.remove", prepared,
                                         model_ref=MODEL_REF)


# --- consent gating (pure, must not touch any receiver) --------------------

def test_missing_consent_still_refuses_with_the_historical_precise_gap():
    worker, root, geom_list, f1, f2 = make_worker()
    with pytest.raises(PreWriteRefusal) as exc:
        prepared_for(worker, body_for())
    assert exc.value.code == "API_UNSUPPORTED"
    assert exc.value.details["receiver_accessed"] is False
    assert exc.value.details["complete"] is False
    obligations = " ".join(exc.value.details["unknown_obligations"])
    assert "descendant-scope" in obligations
    assert "incoming expression/selection/consumer graph" in obligations
    assert "child enumeration" in obligations
    assert "cross-list retention" in obligations
    assert exc.value.details["reviewed_alternative"] == "model-root NodeGroup structural ungroup only"
    assert worker.model_calls == 0 and worker.described == []
    assert geom_list.calls == [] and geom_list.order == ["f1", "f2"]


# --- consented removal: complete route -------------------------------------

def test_consented_nested_removal_dispatches_one_real_remove_and_proves_retention():
    worker, root, geom_list, f1, f2 = make_worker()
    prepared = prepared_for(worker, body_for(authorization=consent()))
    result = execute_node_action(worker, "m", "node.remove", prepared, model_ref=MODEL_REF)
    assert result["success"] is True
    data = result["data"]
    assert data["semantic_operation"] == "com.comsol.model.ModelEntityList.remove(String)"
    assert "ungroup" not in data["semantic_operation"]
    assert geom_list.calls == [("remove", "f1")]
    assert geom_list.order == ["f2"]
    assert root.feature().order == ["a", "b"] and root.component().order == ["c1"]
    assert data["removed_path"] == TARGET
    assert data["cascade"] is False
    assert data["mutation_dispatch"]["request_issued"] is True
    assert data["retention"]["missing_non_target_paths"] == []
    assert data["retention"]["target_absent"] is True
    # honest field names only: the proof compares path presence plus the
    # declared compared_fields, and must not imply full object retention
    assert data["retention"]["compared_fields"] == ["path", "tag", "type_id", "label"]
    assert (data["retention"]["non_target_paths_unchanged_on_compared_fields"]
            == data["retention"]["non_target_paths_before"])
    assert "non_target_retained" not in data["retention"]
    assert data["reference_risk"]["level"] == "UNSCANNED"
    assert data["dependency_check"]["complete"] is False
    assert data["status"] == "INCOMPLETE" and data["complete"] is False
    fields = {row["field"] for row in data["coverage"]}
    assert any("incoming" in field for field in fields)
    assert any("candidate" in field or "enumeration" in field for field in fields)
    assert any("descendant" in field for field in fields)


def test_hidden_child_collection_stays_invisible_and_is_disclosed_not_claimed():
    worker, root, geom_list, f1, f2 = make_worker()
    hidden = FakeList([FakeNode("secret")])
    f1.secretChildList = lambda: hidden
    prepared = prepared_for(worker, body_for(authorization=consent()))
    result = execute_node_action(worker, "m", "node.remove", prepared, model_ref=MODEL_REF)
    assert result["success"] is True
    retention = result["data"]["retention"]
    assert retention["enumeration_scope"] == "candidate_collection_vocabulary"
    reasons = " ".join(str(row.get("reason", "")) for row in result["data"]["coverage"])
    assert "candidate" in reasons or "other accessor names" in reasons
    # The candidate vocabulary never probed the hidden list before or after.
    assert hidden.calls == []


# --- CE-A: in-place non-target replacement must NOT be success --------------

def test_ce_a_in_place_non_target_replacement_is_not_success():
    worker, root, geom_list, f1, f2 = make_worker()
    _remove_then_replace(geom_list, "f1", FakeNode("f2", "ReplacedWithCircle"))
    prepared = prepare_node_action(worker, "m", "node.remove",
                                   body_for(authorization=consent()), model_revision=0)
    result = execute_node_action(worker, "m", "node.remove", prepared, model_ref=MODEL_REF)
    assert result["success"] is False, result["data"].get("retention")
    assert result.get("execution_state_unknown") is True
    assert result.get("partial_change") is True
    data = result["data"]
    assert data["mutation_dispatch"]["request_issued"] is True
    changes = data["retention"]["metadata_changes"]
    f2_change = next((c for c in changes if c["path"]["segments"][-1]["tag"] == "f2"), None)
    assert f2_change is not None, changes
    assert f2_change["fields"]["type_id"]["before"] == "Rectangle"
    assert f2_change["fields"]["type_id"]["after"] == "ReplacedWithCircle"
    assert data["retention"]["missing_non_target_paths"] == []
    assert "general_remove_failure" in data["evidence"]
    # exactly one authorized remove dispatch; the swap is engine-side collateral
    assert geom_list.calls == [("remove", "f1")]


# --- positive control: honest observation scope, no overclaim ---------------

def test_positive_control_declares_observed_fields_and_leaves_identity_unverified():
    worker, root, geom_list, f1, f2 = make_worker()
    prepared = prepare_node_action(worker, "m", "node.remove",
                                   body_for(authorization=consent()), model_revision=0)
    result = execute_node_action(worker, "m", "node.remove", prepared, model_ref=MODEL_REF)
    assert result["success"] is True
    data = result["data"]
    assert geom_list.calls == [("remove", "f1")]
    retention = data["retention"]
    assert retention["missing_non_target_paths"] == []
    assert retention["unexpected_new_paths"] == []
    assert retention["metadata_changes"] == []
    # explicit declaration of what the retention proof actually observed
    assert retention["compared_fields"] == ["path", "tag", "type_id", "label"]
    assert "candidate-vocabulary" in retention["observation_scope"]
    unverified = " ".join(retention["unverified_dimensions"])
    assert "identity" in unverified
    assert "external reference" in unverified
    # the misleading "fully retained" implication must be gone
    assert "non_target_retained" not in retention
    assert "not an object-identity or property claim" in retention["unchanged_claim"]
    assert any(row["status"] == "UNVERIFIED" and "identity" in row["field"] for row in data["coverage"])
    assert data["status"] == "INCOMPLETE" and data["complete"] is False
    # and the non-target nodes really survived on the engine side
    assert geom_list.order == ["f2"] and root.feature().order == ["a", "b"]


# --- legitimate consequence of the AUTHORIZED subtree removal ---------------

def test_target_subtree_internal_disappearance_is_not_escalated():
    worker, root, geom_list, f1, f2 = make_worker()
    children = FakeList([FakeNode("s1"), FakeNode("s2")])
    f1.feature = lambda tag=None: children if tag is None else children.items[tag]  # visible to the candidate vocabulary
    prepared = prepare_node_action(worker, "m", "node.remove",
                                   body_for(authorization=consent()), model_revision=0)
    result = execute_node_action(worker, "m", "node.remove", prepared, model_ref=MODEL_REF)
    assert result["success"] is True
    retention = result["data"]["retention"]
    assert retention["subtree_rows_before"] >= 3
    assert retention["subtree_rows_absent_after"] >= 3
    # disappearance under the authorized prefix is the authorized effect,
    # never a missing NON-target path
    assert retention["missing_non_target_paths"] == []
    assert retention["unexpected_new_paths"] == []
    assert retention["metadata_changes"] == []


# --- the misleading field itself -------------------------------------------

def test_clean_removal_retention_keys_closed_without_misleading_field():
    """Positive control on a healthy fake: the retention block must expose the
    honest closed key set and must NOT publish the always-empty field."""
    worker, root, geom_list, f1, f2 = make_worker()
    _prepared, result = _run(worker)
    assert result["success"] is True
    retention = result["data"]["retention"]
    assert MISLEADING_FIELD not in retention, (
        "new_paths_inside_authorized_subtree is structurally always empty "
        "(subtree_keys come from the pre-snapshot; new_paths excludes it)")
    assert set(retention) == POST_RETENTION_KEYS
    # the surviving guarantee: new paths outside the subtree are still checked
    assert retention["unexpected_new_paths"] == []
    assert retention["target_absent"] is True


# --- authorization boundaries stay byte-for-byte ----------------------------

def test_route_boundaries_refuse_without_receiver_access():
    worker, root, geom_list, f1, f2 = make_worker()
    group_path = {"segments": [{"collection": "nodeGroup", "tag": "g1"}]}
    with pytest.raises(PreWriteRefusal) as exc:
        prepare_node_action(worker, "m", "node.remove",
                            body_for(path=group_path, authorization=consent(path=group_path)),
                            model_revision=0)
    assert exc.value.code == "INVALID_REQUEST"
    assert exc.value.details["receiver_accessed"] is False
    nested = [{"collection": "component", "tag": "c1"}, {"collection": "nodeGroup", "tag": "grp"}]
    with pytest.raises(PreWriteRefusal) as exc:
        prepare_node_action(worker, "m", "node.remove",
                            body_for(path=nested, authorization=consent(path=nested)),
                            model_revision=0)
    assert exc.value.code == "INVALID_NODE_PATH"
    with pytest.raises(PreWriteRefusal) as exc:
        prepare_node_action(worker, "m", "node.remove",
                            body_for(authorization=consent(), cascade=True), model_revision=0)
    assert exc.value.code == "API_UNSUPPORTED"
    assert geom_list.calls == [] and geom_list.order == ["f1", "f2"]
    assert worker.model_calls == 0


# --- published contract surfaces stay honest and closed ---------------------

def test_published_node_remove_schemas_stay_closed():
    entries = {entry.operation_id: entry for entry in _g2_registry._catalog_entries()}
    eff = _g2_registry._effective_input_schema(entries["node.remove"].input_schema, "node.remove")
    auth = eff["properties"]["authorization"]
    assert auth["required"] == ["scope", "target_path", "acknowledge"]
    assert auth["properties"]["scope"]["enum"] == ["target_subtree"]
    assert auth["properties"]["acknowledge"]["maxItems"] == 3
    assert auth["additionalProperties"] is False
    assert eff["properties"]["cascade"]["default"] is False
    data = _g2_registry._unit_a_data_schema("node.remove")
    assert data["additionalProperties"] is False
    for field in ("authorization", "retention", "reference_risk", "readback", "mutation_dispatch"):
        assert field in data["properties"]
