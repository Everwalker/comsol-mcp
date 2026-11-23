"""W18 must retain structured Worker UNKNOWN during dataset binding reads."""
from __future__ import annotations

from pathlib import Path

import pytest

from comsol_mcp import _dataset_binding, _g3_w18
from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._g2_engine import _worker_failure_unknown
from comsol_mcp._g3_ops import DISPATCH
from comsol_mcp._managed_backend import ManagedBackend
from tests.test_g3_w18_plot import MockWorker


@pytest.mark.parametrize("failure_site", [
    "resolver", "model.sol", "solution.get", "solution.isInitialized",
])
def test_plot_render_preserves_dataset_worker_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_site: str,
) -> None:
    worker = MockWorker(tmp_path)
    DISPATCH["plot.group_create"](
        worker, "m1", {"tag": "pg_unknown", "dimension": 2, "dataset": "dset1"}
    )
    DISPATCH["plot.feature_create"](
        worker, "m1", {
            "group": "pg_unknown", "tag": "surf1", "type_id": "Surface",
            "properties": {"expr": "T", "unit": "K"},
        }
    )

    model = worker.model("m1")

    class Solution:
        def isInitialized(self) -> bool:
            return True

    solution = Solution()

    class SolutionContainer:
        def get(self, _tag: str) -> object:
            return solution

    solution_container = SolutionContainer()
    unknown_seen = False
    reads_after_unknown: list[str] = []

    def unknown() -> ExecutionContractError:
        return ExecutionContractError(
            "ENGINE_CALL_FAILED", "synthetic unresolved Worker read",
            stage="post_dispatch", details={"execution_state_unknown": True},
        )

    def mark_unknown() -> None:
        nonlocal unknown_seen
        unknown_seen = True

    def resolve(*_args: object, **_kwargs: object) -> dict[str, object]:
        if failure_site == "resolver":
            mark_unknown()
            raise unknown()
        return {
            "binding_complete": True, "solution": "sol1", "dataset_chain": ["dset1"],
        }

    monkeypatch.setattr(_dataset_binding, "resolve_dataset_binding", resolve)
    original_call = _g3_w18._call

    def tracked_call(node: object, method: str, *args: object) -> object:
        if unknown_seen:
            reads_after_unknown.append(method)
        if node is model and method == "sol":
            if failure_site == "model.sol":
                mark_unknown()
                raise unknown()
            return solution_container
        if node is solution_container and method == "get":
            if failure_site == "solution.get":
                mark_unknown()
                raise unknown()
            return solution
        if node is solution and method == "isInitialized":
            if failure_site == "solution.isInitialized":
                mark_unknown()
                raise unknown()
            return True
        return original_call(node, method, *args)

    monkeypatch.setattr(_g3_w18, "_call", tracked_call)
    try:
        ManagedBackend._dispatch_with_witness(
            "plot.render",
            lambda: _g3_w18.plot_render(
                worker, "m1", {"path": "pg_unknown", "options": {"destination": "render.png"}}
            ),
            effect="READ",
        )
    except ExecutionContractError as exc:
        assert _worker_failure_unknown(exc)
    else:
        pytest.fail(f"plot.render returned instead of preserving UNKNOWN; post-UNKNOWN reads: {reads_after_unknown}")

    assert unknown_seen
    assert reads_after_unknown == []


@pytest.mark.parametrize(("failure_site", "expected_state"), [
    ("resolver", "UNKNOWN_SOLUTION_STATE"),
    ("model.sol", "CURRENT_SOLUTION"),
])
def test_plot_render_keeps_deterministic_dataset_fallbacks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_site: str, expected_state: str,
) -> None:
    worker = MockWorker(tmp_path)
    DISPATCH["plot.group_create"](
        worker, "m1", {"tag": "pg_fallback", "dimension": 2, "dataset": "dset1"}
    )
    DISPATCH["plot.feature_create"](
        worker, "m1", {
            "group": "pg_fallback", "tag": "surf1", "type_id": "Surface",
            "properties": {"expr": "T", "unit": "K"},
        }
    )
    model = worker.model("m1")
    solution = type("Solution", (), {"isInitialized": lambda _self: True})()

    class SolutionContainer:
        def get(self, _tag: str) -> object:
            return solution

    solution_container = SolutionContainer()

    def resolve(*_args: object, **_kwargs: object) -> dict[str, object]:
        if failure_site == "resolver":
            raise ExecutionContractError(
                "ENGINE_CALL_FAILED", "determinate resolver read failure", stage="validation",
            )
        return {"binding_complete": True, "solution": "sol1"}

    monkeypatch.setattr(_dataset_binding, "resolve_dataset_binding", resolve)
    original_call = _g3_w18._call

    def deterministic_call(node: object, method: str, *args: object) -> object:
        if failure_site == "model.sol" and node is model and method == "sol":
            raise ExecutionContractError(
                "API_UNSUPPORTED", "determinate solution accessor failure", stage="validation",
            )
        if node is model and method == "sol":
            return solution_container
        if node is solution_container and method == "get":
            return solution
        return original_call(node, method, *args)

    monkeypatch.setattr(_g3_w18, "_call", deterministic_call)
    result = _g3_w18.plot_render(
        worker, "m1", {"path": "pg_fallback", "options": {"destination": "fallback.png"}}
    )

    assert result["solution_state"] == expected_state
