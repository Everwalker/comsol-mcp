"""T21 public component-scoped G3 list paths, in-process daemon/fake Worker.

Each test owns a disposable project and a fake model; no service, socket,
COMSOL engine or historical model is contacted.  The target request goes
through ControlDaemon.dispatch and project admission, not backend.invoke.
"""
from __future__ import annotations

import json
import pytest

from tests.test_dynamic_project_auth_qwen_20261109 import FULL, make_harness


@pytest.mark.parametrize(("operation", "list_field", "expected_tag"), [
    ("definition.coordinate_manage", "coordinate_systems", "sys1"),
    ("definition.coupling_manage", "couplings", "cpl1"),
    ("definition.pair_manage", "pairs", "pairA"),
])
def test_component_scoped_list_accepts_component_node_path(
        tmp_path, monkeypatch, operation, list_field, expected_tag):
    harness = make_harness(tmp_path, "g3-list-" + operation.replace(".", "-"), monkeypatch, FULL)
    try:
        request = {"action": "list", "path": {"segments": [{"collection": "component", "tag": "comp1"}]}}
        result = harness.call(operation, request, "list-target")
        assert result["success"] is True, f"{json.dumps(result, sort_keys=True)}\nengine_calls={harness.witness.calls!r}"
        assert result["data"]["component"] == "comp1", result
        assert expected_tag in [row["tag"] for row in result["data"][list_field]], result
        assert harness.witness.calls, "a successful list must actually consult the fake engine"
    finally:
        harness.daemon.close()


@pytest.mark.parametrize("operation", [
    "definition.coordinate_manage", "definition.coupling_manage", "definition.pair_manage",
])
def test_component_scoped_list_rejects_unresolved_extra_component(tmp_path, monkeypatch, operation):
    harness = make_harness(tmp_path, "g3-extra-" + operation.replace(".", "-"), monkeypatch, FULL)
    try:
        request = {"action": "list", "path": {"segments": [
            {"collection": "component", "tag": "comp1"},
            {"collection": "component", "tag": "not-a-child"},
        ]}}
        result = harness.call(operation, request, "list-extra")
        assert result["success"] is False, f"{json.dumps(result, sort_keys=True)}\nengine_calls={harness.witness.calls!r}"
        assert result["error"]["code"] in {"INVALID_NODE_PATH", "INVALID_REQUEST"}, result
        assert not harness.witness.calls
    finally:
        harness.daemon.close()


def test_component_list_public_positive_control(tmp_path, monkeypatch):
    harness = make_harness(tmp_path, "g3-list-positive", monkeypatch, FULL)
    try:
        result = harness.call("definition.component_manage", {"action": "list"}, "list-control")
        assert result["success"] is True, result
        assert harness.witness.calls
    finally:
        harness.daemon.close()
