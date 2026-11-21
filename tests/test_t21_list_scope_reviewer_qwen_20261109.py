"""T21 list-scope independent reviewer targeted tests (Qwen reviewer, 2026-11-09).

These are the reviewer's own fake-engine checks, deliberately disjoint from
tests/test_t21_list_scope_candidate_qwen_20261109.py: they probe scope edges the
candidate suite does not name (non-Mapping path, extra path keys, accessor in a
second segment, case-sensitive conflict detection, over-length/typed tags) and
re-assert, independently, one public-route refusal envelope and one public
positive control.  No COMSOL, Worker, socket, or historical model is contacted;
the daemon-side pre-dispatch model snapshot runs through the harness's separate
fake SnapshotAdapter, so engine-call assertions here are callback-scope only.
"""
from __future__ import annotations

import json

import pytest

from comsol_mcp import _g3_w14 as w14
from comsol_mcp._execution_contract import PreWriteRefusal
from tests.test_dynamic_project_auth_qwen_20261109 import FULL, make_harness

LIST_OPS = ("definition.coordinate_manage", "definition.coupling_manage", "definition.pair_manage")
COLLECTIONS = {"definition.coordinate_manage": "coordSystem",
               "definition.coupling_manage": "cpl",
               "definition.pair_manage": "pair"}
LIST_TAGS = {"definition.coordinate_manage": "sys1",
             "definition.coupling_manage": "cpl1",
             "definition.pair_manage": "pairA"}


def component_path(tag: str = "comp1") -> dict:
    return {"segments": [{"collection": "component", "tag": tag}]}


# --------------------------------------------------------------------------
# reviewer edges on the internal helper surface (direct operation calls)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("operation", LIST_OPS)
@pytest.mark.parametrize("bad_path", [
    "comp1",                                              # not a Mapping
    123,                                                  # not a Mapping
    {"segments": {"collection": "component", "tag": "comp1"}},  # not a list
    {"segments": [{"collection": "component", "tag": "comp1"}], "extra": True},
], ids=["str", "int", "segments_not_list", "extra_keys"])
def test_unparsable_path_is_a_clean_validation_refusal(operation, bad_path):
    worker, _, comp = w14_world()
    with pytest.raises(PreWriteRefusal) as info:
        w14.OPERATIONS[operation](worker, "Model", {"action": "list", "path": bad_path})
    assert info.value.code == "INVALID_NODE_PATH", info.value
    assert info.value.stage == "validation"
    assert info.value.details.get("cause_code") == "INVALID_NODE_PATH", info.value.details
    assert comp.collections[COLLECTIONS[operation]].calls == []


@pytest.mark.parametrize("operation", LIST_OPS)
def test_accessor_in_second_segment_is_refused(operation):
    worker, _, comp = w14_world()
    path = {"segments": [{"collection": "component", "tag": "comp1"},
                         {"accessor": "geom"}]}
    with pytest.raises(PreWriteRefusal) as info:
        w14.OPERATIONS[operation](worker, "Model", {"action": "list", "path": path})
    assert info.value.code == "INVALID_NODE_PATH", info.value
    assert comp.collections[COLLECTIONS[operation]].calls == []


@pytest.mark.parametrize("operation", LIST_OPS)
def test_conflict_detection_is_exact(operation):
    """A case-different component must not silently override the path tag."""
    worker, _, comp = w14_world()
    with pytest.raises(PreWriteRefusal) as info:
        w14.OPERATIONS[operation](worker, "Model",
                                  {"action": "list", "component": "COMP1",
                                   "path": component_path("comp1")})
    assert info.value.code == "INVALID_REQUEST", info.value
    assert comp.collections[COLLECTIONS[operation]].calls == []


@pytest.mark.parametrize("tag,label", [
    ("c" * 64, "over_length_64"),
    (" comp1", "leading_space"),
    ("comp1 ", "trailing_space"),
], ids=["over_length_64", "leading_space", "trailing_space"])
def test_path_tag_outside_tag_grammar_is_refused(tag, label):
    worker, _, comp = w14_world()
    with pytest.raises(PreWriteRefusal) as info:
        w14.OPERATIONS["definition.coordinate_manage"](
            worker, "Model", {"action": "list", "path": component_path(tag)})
    assert info.value.code == "INVALID_REQUEST", (label, info.value.code)
    assert comp.collections["coordSystem"].calls == []


def test_typed_tag_segment_is_invalid_node_path():
    worker, _, comp = w14_world()
    path = {"segments": [{"collection": "component", "tag": 123}]}
    with pytest.raises(PreWriteRefusal) as info:
        w14.OPERATIONS["definition.coordinate_manage"](
            worker, "Model", {"action": "list", "path": path})
    assert info.value.code == "INVALID_NODE_PATH", info.value
    assert comp.collections["coordSystem"].calls == []


# --------------------------------------------------------------------------
# reviewer-owned public-route sanity: one refusal envelope, one positive control
# --------------------------------------------------------------------------


def test_public_repeated_component_refusal_envelope_pair(tmp_path, monkeypatch):
    harness = make_harness(tmp_path, "rev-bad-pair", monkeypatch, FULL)
    try:
        path = {"segments": [{"collection": "component", "tag": "comp1"},
                             {"collection": "component", "tag": "comp1"}]}
        result = harness.call("definition.pair_manage", {"action": "list", "path": path}, "rev-bad")
        assert result["success"] is False, json.dumps(result, sort_keys=True)[:1500]
        assert harness.error_code(result) == "INVALID_NODE_PATH", result["error"]
        assert result["error"]["stage"] == "validation", result["error"]
        assert harness.witness.calls == [], harness.witness
        data = result["data"]
        assert data["refused"] is True and data["dispatch_stage"] == "validation"
        assert data["execution_state_unknown"] is False
        assert data["witness"]["engine_calls"] == 0
        assert data["witness"]["mutation_issued"] is False
        assert result["execution"]["dirty"] is False
    finally:
        harness.daemon.close()


def test_public_positive_control_pair_uses_the_engine(tmp_path, monkeypatch):
    harness = make_harness(tmp_path, "rev-ok-pair", monkeypatch, FULL)
    try:
        result = harness.call("definition.pair_manage",
                              {"action": "list", "path": component_path()}, "rev-ok")
        assert result["success"] is True, json.dumps(result, sort_keys=True)[:1500]
        assert result["data"]["component"] == "comp1"
        assert "pairA" in [row["tag"] for row in result["data"]["pairs"]]
        assert harness.witness.calls, "positive control must consult the fake engine"
        assert result["execution"]["dirty"] is False
    finally:
        harness.daemon.close()


def w14_world():
    """Same in-process fake world the W14 suite uses (no engine, no sockets)."""
    from tests.test_g3_w14 import world
    return world()
