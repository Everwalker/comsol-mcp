"""Offline contracts for the chain C user-style fixture builder.

``tools/phase4_chain_c_fixture.py`` builds the synthetic ``.mph`` that the G3
chain C case continues from.  Everything checked here runs without a COMSOL
Server and without touching the engine: the CLI surface, the dry-run plan, the
argument validation that protects the two hard-coded chain C tags (``std1`` /
``time``), the *published-operation* inventory the plan is allowed to use, the
source scans that keep the builder out of the COMSOL lifecycle, and two bounded
stdio runs (handshake-only, and ``--live`` against a port that cannot answer,
which must come back BLOCKED with a receipt instead of inventing a model).
"""
from __future__ import annotations

import ast
import functools
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from typing import Any, Mapping

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tools" / "phase4_chain_c_fixture.py"
DRIVER = ROOT / "tools" / "phase4_run_mcp.py"
G3_OPS = ROOT / "comsol_mcp" / "_g3_ops.py"
DERIVED_SOURCE = ROOT / "tools" / "java" / "Phase4ChainCDerivedValue.java"
INSIDE_PKG = ROOT / "comsol_mcp" / "__init__.py"
MODULE_NAME = "phase4_chain_c_fixture_under_test"

#: Published legacy throughput tools the fixture may call by tool name; every
#: other operation must be an id in the G3 dispatch table.
LEGACY_TOOLS = {"model_create", "model_load", "save_model", "model_inspect", "model_tree"}

#: The tags the chain C case hard-codes; the fixture must refuse to build a
#: model with any other tags.
REQUIRED_STUDY_TAG = "std1"
REQUIRED_STEP_TAG = "time"

FORBIDDEN_LIFECYCLE_TOKENS = (
    '"comsol_start"', "'comsol_start'", '"comsol_stop"', "'comsol_stop'",
    "comsol_start(", "comsol_stop(", "server.start(", "start_comsol", "kill_comsol",
    "server_start(", "ModelUtil.connect",
)

BLOCKED_CODES = {"BLOCKED", "ENGINE_UNRESPONSIVE", "CONNECT_REQUIRED", "CAPABILITY_UNAVAILABLE",
                 "RUNTIME_CONFIGURATION_REQUIRED", "PERMISSION_DENIED", "NOT_IMPLEMENTED",
                 "UNSUPPORTED_OPERATION", "CONTROL_STARTUP_ERROR"}


def _source() -> str:
    assert FIXTURE.is_file(), "tools/phase4_chain_c_fixture.py is missing"
    return FIXTURE.read_text(encoding="utf-8")


def _module_tree() -> ast.Module:
    return ast.parse(_source())


def _module_constant(name: str):
    for node in _module_tree().body:
        if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == name for target in node.targets):
            return ast.literal_eval(node.value)
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} is not assigned at module level")


@functools.lru_cache(maxsize=1)
def _fixture_module():
    """Import the builder itself: importing only publishes constants."""
    pytest.importorskip("mcp", reason="the builder imports the phase4 driver, which imports the MCP SDK")
    spec = importlib.util.spec_from_file_location(MODULE_NAME, FIXTURE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


@functools.lru_cache(maxsize=1)
def _published_g3_operations() -> set[str]:
    """Operation ids published by the G3 domain modules, read statically.

    ``comsol_mcp._g3_ops`` builds ``DISPATCH`` from the ``OPERATIONS`` mapping of the
    modules it lists in ``_MODULES``; reading those literals keeps this check offline
    (no engine, no MCP SDK) while still reflecting the published surface.
    """
    ops_source = G3_OPS.read_text(encoding="utf-8")
    modules: tuple[str, ...] = ()
    for node in ast.walk(ast.parse(ops_source)):
        if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "_MODULES" for target in node.targets):
            modules = tuple(ast.literal_eval(node.value))
    assert modules, "the G3 module list was not found"
    published: set[str] = set()
    for module in modules:
        path = ROOT / "comsol_mcp" / f"{module}.py"
        assert path.is_file(), f"G3 domain module {module} is missing"
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
            else:
                continue
            if not any(isinstance(target, ast.Name) and target.id == "OPERATIONS" for target in targets):
                continue
            assert isinstance(node.value, ast.Dict), f"{module}.OPERATIONS must stay a dict literal"
            published |= {str(key.value) for key in node.value.keys if isinstance(key, ast.Constant)}
    return published


@functools.lru_cache(maxsize=1)
def _catalogue_operations() -> set[str]:
    """Operation ids of the design catalogue the strict route is generated from."""
    path = ROOT / "docs" / "comsol_mcp_design_v1" / "02_ACTION_CATALOG.json"
    assert path.is_file(), "the action catalogue is missing"
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("operations") if isinstance(payload, dict) else payload
    return {str(row["operation_id"]) for row in rows if isinstance(row, dict) and row.get("operation_id")}


def _driver_args(run_dir: Path, *extra: str) -> list[str]:
    return [sys.executable, str(FIXTURE), "--python", sys.executable,
            "--run-dir", str(run_dir), "--private-home", str(run_dir / "mcp-home"), *extra]

def _clean_env() -> dict[str, str]:
    """Environment without a COMSOL installation: no engine can be reached."""
    env = {key: value for key, value in os.environ.items()
           if key not in {"COMSOL_ROOT", "COMSOL_JAVA_HOME", "COMSOL_MCP_ISOLATION_RECEIPT"}}
    env["PYTHONPATH"] = str(ROOT)
    return env


def test_chain_c_fixture_cli_surface():
    reply = subprocess.run([sys.executable, str(FIXTURE), "--help"], cwd=ROOT,
                           capture_output=True, text=True, timeout=60)
    assert reply.returncode == 0, reply.stderr
    for option in ("--live", "--dry-run", "--handshake-only", "--run-dir", "--output",
                   "--study-tag", "--step-tag", "--time-points", "--solver-edit",
                   "--allow-no-manual-solver", "--with-derived-value", "--skip-solve",
                   "--host", "--port", "--private-home", "--python"):
        assert option in reply.stdout, option
    # The two tags the chain C case hard-codes are the documented defaults.
    assert REQUIRED_STUDY_TAG in reply.stdout and REQUIRED_STEP_TAG in reply.stdout


def test_chain_c_fixture_refuses_to_drift_from_the_chain_c_tags(capsys):
    module = _fixture_module()
    assert module.REQUIRED_STUDY_TAG == REQUIRED_STUDY_TAG
    assert module.REQUIRED_STEP_TAG == REQUIRED_STEP_TAG
    parser = module.build_parser()

    def _refusal(argv: list[str]) -> str:
        """Parse and validate exactly like ``main`` does, and return what argparse printed."""
        with pytest.raises(SystemExit) as failure:
            module.validate(parser.parse_args(argv), parser)
        assert failure.value.code == 2
        return capsys.readouterr().err

    for argv, expected in (
            (["--live", "--study-tag", "std2"], "--study-tag"),
            (["--live", "--step-tag", "stat"], "--step-tag"),
            (["--live", "--time-points", "0,2,1"], "strictly increasing"),
            (["--live", "--time-points", "0"], "at least two"),
            (["--live", "--mesh-size-level", "0"], "between 1 and 9"),
            (["--live", "--port", "0"], "--port"),
            (["--live", "--component", "comp-1"], "plain COMSOL tag"),
            (["--live", "--material-tag", "mat1", "--second-material-tag", "mat1"], "must differ"),
            (["--live", "--length", "0"], "positive"),
            (["--live", "--handshake-only"], "--handshake-only"),
            ([], "pass --live"),
    ):
        assert expected in _refusal(argv), argv


def test_chain_c_fixture_plan_uses_only_published_operations():
    plan = _module_constant("PLAN_STEPS")
    assert plan, "the build plan cannot be empty"
    published = _published_g3_operations()
    assert {"study.step_create", "study.step_update", "study.solver_generate",
            "physics.feature_create", "material.selection_set"} <= published, sorted(published)
    catalogue = _catalogue_operations()
    assert {"model.save", "node.find", "node.property_get", "model.create"} <= catalogue
    referenced: set[str] = set()
    for row in plan:
        for token in str(row["operation"]).split("+"):
            token = token.strip()
            if not token or token.startswith("trusted-code") or token.startswith("code execution"):
                continue
            if token.endswith(")"):  # e.g. "model_create (legacy published route)"
                token = token.split("(")[0].strip()
            referenced.add(token)
    assert referenced, "the plan referenced no operation at all"
    unknown = referenced - published - catalogue - LEGACY_TOOLS
    assert unknown == set(), f"plan operations that are not published: {sorted(unknown)}"
    assert {"study.step_create", "study.step_update", "study.create", "geometry.build",
            "mesh.build", "study.solver_generate"} <= referenced
    assert "model_create" in referenced, "the legacy route is the only published model creation"
    assert "model.save" in referenced, "the fixture must be saved through the published save operation"


def test_chain_c_fixture_payloads_satisfy_the_registry_contract():
    """Every published call the builder makes must pass the registry's own validation.

    The fixture's literal ``self.action(op, {...})`` payloads are replayed through
    ``comsol_mcp._g2_registry.validate_call`` (catalogue schema + operation-shape checks)
    with a managed envelope.  Arguments the AST cannot resolve (``self.path(...)``,
    ``args.port`` ...) are marked dynamic: a rejection that names such an argument is
    reported as unverifiable offline, anything else is a defect.
    """
    pytest.importorskip("mcp", reason="the registry imports the MCP SDK")
    sys.path.insert(0, str(ROOT))
    from comsol_mcp._g2_contract import ExecutionContractError
    from comsol_mcp._g2_registry import validate_call

    envelope = {"project_id": "phase4-chain-c-fixture", "session_id": "s-primary",
                "model_ref": {"tag": "m1"}, "expected_revision": 0,
                "idempotency_key": "phase4-chain-c-fixture-contract"}
    #: Payloads the builder assembles through its schema-driven helper.
    helper_calls = {
        "geometry.feature_update": {"path": "<nodepath>", "properties": {"size": [1.0]}},
        "material.set_properties": {"path": "<nodepath>", "group": "def", "properties": {"k": "1[W/(m*K)]"}},
        "physics.feature_update": {"path": "<nodepath>", "properties": {"T0": 293.15}},
        "mesh.feature_update": {"path": "<nodepath>", "properties": {"hauto": 4}},
        "study.step_update": {"path": "<nodepath>", "properties": {"tlist": [0.0, 1.0]}},
        "solver.feature_update": {"path": "<nodepath>", "properties": {"rtol": 1e-5}},
        "node.property_schema": {"path": "<nodepath>"},
    }
    node_path = {"segments": [{"collection": "component", "tag": "comp1"}]}

    def dynamic_keys(payload: Mapping[str, Any]) -> set[str]:
        def unknown(value: Any) -> bool:
            if value == "<dynamic>":
                return True
            if isinstance(value, dict):
                return any(unknown(item) for item in value.values())
            if isinstance(value, (list, tuple)):
                return any(unknown(item) for item in value)
            return False

        return {name for name, value in payload.items() if unknown(value)}

    def resolve(payload: Mapping[str, Any]) -> dict[str, Any]:
        def convert(value: Any) -> Any:
            if value == "<nodepath>":
                return dict(node_path)
            if value == "<dynamic>":
                return {}
            if isinstance(value, dict):
                return {key: convert(item) for key, item in value.items()}
            if isinstance(value, list):
                return [convert(item) for item in value]
            return value

        return {name: convert(value) for name, value in payload.items()}

    def check(operation_id: str, payload: Mapping[str, Any], where: str, defects: list[str]) -> None:
        body = resolve(payload)
        body.update(envelope)
        try:
            validate_call(operation_id, body)
        except ExecutionContractError as failure:
            text = str(failure)
            dynamic = dynamic_keys(payload)
            if any(name in text for name in dynamic) or (dynamic and "NodePath" in text):
                return  # an argument this static check cannot resolve
            if failure.code == "UNSUPPORTED_OPERATION" and operation_id == "model.save":
                # Cataloged but carried by the published legacy tool (`save_model`), which the
                # driver's own fallback table selects -- verified below, not assumed.
                return
            defects.append(f"{where}: {operation_id} -> {failure.code}: {text}")
        except Exception as failure:  # noqa: BLE001
            defects.append(f"{where}: {operation_id} -> {type(failure).__name__}: {failure}")

    defects: list[str] = []
    validated: set[str] = set()
    tree = _module_tree()
    sites = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute) or node.func.attr != "action":
            continue
        if not node.args or not isinstance(node.args[0], ast.Constant) or not isinstance(node.args[0].value, str):
            continue  # a discovered tool name, checked by the live session instead
        operation_id = node.args[0].value
        payload: dict[str, Any] = {}
        if len(node.args) > 1 and isinstance(node.args[1], ast.Dict):
            for key, value in zip(node.args[1].keys, node.args[1].values):
                if not isinstance(key, ast.Constant):
                    continue
                if isinstance(value, ast.Constant):
                    payload[str(key.value)] = value.value
                elif isinstance(value, (ast.Dict, ast.List, ast.Tuple)):
                    payload[str(key.value)] = _literal(value)
                else:
                    payload[str(key.value)] = "<dynamic>"
        check(operation_id, payload, f"line {node.lineno}", defects)
        validated.add(operation_id)
        sites += 1
    for operation_id, payload in helper_calls.items():
        check(operation_id, payload, "set_properties", defects)
        validated.add(operation_id)

    assert not defects, defects
    assert len(validated) >= 30, sorted(validated)
    assert sites >= 30, f"only {sites} call sites were inspected"
    assert {"study.step_create", "study.step_update", "study.solver_generate", "solver.feature_update",
            "material.set_properties", "mesh.build", "model.save"} <= validated
    # The legacy route for model.save must be the driver's own published fallback.
    driver = _fixture_module().load_driver()
    assert driver.LEGACY_ROUTE_FALLBACKS.get("model.save") == "save_model"
    assert driver.LEGACY_ROUTE_FALLBACKS.get("model.create") == "model_create"


def _literal(node: ast.AST) -> Any:
    """Literal value of a nested container node, with unresolved leaves marked dynamic."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Dict):
        return {str(key.value) if isinstance(key, ast.Constant) else "<key>": _literal(value)
                for key, value in zip(node.keys, node.values)}
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_literal(item) for item in node.elts]
    return "<dynamic>"


def test_chain_c_fixture_uses_the_feature_collection_for_the_study_step():
    """The continuation target must be addressed as a ``feature`` segment.

    ``study.step_update`` resolves its path with ``_feature_context`` (last segment must be
    collection ``feature``) and ``NodePath.from_wire`` accepts only ``ACCESSOR_METHODS``
    collections -- ``step`` is not one of them.
    """
    pytest.importorskip("mcp", reason="the path contract lives in the MCP-facing layer")
    sys.path.insert(0, str(ROOT))
    from comsol_mcp._g2_contract import ACCESSOR_METHODS

    assert "feature" in ACCESSOR_METHODS
    assert "step" not in ACCESSOR_METHODS, "a 'step' collection would change the verified wire form"
    source = _source()
    assert '("feature", self.args.step_tag)' in source, "the fixture must address std1/time as a feature segment"
    assert '("step", self.args.step_tag)' not in source


def test_chain_c_fixture_tlist_write_works_on_the_repo_fake_tree(tmp_path):
    """Replay the fixture's tlist write through the real W16 operation, offline.

    The fake COMSOL node tree in ``tests/test_g3_w16.py`` is the repository's own engine
    stand-in: running the builder's *exact* wire form through ``study.step_update`` proves the
    continuation target it prepares is writable, without a COMSOL Server.
    """
    pytest.importorskip("mcp", reason="the fake tree is reached through the operation layer")
    sys.path.insert(0, str(ROOT))
    from comsol_mcp._g2_contract import ExecutionContractError

    spec = importlib.util.spec_from_file_location("g3_w16_fake_tree_for_chain_c",
                                                  ROOT / "tests" / "test_g3_w16.py")
    assert spec and spec.loader
    fake = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = fake
    spec.loader.exec_module(fake)

    model = fake.build_model()
    created = fake.call("study.step_create", model, {
        "study": {"segments": [{"collection": "study", "tag": REQUIRED_STUDY_TAG}]},
        "tag": REQUIRED_STEP_TAG, "type_id": "Transient", "properties": {}})
    assert created["type_readback"] == "Transient"

    target = {"segments": [{"collection": "study", "tag": REQUIRED_STUDY_TAG},
                           {"collection": "feature", "tag": REQUIRED_STEP_TAG}]}
    tlist = [0.0, 0.5, 1.0, 2.0, 5.0]
    result = fake.call("study.step_update", model, {"path": target, "properties": {"tlist": tlist}})
    assert result["applied"], result
    assert result["properties"]["tlist"]["data"] == tlist
    assert result["applied"][0]["readback_match"] is True

    # The former continuation payloads, re-pinned to the now-authoritative wire
    # shapes: a 'step' collection still fails path parsing; the catalog's row
    # array (PropertySet) is the accepted form for the same write; an
    # expression value for a float64 property is refused pre-write with the
    # precise code instead of a shape error.
    with pytest.raises(ExecutionContractError) as failure:
        fake.call("study.step_update", model, {
            "path": {"segments": [{"collection": "study", "tag": REQUIRED_STUDY_TAG},
                                  {"collection": "step", "tag": REQUIRED_STEP_TAG}]},
            "properties": {"tlist": tlist}})
    assert failure.value.code == "INVALID_NODE_PATH"

    rows = fake.call("study.step_update", model, {
        "path": target,
        "properties": [{"name": "tlist", "value": {"kind": "float64", "shape": [len(tlist)], "data": tlist}}]})
    assert rows["applied"], rows
    assert rows["properties"]["tlist"]["data"] == tlist
    assert rows["applied"][0]["readback_match"] is True

    with pytest.raises(ExecutionContractError) as failure:
        fake.call("study.step_update", model, {
            "path": target,
            "properties": [{"name": "tlist", "value": {"kind": "expression", "shape": [1], "data": ["1[s]"]}}]})
    assert failure.value.code == "PROPERTY_TYPE_MISMATCH"


def test_chain_c_fixture_is_offline_import_safe(tmp_path, monkeypatch):
    evidence_root = ROOT / "evidence" / "phase4" / "chain_c_fixture"
    before = sorted(path.name for path in evidence_root.iterdir()) if evidence_root.is_dir() else []
    module = _fixture_module()
    after = sorted(path.name for path in evidence_root.iterdir()) if evidence_root.is_dir() else []
    assert before == after, "importing the builder must not create evidence"
    assert module.DRIVER_PATH == DRIVER, "the builder must reuse the phase4 driver, not a copy"
    assert module.ROOT == ROOT
    assert callable(module.main)
    # No COMSOL contact while importing.
    assert module.DERIVED_VALUE_SOURCE.endswith(".java")


def test_chain_c_fixture_dry_run_writes_no_files_and_contacts_no_engine(tmp_path):
    run_dir = tmp_path / "dry"
    reply = subprocess.run(_driver_args(run_dir, "--dry-run"), cwd=ROOT,
                           capture_output=True, text=True, timeout=120, env=_clean_env())
    assert reply.returncode == 0, reply.stdout + reply.stderr
    for fragment in ("no engine is contacted", f"study '{REQUIRED_STUDY_TAG}'",
                     f"step '{REQUIRED_STEP_TAG}'", "Transient", "study.solver_generate",
                     "solver.feature_update", "model_save", "0.0, 0.5, 1.0, 2.0, 5.0"):
        assert fragment in reply.stdout, fragment
    assert not run_dir.exists(), "the dry run must not write anything"
    assert "fraction" not in reply.stdout.lower()


def test_chain_c_fixture_never_manages_the_comsol_runtime():
    source = _source()
    for token in FORBIDDEN_LIFECYCLE_TOKENS:
        assert token not in source, f"the builder must not call {token!r}"
    tree = _module_tree()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            assert name not in {"Popen", "system", "spawn"}, "the builder must not spawn processes"
    # The fixture reuses the driver's session class instead of re-implementing it.
    assert "ProductionHost" in source and "ActionClient" in source
    assert "phase4_run_mcp.py" in source, "the driver must be named explicitly as the reused artifact"


def test_chain_c_fixture_keeps_the_driver_read_only():
    """The builder reuses the driver; it must not edit it.

    ``tools/phase4_run_mcp.py`` is untracked in this repository, so ``git status``
    reports ``??`` for it: that marker is not a modification.  A tracked change
    (`` M``/``M ``/``MM``) or a new write path inside the builder would be.
    """
    status = subprocess.run(["git", "status", "--short", "--", str(DRIVER)], cwd=ROOT,
                            capture_output=True, text=True, timeout=30)
    assert status.returncode == 0, status.stderr
    for line in status.stdout.splitlines():
        marker = line[:2]
        assert marker in {"??", "!!"}, f"tools/phase4_run_mcp.py must stay untouched: {line!r}"
    source = _source()
    for mode in ("w", "a", "x"):
        assert f'DRIVER_PATH.open("{mode}' not in source
    assert "DRIVER_PATH.write_text" not in source
    assert "DRIVER_PATH.unlink" not in source
    # The receipt proves which driver revision built the fixture.
    assert "_hash_sources([DRIVER_PATH" in source, "the receipt must hash the reused driver"


def test_chain_c_fixture_derived_value_artifact_matches_the_plan():
    assert DERIVED_SOURCE.is_file(), "the best-effort Derived Values source is missing"
    module = _fixture_module()
    assert module.DERIVED_VALUE_SOURCE == "tools/java/Phase4ChainCDerivedValue.java"
    assert module.DERIVED_VALUE_ENTRYPOINT.split("#")[0] == DERIVED_SOURCE.stem
    source = DERIVED_SOURCE.read_text(encoding="utf-8")
    assert "// effect: WRITE" in source, "the Java artifact must declare its effect"
    assert "public static Object run(Model model, Map<String, Object> arguments)" in source
    # The fixture only calls this artifact through a trusted-code tool it discovered live.
    assert "trusted_code_tool" in _source()


def test_chain_c_fixture_handshake_only_produces_a_receipt(tmp_path):
    pytest.importorskip("mcp", reason="the stdio child needs the MCP SDK")
    run_dir = tmp_path / "handshake"
    reply = subprocess.run(_driver_args(run_dir, "--handshake-only"), cwd=ROOT,
                           capture_output=True, text=True, timeout=300, env=_clean_env())
    assert reply.returncode == 0, reply.stdout + reply.stderr
    receipt = json.loads((run_dir / "chain_c_fixture_receipt.json").read_text(encoding="utf-8"))
    assert receipt["kind"] == "phase4-chain-c-user-style-fixture"
    assert receipt["constraints"]["study_tag"] == REQUIRED_STUDY_TAG
    assert receipt["constraints"]["step_tag"] == REQUIRED_STEP_TAG
    assert receipt["constraints"]["satisfied"] is True
    assert receipt["steps"] and receipt["steps"][0]["step"] == "handshake"
    assert receipt["session"]["tool_count"] > 40
    assert "operation_call" in (run_dir / "transcript.json").read_text(encoding="utf-8")
    assert receipt["environment"]["comsol_lifecycle"].startswith("not managed")


def test_chain_c_fixture_live_without_an_engine_is_blocked(tmp_path):
    pytest.importorskip("mcp", reason="the stdio child needs the MCP SDK")
    with socket.socket() as probe:  # a port nothing can answer on
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    run_dir = tmp_path / "blocked"
    reply = subprocess.run(_driver_args(run_dir, "--live", "--host", "127.0.0.1", "--port", str(port)),
                           cwd=ROOT, capture_output=True, text=True, timeout=600, env=_clean_env())
    assert reply.returncode == 3, reply.stdout + reply.stderr
    receipt = json.loads((run_dir / "chain_c_fixture_receipt.json").read_text(encoding="utf-8"))
    assert receipt["exit_code"] == 3
    statuses = {row["status"] for row in receipt["steps"]}
    assert "BLOCKED" in statuses or "FAILED" in statuses, receipt["steps"]
    codes = {row.get("error_code") for row in receipt["steps"]}
    assert codes & BLOCKED_CODES, codes
    assert not receipt["mph"]["exists"], "a blocked build must not claim a model file"
    assert receipt["mph"]["sha256"] is None
    assert receipt["derived_values"]["mode"] == "skipped"
    assert (run_dir / "primary.engine.log").is_file()
