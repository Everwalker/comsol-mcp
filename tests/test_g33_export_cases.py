"""Offline contract test for the public export acceptance driver.

This test does not stand in for a COMSOL run.  It checks the driver's request discipline,
ledger assertions, bounded artifact reconstruction, and truthful distinction between live
cases and the two fault states for which no public injector is available.
"""
from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import os
from pathlib import Path
from typing import Any, Mapping

from tools.g33_export_cases import _plan_budget_probe, run_export_cases


class _FakePublicClient:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.state = {"ref": {"session_id": "fake", "server_instance_id": "fake", "model_tag": "m", "generation": 1}}
        self.calls: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
        self.adopted: list[dict[str, Any]] = []

    def _adopt_readback(self, payload: Mapping[str, Any]) -> None:
        self.adopted.append(dict(payload))

    def _refusal(self, code: str, message: str) -> dict[str, Any]:
        return {
            "success": False,
            "error": {"code": code, "message": message, "stage": "validation"},
            "data": {
                "refused": True,
                "dispatch_stage": "validation",
                "witness": {"mutation_issued": False, "engine_calls": 0, "methods": []},
            },
        }

    def _target(self, destination: str) -> Path:
        raw = Path(destination).expanduser()
        return Path(os.path.abspath(os.fspath(raw if raw.is_absolute() else self.root / raw)))

    def _export(self, body: Mapping[str, Any]) -> dict[str, Any]:
        fmt = str(body.get("format", ""))
        target = self._target(str(body.get("destination", "")))
        try:
            target.relative_to(self.root)
        except ValueError:
            return self._refusal("ACCESS_VIOLATION", "destination escapes project root")
        if fmt not in {"json", "csv"}:
            return self._refusal("API_UNSUPPORTED", "unsupported format")
        if target.is_symlink():
            return self._refusal("ACCESS_VIOLATION", "destination contains a symlink")
        if target.exists() and not body.get("overwrite", False):
            return self._refusal("DESTINATION_EXISTS", "destination already exists")
        expressions = body.get("spec", {}).get("expressions", [])
        if expressions == ["__G33_INVALID_EXPRESSION__"]:
            return {
                "success": False,
                "error": {
                    "code": "EVALUATION_FAILED",
                    "message": "invalid expression",
                    "details": {
                        "dispatch_stage": "post_dispatch",
                        "witness": {
                            "mutation_issued": True,
                            "engine_calls": 3,
                            "methods": ["result.numerical.create", "feature.run", "feature.getData"],
                        },
                    },
                },
            }

        target.parent.mkdir(parents=True, exist_ok=True)
        values = [[{"real": 300.0, "imag": 0.0, "axis": "point", "point": 0, "unit": "K", "coords": [0.0, 0.0]}]]
        metadata = {
            "expressions": ["T"],
            "dataset": "dset1",
            "solution": "sol1",
            "axes": ["expression", "outer", "inner", "point"],
            "units": ["K"],
            "coordinates": [[0.0, 0.0]],
            "field_array": {
                "axes": ["expression", "outer", "inner", "point"],
                "units": ["K"],
                "coordinates": [[0.0, 0.0]],
            },
        }
        if fmt == "json":
            document = {"spec": body.get("spec", {}), "values": values, "metadata": metadata}
            target.write_text(json.dumps(document, sort_keys=True) + "\n", encoding="utf-8")
        else:
            output = io.StringIO(newline="")
            writer = csv.writer(output)
            writer.writerow(["axis", "expr", "outer", "inner", "point", "real", "imag", "unit", "coord_0", "coord_1", "coord_2"])
            writer.writerow(["point", "T", 0, 0, 0, 300.0, 0.0, "K", 0.0, 0.0, ""])
            target.write_text(output.getvalue(), encoding="utf-8")
        size = target.stat().st_size
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        return {
            "success": True,
            "data": {
                "file_path": str(target),
                "sha256": digest,
                "format": fmt,
                "byte_size": size,
                "total_elements": 1,
                "chunk_info": {"chunk_size": 65536, "total_chunks": 1, "streaming": True},
            },
        }

    def _read(self, body: Mapping[str, Any]) -> dict[str, Any]:
        target = self._target(str(body.get("artifact_id", "")))
        try:
            target.relative_to(self.root)
        except ValueError:
            return self._refusal("ACCESS_VIOLATION", "artifact is outside project root")
        if not target.is_file() or target.is_symlink():
            return {"success": False, "error": {"code": "ARTIFACT_NOT_FOUND", "message": "missing artifact"}}
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        expected = body.get("expected_sha256")
        if expected is not None and str(expected).lower() != actual:
            return {"success": False, "error": {"code": "ARTIFACT_HASH_MISMATCH", "message": "hash changed"}}
        offset = body.get("offset", 0)
        length = body.get("length", 65536)
        raw = target.read_bytes()
        if offset < 0 or offset >= len(raw) or length <= 0:
            return {"success": False, "error": {"code": "INVALID_CHUNK_RANGE", "message": "range"}}
        block = raw[offset:offset + length]
        return {
            "success": True,
            "data": {
                "file_path": str(target),
                "file_size": len(raw),
                "whole_file_sha256": actual,
                "offset": offset,
                "length": len(block),
                "eof": offset + len(block) >= len(raw),
                "chunk_sha256": hashlib.sha256(block).hexdigest(),
                "data_base64": base64.b64encode(block).decode("ascii"),
            },
        }

    async def action(self, operation: str, body: Mapping[str, Any], **kwargs: Any) -> dict[str, Any]:
        self.calls.append((operation, dict(body), dict(kwargs)))
        if operation == "result.field_export":
            return self._export(body)
        if operation == "artifact.read":
            return self._read(body)
        raise AssertionError(operation)


class _UnknownExportClient(_FakePublicClient):
    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self._first_export = True

    async def action(self, operation: str, body: Mapping[str, Any], **kwargs: Any) -> dict[str, Any]:
        self.calls.append((operation, dict(body), dict(kwargs)))
        if operation == "result.field_export" and self._first_export:
            self._first_export = False
            return {
                "success": False,
                "error": {
                    "code": "EXECUTION_STATE_UNKNOWN",
                    "message": "field export could not establish a terminal result",
                    "details": {"cause_code": "FIELD_ARRAY_SHAPE_MISMATCH"},
                },
                "data": {"execution_state_unknown": True},
                "execution": {"job_id": "job-shape-1"},
            }
        if operation == "job_status":
            return {"success": True, "data": {"job_id": "job-shape-1", "status": "UNKNOWN"}}
        if operation == "job_log":
            return {"success": True, "data": {"job_id": "job-shape-1", "status": "UNKNOWN",
                                                "events": []}}
        if operation == "job_result":
            return {"success": True, "data": {"job_id": "job-shape-1", "status": "UNKNOWN", "result": {}}}
        if operation == "job_reconcile":
            return {"success": True, "data": {"job_id": "job-shape-1", "status": "UNKNOWN",
                                                "metadata": {"replay_performed": False,
                                                             "reconciled_quiescent": False}}}
        raise AssertionError(operation)


class _UnknownCsvClient(_FakePublicClient):
    """Expose UNKNOWN on a later mutation to verify the central stop gate."""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self._csv_seen = False

    async def action(self, operation: str, body: Mapping[str, Any], **kwargs: Any) -> dict[str, Any]:
        if operation == "result.field_export" and body.get("format") == "csv" and not self._csv_seen:
            self.calls.append((operation, dict(body), dict(kwargs)))
            self._csv_seen = True
            return {
                "success": False,
                "error": {"code": "EXECUTION_STATE_UNKNOWN", "message": "CSV export is unresolved"},
                "data": {"execution_state_unknown": True},
                "execution": {"job_id": "job-csv-1"},
            }
        if operation in {"job_status", "job_log", "job_result", "job_reconcile"}:
            self.calls.append((operation, dict(body), dict(kwargs)))
            if operation == "job_reconcile":
                return {"success": True, "data": {"job_id": "job-csv-1", "status": "RUNNING",
                                                    "metadata": {"reconciled_quiescent": False}}}
            return {"success": True, "data": {"job_id": "job-csv-1", "status": "RUNNING"}}
        return await super().action(operation, body, **kwargs)


class _UnknownQuiescentClient(_FakePublicClient):
    """Return a quiescent UNKNOWN envelope and require a model refresh before return."""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self._first_export = True

    async def action(self, operation: str, body: Mapping[str, Any], **kwargs: Any) -> dict[str, Any]:
        self.calls.append((operation, dict(body), dict(kwargs)))
        if operation == "result.field_export" and self._first_export:
            self._first_export = False
            return {
                "success": False,
                "error": {"code": "EXECUTION_STATE_UNKNOWN", "message": "quiescent unknown"},
                "data": {"execution_state_unknown": True},
                "execution": {"job_id": "job-quiescent-1"},
            }
        if operation in {"job_status", "job_log", "job_result", "job_reconcile"}:
            data: dict[str, Any] = {"job_id": "job-quiescent-1", "status": "UNKNOWN"}
            if operation == "job_log":
                data["events"] = []
            if operation == "job_reconcile":
                data["metadata"] = {"reconciled_quiescent": True}
            return {"success": True, "data": data}
        if operation == "model_inspect":
            return {"success": True, "data": {},
                    "execution": {"revision": 7, "dirty": False,
                                   "model_ref": self.state["ref"]}}
        raise AssertionError(operation)


class _PreflightFailureClient(_FakePublicClient):
    """Return a revision preflight refusal for the invalid-expression request."""

    def _export(self, body: Mapping[str, Any]) -> dict[str, Any]:
        expressions = body.get("spec", {}).get("expressions", [])
        if expressions == ["__G33_INVALID_EXPRESSION__"]:
            return self._refusal("REVISION_CONFLICT", "managed model revision is stale")
        return super()._export(body)


class _BudgetRefusalClient(_FakePublicClient):
    """Exercise the bounded C13 branch with a synthetic public response envelope."""

    def _export(self, body: Mapping[str, Any]) -> dict[str, Any]:
        expressions = body.get("spec", {}).get("expressions", [])
        if len(expressions) > 1 and "budget-refusal" in str(body.get("destination", "")):
            return {
                "success": False,
                "error": {
                    "code": "EXPORT_FAILED",
                    "message": "result budget refused before raw data getter",
                    "details": {
                        "reason_code": "ELEMENT_BUDGET_EXCEEDED",
                        "dispatch_stage": "post_dispatch",
                        "witness": {
                            "mutation_issued": True,
                            "engine_calls": 4,
                            "methods": [
                                "result.numerical.create",
                                "feature.run",
                                "feature.getCoordinatesShape",
                            ],
                        },
                    },
                },
            }
        response = super()._export(body)
        if body.get("format") == "json" and response.get("success") is True:
            target = Path(response["data"]["file_path"])
            document = json.loads(target.read_text(encoding="utf-8"))
            document["metadata"]["result_budget"] = {
                "limits": {
                    "max_elements": 1,
                    "max_numeric_payload_bytes": 64 * 1024 * 1024,
                },
                "records": [{
                    "computed": {
                        "raw_point_upper_bound": 1,
                        "raw_upper_bound_source": "native NumericalFeature.getCoordinatesShape()",
                        "outer_count": 1,
                        "inner_count_total": 1,
                        "complex_components": 1,
                        "scalar_bytes": 8,
                    },
                }],
            }
            target.write_text(json.dumps(document, sort_keys=True) + "\n", encoding="utf-8")
            raw = target.read_bytes()
            response["data"]["byte_size"] = len(raw)
            response["data"]["sha256"] = hashlib.sha256(raw).hexdigest()
        return response


class _UnknownEvaluationFailureClient(_FakePublicClient):
    """Propagate a dispatched evaluation failure through a reconciled UNKNOWN envelope."""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self._failure_seen = False

    async def action(self, operation: str, body: Mapping[str, Any], **kwargs: Any) -> dict[str, Any]:
        self.calls.append((operation, dict(body), dict(kwargs)))
        if operation == "result.field_export" and body.get("spec", {}).get("expressions") == ["__G33_INVALID_EXPRESSION__"]:
            self._failure_seen = True
            return {
                "success": False,
                "error": {
                    "code": "EXECUTION_STATE_UNKNOWN",
                    "message": "legacy evaluation callback failed after dispatch",
                    "details": {
                        "cause_code": "ENGINE_CALL_FAILED",
                        "dispatch_stage": "post_dispatch",
                        "witness": {"mutation_issued": True, "engine_calls": 3,
                                    "methods": ["result.numerical.create", "feature.run", "feature.getData"]},
                    },
                },
                "data": {"execution_state_unknown": True},
                "execution": {"job_id": "job-evaluation-failure-1"},
            }
        if operation in {"job_status", "job_log", "job_result", "job_reconcile"}:
            data: dict[str, Any] = {"job_id": "job-evaluation-failure-1", "status": "UNKNOWN"}
            if operation == "job_log":
                data["events"] = []
            if operation == "job_reconcile":
                data["metadata"] = {"reconciled_quiescent": True}
            return {"success": True, "data": data}
        if operation == "model_inspect":
            return {"success": True, "data": {},
                    "execution": {"revision": 9, "dirty": False,
                                   "model_ref": self.state["ref"]}}
        return await super().action(operation, body, **kwargs)


def test_public_export_driver_contract_and_ledger(tmp_path: Path) -> None:
    root = tmp_path.resolve() / "project"
    run_dir = tmp_path.resolve() / "run"
    client = _FakePublicClient(root)
    result = __import__("asyncio").run(run_export_cases(client, run_dir))

    by_case = {row["case"]: row for row in result["rows"]}
    for case in (
        "json-success", "csv-success", "overwrite-default-refused", "overwrite-explicit",
        "path-escape-before-evaluate", "unknown-format-before-evaluate", "symlink-before-evaluate",
        "evaluation-failure-no-publish", "artifact-read-reconstruct", "artifact-read-range-refusal",
        "artifact-read-tamper-detection", "artifact-read-access-refusal",
    ):
        assert by_case[case]["status"] == "PASS", by_case[case]
    assert by_case["unknown-outcome-no-publish"]["status"] == "NOT_RUN"
    assert by_case["cleanup-failure-no-publish"]["status"] == "NOT_RUN"
    chunk_memory = by_case["artifact-read-reconstruct"]["assertions"]["chunk_memory"]
    assert chunk_memory["scope"] == "driver_artifact_read_chunk_buffers"
    assert chunk_memory["sampled_peak"] is True
    assert chunk_memory["absolute_peak"] is False
    assert chunk_memory["engine_internal_cache"] == "UNMEASURED"
    assert result["overall"] == "PASS", result
    assert all(call[2].get("reconcile") is False for call in client.calls)
    keys = [call[2]["key"] for call in client.calls]
    assert len(keys) == len(set(keys))
    assert (run_dir / "g33_export_cases.json").is_file()
    assert (run_dir / "g33_export_cases.final.json").is_file()


def test_unknown_export_queries_job_before_return_and_fails_shape_defect(tmp_path: Path) -> None:
    root = tmp_path.resolve() / "project"
    run_dir = tmp_path.resolve() / "run-unknown"
    client = _UnknownExportClient(root)
    result = __import__("asyncio").run(run_export_cases(client, run_dir))

    by_case = {row["case"]: row for row in result["rows"]}
    assert by_case["json-success"]["status"] == "FAIL", by_case["json-success"]
    assert by_case["json-success"]["assertions"]["product_failure_code"] == "FIELD_ARRAY_SHAPE_MISMATCH"
    assert by_case["unknown-outcome-job-queries"]["status"] == "PASS"
    assert by_case["unknown-outcome-no-publish"]["status"] == "NOT_RUN"
    assert result["overall"] == "FAIL", result
    assert [call[0] for call in client.calls] == [
        "result.field_export", "job_status", "job_log", "job_result", "job_reconcile",
    ]
    keys = [call[2]["key"] for call in client.calls]
    assert len(keys) == len(set(keys))
    assert all(call[2].get("reconcile") is False for call in client.calls)
    assert (run_dir / "g33_export_cases.final.json").is_file()


def test_unknown_on_later_export_halts_all_following_mutations(tmp_path: Path) -> None:
    root = tmp_path.resolve() / "project"
    run_dir = tmp_path.resolve() / "run-later-unknown"
    client = _UnknownCsvClient(root)
    result = __import__("asyncio").run(run_export_cases(client, run_dir))

    by_case = {row["case"]: row for row in result["rows"]}
    assert result["overall"] == "BLOCKED", result
    assert by_case["csv-success"]["status"] == "BLOCKED"
    assert by_case["unknown-outcome-job-queries"]["status"] == "PASS"
    assert by_case["unknown-quiescence-gate"]["status"] == "BLOCKED"
    operations = [call[0] for call in client.calls]
    assert operations[:2] == ["result.field_export", "result.field_export"]
    assert operations[2:] == ["job_status", "job_log", "job_result", "job_reconcile"]


def test_quiescent_unknown_refreshes_and_adopts_managed_revision(tmp_path: Path) -> None:
    root = tmp_path.resolve() / "project"
    run_dir = tmp_path.resolve() / "run-quiescent-unknown"
    client = _UnknownQuiescentClient(root)
    result = __import__("asyncio").run(run_export_cases(client, run_dir))

    by_case = {row["case"]: row for row in result["rows"]}
    assert result["overall"] == "BLOCKED", result
    assert by_case["unknown-model-reconcile"]["status"] == "PASS", by_case
    assert "unknown-quiescence-gate" not in by_case
    operations = [call[0] for call in client.calls]
    assert operations == [
        "result.field_export", "job_status", "job_log", "job_result", "job_reconcile", "model_inspect",
    ]
    refresh_call = client.calls[-1]
    assert refresh_call[1] == {"refresh": True}
    assert refresh_call[2]["reconcile"] is False
    assert client.adopted and client.adopted[-1]["execution"]["revision"] == 7


def test_preflight_refusal_is_not_called_an_evaluation_failure(tmp_path: Path) -> None:
    root = tmp_path.resolve() / "project"
    run_dir = tmp_path.resolve() / "run-preflight-failure"
    result = __import__("asyncio").run(run_export_cases(_PreflightFailureClient(root), run_dir))

    row = {item["case"]: item for item in result["rows"]}["evaluation-failure-no-publish"]
    assert row["status"] == "FAIL", row
    assert row["assertions"]["evaluation_failure_code"] is None
    assert row["assertions"]["evaluation_dispatched"] is False


def test_budget_refusal_branch_requires_no_raw_getter_and_no_publish(tmp_path: Path) -> None:
    root = tmp_path.resolve() / "project"
    run_dir = tmp_path.resolve() / "run-budget-refusal"
    result = __import__("asyncio").run(run_export_cases(_BudgetRefusalClient(root), run_dir))

    by_case = {item["case"]: item for item in result["rows"]}
    budget = by_case["result-budget-refusal-no-getdata"]
    assert result["overall"] == "PASS", result
    assert budget["status"] == "PASS", budget
    assert budget["assertions"]["budget_plan"]["expression_count"] == 2
    assert budget["assertions"]["budget_reason_codes"] == ["ELEMENT_BUDGET_EXCEEDED"]
    assert budget["assertions"]["raw_getter_methods"] == []
    assert budget["assertions"]["destination_absent"] is True
    assert budget["assertions"]["artifact_inventory_no_new"] is True
    assert budget["assertions"]["classification"] == "LIVE_NATIVE_RESULT_BUDGET_REFUSAL_NO_GETDATA"
    assert budget["assertions"]["driver_memory_sample"]["scope"] == "driver_process"
    assert budget["assertions"]["driver_memory_sample"]["absolute_peak"] is False
    assert budget["assertions"]["engine_internal_cache"] == "UNMEASURED"


def test_dispatched_evaluation_unknown_can_certify_no_publish_after_reconcile(tmp_path: Path) -> None:
    root = tmp_path.resolve() / "project"
    run_dir = tmp_path.resolve() / "run-unknown-evaluation-failure"
    result = __import__("asyncio").run(run_export_cases(_UnknownEvaluationFailureClient(root), run_dir))

    by_case = {item["case"]: item for item in result["rows"]}
    evaluation = by_case["evaluation-failure-no-publish"]
    unknown = by_case["unknown-outcome-no-publish"]
    assert result["overall"] == "PASS", result
    assert evaluation["status"] == "PASS", evaluation
    assert evaluation["assertions"]["classification"] == "LIVE_NATIVE_EVALUATION_FAILURE_UNKNOWN_PROPAGATED_NO_PUBLISH"
    assert evaluation["assertions"]["unknown_state_preserved"] is True
    assert evaluation["assertions"]["unknown_reconciled_quiescent"] is True
    assert evaluation["assertions"]["evaluation_methods"] == ["feature.run", "feature.getData"]
    assert evaluation["assertions"]["artifact_inventory_no_new"] is True
    assert unknown["status"] == "PASS", unknown
    assert unknown["assertions"]["source_case"] == "evaluation-failure-no-publish"
    assert unknown["assertions"]["timeout_probe_dispatched"] is False


def test_unknown_timeout_probe_is_never_dispatched(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("G33_EXPORT_ENABLE_UNKNOWN_PROBE", "1")
    root = tmp_path.resolve() / "project"
    run_dir = tmp_path.resolve() / "run-no-timeout-probe"
    client = _FakePublicClient(root)
    result = __import__("asyncio").run(run_export_cases(client, run_dir))

    row = {item["case"]: item for item in result["rows"]}["unknown-outcome-no-publish"]
    assert row["status"] == "NOT_RUN", row
    assert not any("unknown-probe" in str(body.get("destination")) for _, body, _ in client.calls)


def _budget_document(*, point_count: int = 2305, max_elements: int = 1_000_000,
                     outer_count: int = 1, inner_count_total: int = 1) -> dict[str, Any]:
    return {
        "metadata": {
            "result_budget": {
                "limits": {
                    "max_elements": max_elements,
                    "max_numeric_payload_bytes": 64 * 1024 * 1024,
                },
                "records": [{
                    "computed": {
                        "raw_point_upper_bound": point_count,
                        "raw_upper_bound_source": "native NumericalFeature.getCoordinatesShape()",
                        "outer_count": outer_count,
                        "inner_count_total": inner_count_total,
                        "complex_components": 1,
                        "scalar_bytes": 8,
                    },
                }],
            },
        },
    }


def test_budget_plan_uses_native_shape_and_stays_bounded() -> None:
    plan = _plan_budget_probe(_budget_document(), seed_expression="T")

    assert plan["expression_count"] == 434
    assert plan["theoretical_elements"] == 1_000_370
    assert plan["theoretical_numeric_payload_bytes"] == 8_002_960
    assert plan["helper_expression_cap"] == 1000
    assert plan["public_expression_cap"] == 32
    assert plan["helper_expression_cap_ok"] is True
    assert plan["public_expression_cap_ok"] is False
    assert plan["ready"] is False
    assert len(plan["expressions"]) == 434
    assert plan["point_count_source"].startswith("native")


def test_budget_plan_does_not_expand_unbounded_expression_request() -> None:
    plan = _plan_budget_probe(_budget_document(point_count=1, max_elements=10_000_000), seed_expression="T")

    assert plan["ready"] is False
    assert plan["expression_count"] > 1000
    assert plan["expressions"] == []
    assert plan["expression_request_bytes"] is None
    assert "helper expression cap" in plan["reason"]


def test_budget_plan_does_not_double_count_outer_axis() -> None:
    plan = _plan_budget_probe(
        _budget_document(point_count=1000, outer_count=2, inner_count_total=6),
        seed_expression="T",
    )

    assert plan["per_expression_elements"] == 6000
    assert plan["expression_count"] == 167
    assert plan["theoretical_elements"] == 1_002_000


def test_budget_plan_rejects_unbounded_point_shape() -> None:
    plan = _plan_budget_probe(_budget_document(point_count=100_001), seed_expression="T")

    assert plan["ready"] is False
    assert plan["point_cap"] == 100_000
    assert plan["expressions"] == []
    assert "point cap" in plan["reason"]
