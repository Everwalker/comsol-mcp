"""T21 Pair read UNKNOWN through the managed daemon/service with fake Worker.

The control daemon, registry, managed backend and execution service are real;
COMSOL and Worker endpoints are isolated in-process test doubles. No real
service, socket, COMSOL process, or historical model is contacted.
"""
from __future__ import annotations

from comsol_mcp._java_worker import JavaWorkerTimeout
from tests.test_t20_artifact_boundary import FList, FNode, _call, _envelope, _make_project
from tests.test_t21_pair_selection_20261108 import FPairNode, pair_path


def test_pair_inspect_worker_unknown_reaches_real_managed_outcome(tmp_path, monkeypatch):
    daemon, project, _, model, _, execution = _make_project(tmp_path, "t21-read-unknown", monkeypatch)
    trace: list[str] = []

    class FaultPair(FPairNode):
        def source(self):
            trace.append("source")
            trace.append("UNKNOWN")
            raise JavaWorkerTimeout(
                "synthetic source timeout",
                reply={"status": "UNKNOWN", "execution_state_unknown": True,
                       "request_id": "synthetic-t21-read-unknown"},
            )

        def destination(self):
            trace.append("destination")
            return super().destination()

    try:
        pairs = FList()
        pairs.items["pairA"] = FaultPair(tag="pairA", type_id="Contact")
        component = FNode(tag="comp1", type_id="Component", collections={"pair": pairs})
        model.collections["component"].items["comp1"] = component
        result = _call(daemon, "definition.pair_manage",
                       {"action": "inspect", "path": pair_path("pairA")},
                       _envelope(project, execution, "t21-read-unknown"))
        assert trace == ["source", "UNKNOWN"], result
        assert result["success"] is False, result
        assert result["error"]["code"] == "EXECUTION_STATE_UNKNOWN", result
        assert result["data"]["execution_state_unknown"] is True, result
        assert result["data"]["partial_change"] is False, result
        assert result["data"]["destination"]["probe_skipped"] is True, result
    finally:
        daemon.close()


def test_pair_inspect_clean_still_succeeds_via_managed_route(tmp_path, monkeypatch):
    daemon, project, _, model, _, execution = _make_project(tmp_path, "t21-read-clean", monkeypatch)
    try:
        pairs = FList()
        pairs.items["pairA"] = FPairNode(tag="pairA", type_id="Contact")
        model.collections["component"].items["comp1"] = FNode(
            tag="comp1", type_id="Component", collections={"pair": pairs})
        result = _call(daemon, "definition.pair_manage",
                       {"action": "inspect", "path": pair_path("pairA")},
                       _envelope(project, execution, "t21-read-clean"))
        assert result["success"] is True, result
        assert result["data"]["source"]["available"] is True, result
        assert result["data"]["destination"]["available"] is True, result
    finally:
        daemon.close()


def test_pair_inspect_unknown_via_backend_only_without_daemon_admission(tmp_path, monkeypatch):
    """Localize backend UNKNOWN handling; this deliberately skips daemon auth."""
    daemon, project, _, model, _, execution = _make_project(tmp_path, "t21-backend-only", monkeypatch)
    trace: list[str] = []

    class FaultPair(FPairNode):
        def source(self):
            trace.extend(("source", "UNKNOWN"))
            raise JavaWorkerTimeout(
                "synthetic source timeout",
                reply={"status": "UNKNOWN", "execution_state_unknown": True,
                       "request_id": "synthetic-t21-backend-only"},
            )

        def destination(self):
            trace.append("destination")
            return super().destination()

    try:
        pairs = FList()
        pairs.items["pairA"] = FaultPair(tag="pairA", type_id="Contact")
        model.collections["component"].items["comp1"] = FNode(
            tag="comp1", type_id="Component", collections={"pair": pairs})
        result = daemon.backend.invoke(
            "registry_call",
            {"operation_id": "definition.pair_manage",
             "arguments": {"action": "inspect", "path": pair_path("pairA")}},
            _envelope(project, execution, "t21-backend-only"), "test-t21-backend-only", None,
        )
        assert trace == ["source", "UNKNOWN"], result
        assert result["success"] is False, result
        assert result["error"]["code"] == "EXECUTION_STATE_UNKNOWN", result
        assert result["data"]["execution_state_unknown"] is True, result
        assert result["data"]["partial_change"] is False, result
        assert result["data"]["destination"]["probe_skipped"] is True, result
    finally:
        daemon.close()
