"""Contract tests for the public C11/C14 node/probe acceptance helper.

The fake client below keeps an independent native-like store.  Responses are produced from that
store on list/get/history calls, so the helper cannot pass by merely echoing request bodies.
These tests do not start COMSOL.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from tools.g33_node_probe_cases import (
    _NodeLedger,
    _axis_covers_stored_times,
    _close_value,
    _cutpoint_mid_set_failure,
    _history_time_axis,
    run_cutplane_cases,
    run_node_probe_cases,
)


def _path(collection: str, tag: str) -> dict[str, Any]:
    return {"segments": [{"accessor": "result"}, {"collection": collection, "tag": tag}]}


def _probe_path(tag: str, component: str = "comp1") -> dict[str, Any]:
    return {"segments": [{"collection": "component", "tag": component}, {"collection": "probe", "tag": tag}]}


class IndependentPublicClient:
    """Small public ActionClient double with independent list/get readback."""

    def __init__(self, *, bound: bool = True, cascade_probe_table: bool = False,
                 cascade_dataset: bool = False, omit_at_points_dataset: bool = False) -> None:
        self.state = {"ref": {"model": "chain_a"}} if bound else {}
        self.cascade_probe_table = cascade_probe_table
        self.cascade_dataset = cascade_dataset
        self.omit_at_points_dataset = omit_at_points_dataset
        self.calls: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
        self.datasets: dict[str, dict[str, Any]] = {
            "dset1": {"type_id": "Solution", "properties": {"solution": "sol1", "comp": "comp1", "geom": "geom1"}},
        }
        self.tables: dict[str, dict[str, Any]] = {
            "user_table": {"type_id": "Table", "headers": ["user"], "data": [[7.0]], "imaginary": None},
        }
        self.numerical = {"user_num": {"type_id": "EvalGlobal"}}
        self.probes: dict[tuple[str, str], dict[str, Any]] = {
            ("comp1", "user_probe"): {"type_id": "Domain", "properties": {"expr": "T", "unit": "K"}},
        }

    @staticmethod
    def _ok(data: dict[str, Any]) -> dict[str, Any]:
        return {"success": True, "data": data}

    @staticmethod
    def _err(code: str) -> dict[str, Any]:
        return {"success": False, "error": {"code": code, "message": code}}

    @staticmethod
    def _readback(type_id: str, properties: dict[str, Any]) -> dict[str, Any]:
        return {"readable": True, "match": True, "type_id": type_id,
                "properties": [{"property": key, "readable": True, "match": True, "value": value}
                               for key, value in properties.items()]}

    def _dataset_list(self) -> dict[str, Any]:
        items = []
        for tag, node in self.datasets.items():
            props = node["properties"]
            items.append({"tag": tag, "type_id": node["type_id"], "solution": props.get("solution") or props.get("data"),
                          "component": props.get("comp"), "geometry": props.get("geom"), "path": _path("dataset", tag)})
        return self._ok({"datasets": items, "count": len(items), "tags": list(self.datasets)})

    def _table_list(self) -> dict[str, Any]:
        items = [{"tag": tag, "path": _path("table", tag), "headers": node["headers"],
                  "readback": {"readable": True, "headers": node["headers"], "data": node["data"],
                               "imaginary": node["imaginary"]}}
                 for tag, node in self.tables.items()]
        return self._ok({"action": "list", "tables": items, "count": len(items), "tags": list(self.tables),
                         "readback": {"readable": True}})

    def _probe_list(self) -> dict[str, Any]:
        items = [{"tag": tag, "component": comp, "type_id": node["type_id"],
                  "native_type": node["type_id"], "expression": node["properties"].get("expr"),
                  "table": node["properties"].get("table"), "path": _probe_path(tag, comp)}
                 for (comp, tag), node in self.probes.items()]
        return self._ok({"probes": items, "count": len(items), "tags": [item["tag"] for item in items],
                         "readback": {"readable": True}})

    async def action(self, operation: str, body: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        self.calls.append((operation, body, kwargs))
        assert kwargs.get("reconcile") is False
        if operation == "dataset.list":
            return self._dataset_list()
        if operation == "result.numerical_manage":
            return self._ok({"action": "list", "features": [{"tag": tag, **node} for tag, node in self.numerical.items()],
                             "count": len(self.numerical), "tags": list(self.numerical), "readback": {"readable": True}})
        if operation == "result.table_manage":
            action = body.get("action")
            if action == "list":
                return self._table_list()
            path = body.get("path", {})
            tag = path.get("segments", [{}, {}])[-1].get("tag")
            if action == "create":
                definition = body.get("definition", {})
                self.tables[tag] = {"type_id": "Table", "headers": list(definition.get("headers", [])),
                                    "data": definition.get("data", []), "imaginary": definition.get("imaginary")}
                node = self.tables[tag]
                return self._ok({"action": "create", "tag": tag, "type_id": "Table", "status": "APPLIED",
                                 "created": True, "readback": {"readable": True, "match": True,
                                    "type_id": "Table", "headers": node["headers"], "data": node["data"],
                                    "imaginary": node["imaginary"]}})
            if action == "get":
                node = self.tables.get(tag)
                if node is None:
                    return self._err("NODE_NOT_FOUND")
                data = node["data"]
                if node["imaginary"] is not None:
                    data = [[{"real": value, "imag": node["imaginary"][r][c]} for c, value in enumerate(row)]
                            for r, row in enumerate(data)]
                return self._ok({"action": "get", "tag": tag, "headers": node["headers"], "data": data,
                                 "imaginary": node["imaginary"], "readback": {"readable": True}})
            if action == "remove":
                if tag not in self.tables:
                    return self._err("NODE_NOT_FOUND")
                del self.tables[tag]
                return self._ok({"action": "remove", "tag": tag, "removed": True, "verified_removed": True,
                                 "status": "APPLIED", "readback": {"readable": True, "match": True}})
        if operation == "dataset.inspect":
            path = body.get("path", {})
            segments = path.get("segments", [])
            if not segments or segments[-1].get("collection") != "dataset":
                return self._err("INVALID_NODE_PATH")
            tag = segments[-1].get("tag")
            node = self.datasets.get(tag)
            if node is None:
                return self._err("NODE_NOT_FOUND")
            return self._ok({"tag": tag, "type_id": node["type_id"], "properties": dict(node["properties"]),
                             "path": _path("dataset", tag)})
        if operation == "dataset.solution_indices":
            path = body.get("path", {})
            tag = path.get("segments", [{}, {}])[-1].get("tag")
            if tag not in self.datasets:
                return self._err("NODE_NOT_FOUND")
            return self._ok({"dataset": tag, "solution": "sol1", "binding_complete": True,
                             "dataset_binding": {"dataset": tag, "solution": "sol1",
                                                  "component": "comp1", "geometry": "geom1",
                                                  "binding_complete": True},
                             "axis_metadata_complete": True, "pair_mapping_complete": True,
                             "inner_indices": [1], "outer_indices": [1], "solnum_pairs": [{"outer": 1, "inner": 1, "solnum": 1}]})
        if operation == "result.evaluate":
            spec = body.get("spec", {})
            solution = spec.get("solution", {})
            tag = solution.get("dataset")
            if tag not in self.datasets:
                return self._err("NODE_NOT_FOUND")
            if self.datasets[tag]["type_id"] == "Join":
                witness = {"mutation_issued": False, "engine_calls": 0, "methods": []}
                return {"success": False,
                        "data": {"refused": True, "dispatch_stage": "validation", "witness": witness},
                        "error": {"code": "API_UNSUPPORTED", "message": "Join raw evaluate unsupported",
                                  "stage": "validation", "details": {"witness": witness}}}
            expression = (spec.get("expressions") or [""])[0]
            if self.datasets[tag]["type_id"] in {"CutPoint2D", "CutLine2D"}:
                values = [323.15]
            elif self.datasets[tag]["type_id"] == "Join":
                values = [0.0]
            elif expression == "1":
                values = [1.0]
            else:
                values = [0.0]
            return self._ok({"dataset": tag, "solution": "sol1", "values": values,
                             "aggregate": spec.get("aggregate", "none")})
        if operation == "result.at_points":
            spec = body.get("spec", {})
            tag = spec.get("solution", {}).get("dataset")
            if tag not in self.datasets:
                return self._err("NODE_NOT_FOUND")
            count = len(body.get("points") or [])
            values = ([0.0] * count if self.datasets[tag]["type_id"] == "Join" else [323.15] * max(count, 1))
            response = {"solution": "sol1", "values": values, "points": body.get("points")}
            if not self.omit_at_points_dataset:
                response["dataset"] = tag
            return self._ok(response)
        if operation == "dataset.create":
            tag = body["tag"]
            definition = dict(body.get("definition", {}))
            if definition.get("comp") == "__g33_missing_component__":
                return self._err("NODE_NOT_FOUND")
            if tag in self.datasets:
                return self._err("TAG_CONFLICT")
            self.datasets[tag] = {"type_id": body["type_id"], "properties": definition}
            return self._ok({"tag": tag, "type_id": body["type_id"], "created": True, "status": "APPLIED",
                             "readback": self._readback(body["type_id"], definition)})
        if operation == "dataset.update":
            path = body["path"]; tag = path["segments"][-1].get("tag")
            node = self.datasets.get(tag)
            if node is None:
                return self._err("NODE_NOT_FOUND")
            definition = dict(body.get("definition", {}))
            if "__g33_unknown_property__" in definition:
                return self._err("INVALID_REQUEST")
            if tag.startswith("g33cycb") and str(definition.get("data", "")).startswith("g33cyca"):
                return self._err("DATASET_CYCLE_DETECTED")
            if definition.get("method") == "g33_invalid_method":
                # Native-like ordered setter failure: pointx reaches the backing store, the
                # invalid enum is rejected, and the trailing pointy setter is never attempted.
                applied = []
                if "pointx" in definition:
                    node["properties"]["pointx"] = definition["pointx"]
                    applied.append({"step": "property", "property": "pointx",
                                    "requested": definition["pointx"]})
                return self._ok({"tag": tag, "updated": True, "status": "PARTIAL_FAILURE",
                                 "partial_change": True, "applied": applied,
                                 "failed": [{"step": "property", "property": "method",
                                             "error": {"code": "ENGINE_CALL_FAILED",
                                                        "message": "invalid method enum"}}],
                                 "not_executed": [{"step": "property", "property": "pointy"}],
                                 "readback": {"readable": True, "match": False,
                                               "properties": [{"property": "pointx", "value": node["properties"].get("pointx")},
                                                              {"property": "method", "value": node["properties"].get("method")},
                                                              {"property": "pointy", "value": node["properties"].get("pointy")}]}})
            node["properties"].update(definition)
            return self._ok({"tag": tag, "updated": True, "status": "APPLIED",
                             "readback": self._readback(node["type_id"], definition)})
        if operation == "dataset.remove":
            tag = body["path"]["segments"][-1].get("tag")
            if tag not in self.datasets:
                return self._err("NODE_NOT_FOUND")
            del self.datasets[tag]
            if self.cascade_dataset and tag.startswith("g33cycb"):
                for dependent in [candidate for candidate in self.datasets if candidate.startswith("g33cyca")]:
                    del self.datasets[dependent]
            return self._ok({"tag": tag, "removed": True, "verified_removed": True, "status": "APPLIED",
                             "readback": {"readable": True, "match": True}})
        if operation == "probe.list":
            return self._probe_list()
        if operation == "probe.create":
            if "component" in body:
                return self._err("INVALID_REQUEST")
            tag = body["tag"]
            definition = body.get("definition", {})
            comp = definition.get("component") or "comp1"
            props = dict(definition.get("properties", {}))
            self.probes[(comp, tag)] = {"type_id": "Domain", "properties": props}
            return self._ok({"tag": tag, "type_id": "DomainProbe", "created": True, "status": "APPLIED",
                             "readback": {"readable": True, "match": True, "type": "Domain", "type_id": "DomainProbe"}})
        if operation == "probe.update":
            definition = body.get("definition", {})
            props = definition.get("properties", {})
            if "__g33_unknown_property__" in props:
                return self._err("INVALID_REQUEST")
            segment = body["path"]["segments"][-1]
            comp = body["path"]["segments"][0]["tag"]
            tag = segment["tag"]
            node = self.probes[(comp, tag)]
            node["properties"].update(props)
            applied = [{"step": "property", "property": key} for key in props]
            if "gen_result" in definition:
                applied.append({"step": "genResult", "solver": "sol1"})
            return self._ok({"tag": tag, "type_id": "Domain", "updated": True, "status": "APPLIED",
                             "applied": applied, "readback": {"readable": True, "match": True, "type": "Domain"}})
        if operation == "study.run":
            for node in self.probes.values():
                table_tag = node["properties"].get("table")
                if table_tag in self.tables:
                    # A real study run, rather than genResult preparation, produces the
                    # native probe table rows consumed by the later history read.
                    self.tables[table_tag]["headers"] = ["T"]
                    self.tables[table_tag]["data"] = [[323.15]]
                    self.tables[table_tag]["imaginary"] = None
            return self._ok({"status": "SUCCEEDED", "solved": True})
        if operation == "probe.history":
            segment = body["path"]["segments"][-1]
            comp = body["path"]["segments"][0]["tag"]
            tag = segment["tag"]
            node = self.probes[(comp, tag)]
            table_tag = node["properties"].get("table")
            table = self.tables.get(table_tag)
            if table is None:
                return self._err("NO_HISTORY")
            rows = table["data"]
            return self._ok({"tag": tag, "table": table_tag, "headers": table["headers"],
                             "rows": rows, "unit": "K", "time": None, "parameters": None,
                             "source": {"real_table_readback": True, "gen_result_called": False},
                             "readback": {"real_readable": True}})
        if operation == "probe.remove":
            segments = body["path"]["segments"]
            comp, tag = segments[0]["tag"], segments[1]["tag"]
            probe = self.probes.pop((comp, tag), None)
            if self.cascade_probe_table and isinstance(probe, dict):
                table_tag = probe.get("properties", {}).get("table")
                if isinstance(table_tag, str):
                    self.tables.pop(table_tag, None)
            return self._ok({"tag": tag, "removed": True, "verified_removed": True, "status": "APPLIED",
                             "readback": {"readable": True, "match": True}})
        raise AssertionError(f"unexpected public operation {operation}: {body}")


class UnknownHost:
    def __init__(self, *, terminal: bool) -> None:
        self.terminal = terminal
        self.calls: list[str] = []

    async def call(self, operation: str, body: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        self.calls.append(operation)
        if self.terminal and operation == "job_reconcile":
            return {"success": True, "data": {"metadata": {"reconciled_quiescent": True}}}
        if self.terminal and operation == "model_inspect":
            return {"success": True, "execution": {"model_ref": {"model": "chain_a"},
                                                       "revision": 1, "dirty": False}}
        return {"success": True, "data": {"status": "RUNNING"}}


class UnknownClient:
    def __init__(self, *, terminal: bool) -> None:
        self.state = {"ref": {"model": "chain_a"}}
        self.host = UnknownHost(terminal=terminal)
        self.action_calls = 0

    async def action(self, operation: str, body: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        self.action_calls += 1
        if self.action_calls == 1:
            return {"success": False, "error": {"code": "EXECUTION_STATE_UNKNOWN"},
                    "execution": {"job_id": "job-g33"}}
        return {"success": True, "data": {"status": "APPLIED", "readback": {"readable": True}}}


class UnknownPartialSetterClient(IndependentPublicClient):
    """Native-like partial setter that reports UNKNOWN after a real first property write."""

    def __init__(self) -> None:
        super().__init__()
        self.state = {"ref": {"model": "chain_a"}}
        self.host = UnknownHost(terminal=True)
        self.datasets["cpt"] = {
            "type_id": "CutPoint2D",
            "properties": {"data": "dset1", "method": "coords", "pointx": 0.025, "pointy": 0.005},
        }

    async def action(self, operation: str, body: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        if operation == "dataset.update":
            node = self.datasets["cpt"]
            node["properties"]["pointx"] = 0.015
            return {
                "success": False,
                "_outer_isError": True,
                "error": {"code": "ENGINE_CALL_FAILED", "message": "native setter failed"},
                "execution": {"job_id": "job-partial-setter"},
                "data": {
                    "status": "PARTIAL_FAILURE",
                    "execution_state_unknown": True,
                    "applied": [{"step": "property", "property": "pointx"}],
                    "failed": [{"step": "property", "property": "method",
                                 "error": {"code": "ENGINE_CALL_FAILED"}}],
                    "not_executed": [{"step": "property", "property": "pointy"}],
                    "readback": {"readable": True, "match": False},
                },
            }
        return await super().action(operation, body, **kwargs)


def test_bound_node_probe_helper_uses_realistic_readback_and_preserves_user_nodes(tmp_path: Path) -> None:
    client = IndependentPublicClient()
    result = asyncio.run(run_node_probe_cases(client, tmp_path / "node-run", execute_java=None))

    assert result["overall"] == "PASS", result
    assert result["summary"]["FAIL"] == 0
    assert result["summary"]["UNVERIFIED"] == 0
    by_case = {row["case"]: row for row in result["rows"]}
    assert by_case["C11"]["status"] == "PASS"
    assert by_case["C14"]["status"] == "PASS"
    assert by_case["C11-CutPlane-3D"]["status"] == "NOT_RUN"
    line_eval = next(row for row in result["rows"]
                     if row["case"].startswith("C11-CutLine2D-evaluate-average-323.15-"))
    assert line_eval["status"] == "PASS"
    assert by_case["C11-property-failure-stop-readback"]["status"] == "PASS"
    assert by_case["C11-property-failure-stop-readback"]["assertions"]["execution_mode"] == "partial_setter"
    assert by_case["C11-property-failure-stop-readback"]["assertions"]["ordered_partial_evidence"] is True
    assert by_case["preserve-user-datasets"]["status"] == "PASS"
    assert by_case["preserve-user-tables"]["status"] == "PASS"
    assert by_case["preserve-user-probes"]["status"] == "PASS"
    assert all(call[2]["reconcile"] is False for call in client.calls)
    probe_creates = [body for operation, body, _ in client.calls if operation == "probe.create"]
    assert probe_creates and all("component" not in body for body in probe_creates)
    assert all(body["definition"].get("component") == "comp1" for body in probe_creates)
    line_create = next(body for operation, body, _ in client.calls
                       if operation == "dataset.create" and body.get("type_id") == "CutLine2D")
    assert line_create["definition"]["genpoints"] == [[0.01, 0.005], [0.04, 0.005]]
    at_points = [body for operation, body, _ in client.calls if operation == "result.at_points"]
    assert at_points
    assert all(isinstance(point, dict) and set(point) == {"x", "y"}
               for body in at_points for point in body.get("points", []))
    assert any(operation == "study.run" for operation, _, _ in client.calls)
    assert len({call[2]["key"] for call in client.calls}) == len(client.calls)
    assert (tmp_path / "node-run" / "g33_node_probe_cases.final.json").is_file()
    response_files = list((tmp_path / "node-run" / "responses").glob("*.json"))
    assert response_files


def test_unbound_node_probe_helper_is_blocked_without_synthetic_pass(tmp_path: Path) -> None:
    result = asyncio.run(run_node_probe_cases(IndependentPublicClient(bound=False), tmp_path / "unbound"))
    assert result["overall"] == "BLOCKED"
    assert result["summary"]["PASS"] == 0
    assert {row["status"] for row in result["rows"]} == {"BLOCKED", "NOT_RUN"}


def test_c14_can_run_in_isolated_scope_without_c11_chain(tmp_path: Path) -> None:
    result = asyncio.run(run_node_probe_cases(IndependentPublicClient(), tmp_path / "c14-only",
                                              include_c11=False))
    rows = {row["case"]: row for row in result["rows"]}
    assert rows["C11"]["status"] == "NOT_RUN"
    assert rows["C14"]["status"] == "PASS"
    assert result["metadata"]["include_c11"] is False


def test_join_at_points_accepts_missing_echo_only_with_prior_binding(tmp_path: Path) -> None:
    """The public route may omit ``data.dataset``; request binding remains mandatory."""
    client = IndependentPublicClient(omit_at_points_dataset=True)
    result = asyncio.run(run_node_probe_cases(client, tmp_path / "join-no-echo"))

    assert result["overall"] == "PASS", result
    join_row = next(row for row in result["rows"]
                    if row["case"].startswith("C11-Join-difference-zero-at-points-"))
    provenance = join_row["assertions"]["dataset_provenance"]
    assert join_row["status"] == "PASS"
    assert provenance["response_dataset_field_present"] is False
    assert provenance["binding_precheck"] is True
    assert join_row["assertions"]["dataset_provenance_ok"] is True


def test_c14_cleanup_accepts_native_probe_table_cascade_and_preserves_sentinel(tmp_path: Path) -> None:
    client = IndependentPublicClient(cascade_probe_table=True)
    result = asyncio.run(run_node_probe_cases(client, tmp_path / "c14-cascade", include_c11=False))

    assert result["overall"] == "PASS", result
    rows = {row["case"]: row for row in result["rows"]}
    history_cleanup = next(row for case, row in rows.items() if case.startswith("table-cleanup-g33hist"))
    assert history_cleanup["status"] == "PASS"
    assert history_cleanup["assertions"]["native_cascade_after_probe_remove"] is True
    assert rows["preserve-user-tables"]["status"] == "PASS"
    assert "g33hist" not in client.tables


def test_c11_cleanup_accepts_native_dataset_cascade(tmp_path: Path) -> None:
    client = IndependentPublicClient(cascade_dataset=True)
    result = asyncio.run(run_node_probe_cases(client, tmp_path / "dataset-cascade"))

    assert result["overall"] == "PASS", result
    cascade_rows = [row for row in result["rows"]
                    if row["case"].startswith("dataset-cleanup-g33cyca")]
    assert cascade_rows
    assert any(row["status"] == "PASS"
               and row["assertions"].get("native_cascade_after_dataset_remove") is True
               for row in cascade_rows)
    assert not any(row["status"] == "FAIL" for row in cascade_rows)
    assert not any(tag.startswith("g33cyc") for tag in client.datasets)


def test_cutpoint_property_readback_accepts_native_numeric_strings() -> None:
    assert _close_value("0.015", 0.015)
    assert _close_value("0.005", 0.005)
    assert not _close_value("not-a-number", 0.005)


def test_solved_cutplane_helper_requires_binding_and_native_area_readback(tmp_path: Path) -> None:
    client = IndependentPublicClient()
    client.cutplane_fixture = {"fixture_ready": True, "dataset": "dset1",
                               "component": "comp1", "geometry": "geom1"}
    result = asyncio.run(run_cutplane_cases(client, tmp_path / "cutplane-run"))
    assert result["overall"] == "PASS", result
    rows = {row["case"]: row for row in result["rows"]}
    assert rows["C11-CutPlane-3D"]["status"] == "PASS"
    assert rows["C11-CutPlane-3D-solution-indices"]["status"] == "PASS"
    assert rows["C11-CutPlane-3D-area-evaluate"]["status"] == "PASS"


def test_unknown_observes_original_job_before_allowing_next_call(tmp_path: Path) -> None:
    client = UnknownClient(terminal=True)
    ledger = _NodeLedger(client, tmp_path / "unknown-terminal")
    first = asyncio.run(ledger.call("dataset.create", {}, "unknown-first"))
    assert first[2] == "unknown"
    assert client.host.calls == ["job_status", "job_result", "job_reconcile", "model_inspect"]
    assert ledger.halted is False
    second = asyncio.run(ledger.call("dataset.list", {}, "after-quiescent"))
    assert second[2] == "response"
    assert client.action_calls == 2


def test_unknown_without_quiescent_readback_halts_mutation_and_is_recorded(tmp_path: Path) -> None:
    client = UnknownClient(terminal=False)
    ledger = _NodeLedger(client, tmp_path / "unknown-open")
    first = asyncio.run(ledger.call("dataset.create", {}, "unknown-first"))
    assert first[2] == "unknown"
    assert ledger.halted is True
    second = asyncio.run(ledger.call("dataset.create", {}, "must-stop"))
    assert second[2] == "halted"
    assert client.action_calls == 1
    assert ledger.calls[-1]["mode"] == "halted"


def test_partial_setter_unknown_passes_only_after_reconciliation(tmp_path: Path) -> None:
    client = UnknownPartialSetterClient()
    ledger = _NodeLedger(client, tmp_path / "unknown-partial-setter")
    passed = asyncio.run(_cutpoint_mid_set_failure(ledger, "cpt"))

    assert passed is True
    row = next(row for row in ledger.rows if row["case"] == "C11-property-failure-stop-readback")
    assert row["status"] == "PASS"
    assert row["assertions"]["initial_execution_state_unknown"] is True
    assert row["assertions"]["unknown_reconciled"] is True
    assert ledger.unknown_observations[0]["quiescent"] is True
    assert ledger.unknown_observations[0]["model_reconciled"] is True


def test_recorded_probe_axis_must_cover_the_stored_solution_span() -> None:
    """The recorded history is at solver steps, so require span coverage.

    Verified live: the Chain-B fixture stores five output times
    (``getPVals() == [0.0, 0.5, 1.0, 1.5, 2.0]``) while the linked probe table
    records 23 rows at the solver's own steps (0 -> 2.0952).  Equality would be
    the wrong rule; covering the stored span is the observation-based one.
    """
    stored = [0.0, 0.5, 1.0, 1.5, 2.0]
    recorded = [0.0, 0.00137, 0.52, 0.6952, 0.8952, 2.09521]
    ok, evidence = _axis_covers_stored_times(recorded, stored)
    assert ok is True, evidence
    assert evidence["covers_stored_span"] is True
    assert evidence["recorded_steps"] == 6 and evidence["stored_steps"] == 5

    for bad in ([0.1, 0.6, 1.1, 2.1],            # shifted axis
                [0.0, 0.5, 1.0],                  # truncated axis
                [0.0, 0.5],                       # fewer than three samples
                []):
        refused, refusal = _axis_covers_stored_times(bad, stored)
        assert refused is False, (bad, refusal)
        assert "reason" in refusal

    # A solution that publishes fewer than three stored steps cannot prove a span.
    refused, refusal = _axis_covers_stored_times(recorded, [0.0, 1.0])
    assert refused is False and "fewer than three stored time steps" in refusal["reason"]


def test_history_axis_reads_the_localized_labelled_column_of_complex_rows() -> None:
    """The live table publishes complex wrappers and a localized time label."""
    history = {
        "headers": ["时间 (s)", "2 (1)"],
        "rows": [[{"real": value, "imag": 0.0}, {"real": 2.0, "imag": 0.0}]
                 for value in (0.0, 0.001372516503164569, 0.52, 1.0952, 2.0952)],
        "row_headers": [],
    }
    times, evidence = _history_time_axis(history, history["rows"], [0.0, 0.5, 1.0, 1.5, 2.0])
    assert times == [0.0, 0.001372516503164569, 0.52, 1.0952, 2.0952]
    assert evidence["source"] == "column" and evidence["column_index"] == 0
    assert evidence["header"] == "时间 (s)"
    assert evidence["stored_output_times"] == [0.0, 0.5, 1.0, 1.5, 2.0]
    covers, cover_evidence = _axis_covers_stored_times(times, [0.0, 0.5, 1.0, 1.5, 2.0])
    assert covers is True and cover_evidence["covers_stored_span"] is True

    # A genuinely complex column is not a real time axis.
    complex_row = {"headers": ["时间 (s)", "z (1)"],
                   "rows": [[{"real": value, "imag": 1.0}, {"real": 1.0, "imag": 1.0}]
                            for value in (0.0, 0.5, 1.0)]}
    refused, refusal = _history_time_axis(complex_row, complex_row["rows"], [0.0, 0.5, 1.0])
    assert refused == [] and refusal["source"] == "none"
