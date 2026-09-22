"""Offline contract tests for the independent Chain-B transient probe helper."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from tools.g33_node_probe_cases import run_transient_probe_case


def _path(collection: str, tag: str) -> dict[str, Any]:
    return {"segments": [{"accessor": "result"}, {"collection": collection, "tag": tag}]}


class TransientPublicClient:
    """Native-like Chain-B public client with independently generated history rows."""

    def __init__(self, *, short_axis: bool = False, cascade_table: bool = False,
                 shifted_axis: bool = False) -> None:
        self.state = {"ref": {"model": "chain_b"}}
        self.short_axis = short_axis
        self.cascade_table = cascade_table
        self.shifted_axis = shifted_axis
        self.solved = False
        self.calls: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
        self.datasets = {"dset1": {"type_id": "Solution", "properties": {"solution": "sol1"}}}
        self.tables: dict[str, dict[str, Any]] = {
            "user_table": {"headers": ["user"], "data": [[7.0]], "imaginary": None},
        }
        self.probes: dict[tuple[str | None, str], dict[str, Any]] = {
            ("comp1", "user_probe"): {
                "type_id": "Domain", "properties": {"expr": "T", "unit": "K"},
            },
        }

    @staticmethod
    def _ok(data: dict[str, Any]) -> dict[str, Any]:
        return {"success": True, "data": data}

    @staticmethod
    def _err(code: str) -> dict[str, Any]:
        return {"success": False, "error": {"code": code, "message": code}}

    def _dataset_list(self) -> dict[str, Any]:
        return self._ok({"datasets": [{"tag": "dset1", "type_id": "Solution",
                                       "solution": "sol1", "path": _path("dataset", "dset1")}],
                         "count": 1, "tags": ["dset1"]})

    def _table_list(self) -> dict[str, Any]:
        items = [{"tag": tag, "path": _path("table", tag), "headers": node["headers"],
                  "readback": {"readable": True, "data": node["data"],
                               "imaginary": node["imaginary"]}}
                 for tag, node in self.tables.items()]
        return self._ok({"tables": items, "count": len(items), "tags": list(self.tables),
                         "readback": {"readable": True}})

    def _probe_list(self) -> dict[str, Any]:
        items = []
        for (component, tag), node in self.probes.items():
            props = node["properties"]
            items.append({"tag": tag, "component": component, "type_id": node["type_id"],
                          "native_type": node["type_id"], "expression": props.get("expr"),
                          "table": props.get("table"),
                          "path": {"segments": ([{"collection": "component", "tag": component}]
                                                   if component is not None else [])
                                   + [{"collection": "probe", "tag": tag}]}})
        return self._ok({"probes": items, "count": len(items),
                         "tags": [item["tag"] for item in items],
                         "readback": {"readable": True}})

    async def action(self, operation: str, body: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        self.calls.append((operation, body, kwargs))
        assert kwargs.get("reconcile") is False
        if operation == "dataset.list":
            return self._dataset_list()
        if operation == "result.numerical_manage":
            return self._ok({"features": [], "count": 0, "tags": [],
                             "readback": {"readable": True}})
        if operation == "result.table_manage":
            action = body.get("action")
            if action == "list":
                return self._table_list()
            tag = body.get("path", {}).get("segments", [{}, {}])[-1].get("tag")
            if action == "create":
                definition = body.get("definition", {})
                self.tables[tag] = {"headers": list(definition.get("headers", [])),
                                    "data": list(definition.get("data", [])),
                                    "imaginary": definition.get("imaginary")}
                return self._ok({"tag": tag, "type_id": "Table", "status": "APPLIED",
                                 "created": True, "readback": {"readable": True, "match": True,
                                    "type_id": "Table"}})
            if action == "remove":
                if tag not in self.tables:
                    return self._err("NODE_NOT_FOUND")
                del self.tables[tag]
                return self._ok({"tag": tag, "removed": True, "verified_removed": True,
                                 "status": "APPLIED", "readback": {"readable": True, "match": True}})
        if operation == "probe.list":
            return self._probe_list()
        if operation == "probe.create":
            tag = body["tag"]
            definition = body.get("definition", {})
            props = dict(definition.get("properties", {}))
            component = body.get("component")
            self.probes[(component, tag)] = {"type_id": "GlobalVariable", "properties": props}
            return self._ok({"tag": tag, "type_id": "GlobalProbe", "created": True,
                             "component": component,
                             "status": "APPLIED",
                             "readback": {"readable": True, "match": True,
                                           "type_id": "GlobalProbe"}})
        if operation == "probe.update":
            tag = body["path"]["segments"][-1]["tag"]
            component = body["path"]["segments"][0].get("tag")
            node = self.probes[(component, tag)]
            definition = body.get("definition", {})
            node["properties"].update(definition.get("properties", {}))
            return self._ok({"tag": tag, "component": component, "type_id": "GlobalVariable", "updated": True,
                             "status": "APPLIED",
                             "applied": [{"step": "property", "property": "expr"},
                                         {"step": "genResult", "solver": "sol1"}],
                             "readback": {"readable": True, "match": True,
                                           "type_id": "GlobalVariable"}})
        if operation == "probe.history":
            tag = body["path"]["segments"][-1]["tag"]
            component = body["path"]["segments"][0].get("tag")
            node = self.probes.get((component, tag))
            if node is None:
                return self._err("NODE_NOT_FOUND")
            table_tag = node["properties"].get("table")
            table = self.tables.get(table_tag)
            if table is None:
                return self._err("NO_HISTORY")
            if not self.solved:
                rows: list[list[float]] = []
                times: list[float] = []
            elif self.short_axis:
                rows = [[0.0, 2.0], [0.5, 2.0]]
                times = [0.0, 0.5]
            else:
                rows = [[0.0, 2.0], [0.5, 2.0], [1.0, 2.0], [1.5, 2.0], [2.0, 2.0]]
                times = [0.0, 0.5, 1.0, 1.5, 2.0]
                if self.shifted_axis:
                    # A fabricated axis that no longer equals the solved
                    # solution's time steps must not be accepted.
                    rows = [[value + 0.1, row[1]] for value, row in zip(times, rows)]
                    times = [value + 0.1 for value in times]
            return self._ok({"tag": tag, "table": table_tag, "headers": ["Time (s)", "probe"],
                             "rows": rows, "row_headers": [], "time": times,
                             "time_metadata": {"status": "VERIFIED", "source": "native_table_column",
                                               "header": "Time (s)", "column_index": 0, "unit": "s",
                                               "values": list(times)},
                             "unit": "1", "parameters": None,
                             "source": {"probe_table_property": table_tag,
                                        "real_table_readback": True, "gen_result_called": False},
                             "readback": {"real_readable": True}})
        if operation == "dataset.solution_indices":
            # The bound solution's own time steps: the helper must prove the
            # generated table's axis against these, not against a header label.
            times = [0.0, 0.5] if self.short_axis else [0.0, 0.5, 1.0, 1.5, 2.0]
            return self._ok({"dataset": "dset1", "solution": "sol1", "binding_complete": True,
                             "time_values": times, "inner_indices": list(range(1, len(times) + 1)),
                             "outer_indices": [1], "parameters": {},
                             "dataset_binding": {"solution": "sol1", "binding_complete": True}})
        if operation == "study.run":
            self.solved = True
            return self._ok({"status": "SUCCEEDED", "solved": True})
        if operation == "probe.remove":
            tag = body["path"]["segments"][-1]["tag"]
            component = body["path"]["segments"][0].get("tag")
            node = self.probes.pop((component, tag), None)
            if node is None:
                return self._err("NODE_NOT_FOUND")
            if self.cascade_table:
                table_tag = node["properties"].get("table")
                if isinstance(table_tag, str):
                    self.tables.pop(table_tag, None)
            return self._ok({"tag": tag, "removed": True, "verified_removed": True,
                             "status": "APPLIED", "readback": {"readable": True, "match": True}})
        raise AssertionError(operation)


def test_transient_probe_requires_real_time_axis_and_constant_rows(tmp_path: Path) -> None:
    client = TransientPublicClient(cascade_table=True)
    result = asyncio.run(run_transient_probe_case(client, tmp_path / "transient"))

    assert result["overall"] == "PASS", result
    rows = {row["case"]: row for row in result["rows"]}
    assert rows["C14-transient-history-real-table"]["status"] == "PASS"
    assert rows["C14-transient-history-axis-metadata"]["status"] == "PASS"
    assert rows["C14-transient"]["status"] == "PASS"
    assert rows["C14-transient-history-real-table"]["assertions"]["time_values"] == [0.0, 0.5, 1.0, 1.5, 2.0]
    assert rows["C14-transient-history-real-table"]["assertions"]["row_values_all_two"] is True
    assert rows["preserve-user-probes"]["status"] == "PASS"
    assert rows["preserve-user-tables"]["status"] == "PASS"
    assert "user_table" in client.tables
    assert (tmp_path / "transient" / "g33_node_probe_cases.final.json").is_file()


def test_transient_probe_does_not_pass_short_history_axis(tmp_path: Path) -> None:
    result = asyncio.run(run_transient_probe_case(TransientPublicClient(short_axis=True), tmp_path / "short"))

    rows = {row["case"]: row for row in result["rows"]}
    assert result["overall"] == "FAIL", result
    assert rows["C14-transient-history-real-table"]["status"] == "FAIL"
    assert rows["C14-transient-history-axis-metadata"]["status"] == "FAIL"
    assert rows["C14-transient"]["status"] == "FAIL"


def test_transient_probe_rejects_a_table_axis_that_is_not_the_solution_axis(tmp_path: Path) -> None:
    """A labelled time column is not enough: it must equal the solved time steps."""
    result = asyncio.run(run_transient_probe_case(
        TransientPublicClient(cascade_table=True, shifted_axis=True), tmp_path / "shifted"))

    rows = {row["case"]: row for row in result["rows"]}
    assert result["overall"] == "FAIL", result
    assert rows["C14-transient-history-real-table"]["status"] == "FAIL"
    assert rows["C14-transient-history-real-table"]["assertions"]["time_axis"]["source"] == "column"
    assert rows["C14-transient-history-axis-metadata"]["assertions"]["axis_source"] == "column"
    assert rows["C14-transient-history-axis-metadata"]["status"] == "FAIL"
    assert rows["C14-transient-solution-time-steps"]["status"] == "PASS"
