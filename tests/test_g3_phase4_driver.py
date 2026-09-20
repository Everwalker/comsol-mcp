"""Static and smoke-level contracts for the G3 (Phase 4) production stdio driver.

These tests never import the COMSOL engine and never start or stop a COMSOL Server: they
inspect the driver's published CLI/case inventory and run one bounded offline case against
the real MCP stdio entrypoint to prove the driver produces its evidence tree.
"""
import ast
import functools
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import types

import pytest

ROOT = Path(__file__).resolve().parents[1]
DRIVER = ROOT / "tools" / "phase4_run_mcp.py"
PHASE3 = ROOT / "tools" / "phase3_run_mcp.py"

REQUIRED_CASES = {
    "R01_LIVE", "R03_LIVE", "R04_LIVE", "R_READBACK",
    "W13_T006_variables", "W13_T015_units", "W13_T048_selection_drift", "W13_T016_2D_data",
    "W14_T009_geometry_edit", "W14_T034_local_paths",
    "W15_T007_selections", "W15_T017_material", "W15_T042_license",
    "W16_T018_mesh", "W16_T019_chainA_steady", "W16_T019_chainB_transient",
    "W16_T019_chainC_continue", "W16_T020_solver",
    "GUARD_T010", "GUARD_T035", "GUARD_T038", "GUARD_T005", "GUARD_T033",
}

# Names of the COMSOL runtime lifecycle tools the driver must never call: process management
# belongs to the orchestrator, not to an acceptance driver.
FORBIDDEN_LIFECYCLE_TOKENS = (
    '"comsol_start"', "'comsol_start'", '"comsol_stop"', "'comsol_stop'",
    "comsol_start(", "comsol_stop(", "server.start(", "start_comsol", "kill_comsol",
)


def _module_tree() -> ast.Module:
    assert DRIVER.is_file(), "G3 production stdio driver is missing"
    return ast.parse(DRIVER.read_text(encoding="utf-8"))


def _assignment_value(tree: ast.Module, name: str) -> ast.expr:
    for node in tree.body:
        if isinstance(node, ast.Assign):
            if any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
                return node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
            assert node.value is not None, f"{name} has no value"
            return node.value
    raise AssertionError(f"{name} is not assigned at module level")


def _assigned_sequence(tree: ast.Module, name: str) -> list[str]:
    return list(ast.literal_eval(_assignment_value(tree, name)))


def _assigned_dict_keys(tree: ast.Module, name: str) -> set[str]:
    value = _assignment_value(tree, name)
    assert isinstance(value, ast.Dict), f"{name} is not a dict literal"
    return {str(key.value) for key in value.keys if isinstance(key, ast.Constant)}


def test_phase4_cli_and_case_inventory():
    tree = _module_tree()
    cases = _assigned_sequence(tree, "CASE_ORDER")
    assert REQUIRED_CASES <= set(cases), sorted(REQUIRED_CASES - set(cases))
    assert len(cases) == len(set(cases))
    reply = subprocess.run([sys.executable, str(DRIVER), "--help"], cwd=ROOT,
                           capture_output=True, text=True, timeout=60)
    assert reply.returncode == 0, reply.stderr
    for option in ("--live", "--runtime-root", "--model", "--reopen-check", "--only",
                   "--host", "--port", "--private-home"):
        assert option in reply.stdout, option


def test_phase4_case_inventory_is_fully_implemented():
    tree = _module_tree()
    order = set(_assigned_sequence(tree, "CASE_ORDER"))
    registered = _assigned_dict_keys(tree, "CASES")
    packages = {row[0] for row in ast.literal_eval(_assignment_value(tree, "CASE_PACKAGES"))}
    plan = _assigned_dict_keys(tree, "PLAN")
    assert registered == order, sorted(order ^ registered)
    assert packages == order, sorted(order ^ packages)
    assert plan == order, sorted(order ^ plan)


def test_phase4_driver_never_manages_the_comsol_runtime():
    source = DRIVER.read_text(encoding="utf-8")
    for token in FORBIDDEN_LIFECYCLE_TOKENS:
        assert token not in source, f"the driver must not call {token!r}"
    # subprocess is only used for read-only git context, never to launch COMSOL.
    tree = _module_tree()
    calls = [node for node in ast.walk(tree)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
             and isinstance(node.func.value, ast.Name) and node.func.value.id == "subprocess"]
    for call in calls:
        first = call.args[0] if call.args else None
        if isinstance(first, ast.List) and first.elts:
            assert isinstance(first.elts[0], ast.Constant) and first.elts[0].value == "git", \
                "subprocess may only be used for read-only git context"


def test_phase4_driver_keeps_the_phase3_evidence_layout():
    source = DRIVER.read_text(encoding="utf-8")
    for name in ("environment.json", "requests.json", "results.json", "assertions.json",
                 "transcript.json", "SHA256SUMS", "engine.log", "index.json", "summary.md"):
        assert name in source, f"evidence artifact {name} is not written by the driver"
    for status in ("PASS", "FAIL", "BLOCKED", "NOT_RUN"):
        assert status in source
    for level in ("static", "protocol", "fixture", "live", "numerical", "rollup"):
        assert f'"{level}"' in source, f"evidence level {level} is missing"
    assert "COMSOL_MCP_ISOLATION_RECEIPT" in source, "the isolation receipt must be passed through, not invented"
    assert "COMSOL_SERVER_MCP_HOME" in source and "COMSOL_MCP_TOOL_PROFILE" in source


def test_phase4_driver_does_not_modify_the_frozen_phase3_driver():
    assert PHASE3.is_file()
    status = subprocess.run(["git", "status", "--short", "--", str(PHASE3)], cwd=ROOT,
                            capture_output=True, text=True, timeout=30)
    assert status.returncode == 0, status.stderr
    assert status.stdout.strip() == "", f"tools/phase3_run_mcp.py must stay untouched: {status.stdout}"


DRIVER_MODULE_NAME = "phase4_run_mcp_under_test"


@functools.lru_cache(maxsize=1)
def _driver_module():
    """Load the driver itself: importing it only publishes constants, it starts nothing."""
    pytest.importorskip("mcp", reason="the driver imports the MCP SDK at module level")
    spec = importlib.util.spec_from_file_location(DRIVER_MODULE_NAME, DRIVER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[DRIVER_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


def test_legacy_route_fallback_keeps_the_strict_probe_visible():
    """A published legacy MCP action is a public route, recorded, never a silent substitution."""
    module = _driver_module()
    assert module.LEGACY_ROUTE_FALLBACKS == {"model.create": "model_create", "model.load": "model_load",
                                             "model.save": "save_model"}
    host = types.SimpleNamespace(tools={"model_create": {}, "operation_call": {}})
    strict_row = {"available": False, "implementation_status": "PROPOSED_NOT_IMPLEMENTED",
                  "executable": False, "route": "control plane", "mcp_tool_name": "model_create",
                  "error_code": None}
    rows = {"model.create": dict(strict_row), "node.property_get": {"available": True}}
    module._apply_legacy_fallbacks(host, rows)
    row = rows["model.create"]
    assert row["available"] is True
    assert row["fallback_tool"] == "model_create"
    assert row["strict_available"] is False
    assert row["strict_route"] == "control plane"
    assert row["strict_probe"] == strict_row, "the strict probe must survive verbatim"
    assert row["route"] != strict_row["route"], "the row must say which route carries the call"
    # A strict-row copy is replaced, the caller's original dict is not mutated in place.
    assert rows["node.property_get"] == {"available": True}


def test_legacy_route_fallback_never_invents_availability():
    module = _driver_module()
    unpublished = types.SimpleNamespace(tools={"operation_call": {}})
    blocked = {"model.load": {"available": False, "implementation_status": "PROPOSED_NOT_IMPLEMENTED"}}
    module._apply_legacy_fallbacks(unpublished, blocked)
    assert blocked["model.load"]["available"] is False, "an unpublished fallback tool must not be assumed"
    already_strict = types.SimpleNamespace(tools={"model_load": {}})
    strict = {"model.load": {"available": True, "implementation_status": "SUPPORTED_UNVERIFIED"}}
    module._apply_legacy_fallbacks(already_strict, strict)
    assert strict["model.load"]["available"] is True
    assert "fallback_tool" not in strict["model.load"], "an executable strict operation is left alone"


def test_action_client_routes_and_reshapes_the_legacy_model_route():
    module = _driver_module()
    legacy_only = types.SimpleNamespace(tools={"save_model": {}, "operation_call": {}})
    client = module.ActionClient(legacy_only, types.SimpleNamespace(), {})
    assert client._published_tool("model.save") == ("save_model", False)
    strict = types.SimpleNamespace(tools={"model_save": {}, "save_model": {}, "operation_call": {}})
    assert module.ActionClient(strict, types.SimpleNamespace(), {})._published_tool("model.save") == ("model_save", False)
    # The strict catalog types ``destination`` as a path string; the legacy tool wants ``path``.
    assert module._legacy_arguments("model.save", "save_model",
                                    {"destination": "/tmp/x.mph", "overwrite": True}) == {"path": "/tmp/x.mph"}
    assert module._legacy_arguments("model.save", "save_model",
                                    {"destination": {"path": "/tmp/y.mph"}}) == {"path": "/tmp/y.mph"}
    # A policy token is not a path and must never be forwarded as one.
    assert module._legacy_arguments("model.save", "save_model", {"destination": "local", "overwrite": True}) == \
        {"destination": "local", "overwrite": True}


def test_plan_declares_the_operations_each_static_gate_probes():
    """The static checks that gate live subcases must name their operations in the plan."""
    module = _driver_module()
    declared = {item.name: item.ops for item in module.PLAN["GUARD_T035"]}
    assert declared["static_docs_guard_ops_executable"] == ("docs.index", "docs.search")
    license_ops = {item.name: item.ops for item in module.PLAN["W15_T042_license"]}
    assert license_ops["static_license_ops_availability"] == ("runtime.license_inspect", "runtime.capabilities")


@pytest.mark.integration
def test_phase4_offline_protocol_case_produces_evidence(tmp_path):
    """One bounded offline case against the real MCP stdio entrypoint (no COMSOL Server)."""
    run_dir = tmp_path / "offline"
    reply = subprocess.run(
        [sys.executable, str(DRIVER), "--only", "GUARD_T038", "--run-dir", str(run_dir)],
        cwd=ROOT, capture_output=True, text=True, timeout=900,
    )
    assert reply.returncode in {0, 3, 4}, reply.stdout + reply.stderr
    index = json.loads((run_dir / "index.json").read_text(encoding="utf-8"))
    assert [case["case_id"] for case in index["cases"]] == ["GUARD_T038"]
    case = index["cases"][0]
    assert case["case_id"] == "GUARD_T038"
    assert case["status"] in {"PASS", "BLOCKED", "NOT_RUN"}
    assert case["subcases"], "the protocol guard recorded no subcase"
    for artifact in ("environment.json", "transcript.json", "summary.md", "SHA256SUMS"):
        assert (run_dir / artifact).is_file(), artifact
    for artifact in ("environment.json", "requests.json", "results.json", "assertions.json"):
        assert (run_dir / "cases" / "GUARD_T038" / artifact).is_file(), artifact
    # The evidence tree must not leak the caller's absolute home path.
    for path in run_dir.rglob("*.json"):
        assert str(Path.home()) not in path.read_text(encoding="utf-8"), path
