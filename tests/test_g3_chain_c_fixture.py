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
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
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
                   "--host", "--port", "--private-home", "--python", "--self-check",
                   "--sibling-geometry-tag", "--with-result-nodes", "--no-result-nodes",
                   "--allow-missing-result-nodes",
                   "--missing-dependency-plan", "--inspect-missing-dependency",
                   "--missing-dependency-donor", "--missing-dependency-out"):
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

    The control-plane fallback tools (``operation_call``/``operation_describe``) are
    *tools*, not catalogue operations - ``validate_call`` is the wrong validator for
    them and rightly answers ``UNSUPPORTED_OPERATION``.  They are checked the way the
    contract actually works instead: their payload shape plus a catalogue lookup of
    the operation id they carry, so a typo inside ``operation_call`` still fails here.
    """
    pytest.importorskip("mcp", reason="the registry imports the MCP SDK")
    sys.path.insert(0, str(ROOT))
    from comsol_mcp._g2_contract import ExecutionContractError
    from comsol_mcp._g2_registry import BY_ID, validate_call

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

    #: The published control-plane fallback tools.  These are tools, not catalogue
    #: operations: ``validate_call`` answers UNSUPPORTED_OPERATION for them by design,
    #: so they are checked by shape + a catalogue lookup of the id they carry.
    control_tools = {"operation_call", "operation_describe"}
    control_payloads: dict[str, list[dict[str, Any]]] = {}

    def check(operation_id: str, payload: Mapping[str, Any], where: str, defects: list[str]) -> None:
        if operation_id in control_tools:
            control_payloads.setdefault(operation_id, []).append(dict(payload))
            extra = set(payload) - ({"operation_id", "arguments"} if operation_id == "operation_call"
                                    else {"operation_id"})
            if extra:
                defects.append(f"{where}: {operation_id} -> unexpected keys {sorted(extra)}")
            if not isinstance(payload.get("operation_id"), str):
                defects.append(f"{where}: {operation_id} -> operation_id must be a string")
            return
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

    module = _fixture_module()
    # The control-plane tools are published and used, and the operation ids they
    # carry are real catalogue entries - checked here, not assumed.
    assert set(control_payloads) == control_tools, sorted(control_payloads)
    assert module.TRUSTED_CODE_OPERATION in BY_ID, "the trusted-code operation must be catalogued"
    assert set(module.RESULT_NODE_OPERATIONS) <= set(BY_ID)
    # The fixture's route rule must agree with the registry's own statuses: it is
    # available exactly when the trusted-code operation is implemented and every
    # result operation is fully supported.
    statuses = {name: BY_ID[name].implementation_status for name in module.RESULT_NODE_OPERATIONS}
    trusted_status = BY_ID[module.TRUSTED_CODE_OPERATION].implementation_status
    assert trusted_status in {"SUPPORTED", "SUPPORTED_UNVERIFIED"}, trusted_status
    route = module.resolve_trusted_code_route(describe_status=trusted_status, trusted_code_enabled=True,
                                              published_tools=(), profile="expert", result_operations=statuses)
    assert route["available"] is all(value == "SUPPORTED" for value in statuses.values()), route
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

    The guard is on the *builder*: it may read and hash ``tools/phase4_run_mcp.py`` (the receipt
    records which driver revision built the fixture) and must never write it.  A ``git status``
    check on the driver was how this was once expressed, but the driver is a normal, fully editable
    artifact of the acceptance suite — other work on the suite edits it on purpose — so the marker
    says nothing about the builder.  What the builder does is checked directly: no write path in its
    source, and its own hashing helper resolves the driver.
    """
    source = _source()
    for mode in ("w", "a", "x"):
        assert f'DRIVER_PATH.open("{mode}' not in source
    assert "DRIVER_PATH.write_text" not in source
    assert "DRIVER_PATH.unlink" not in source
    assert "DRIVER_PATH.rename" not in source
    # The receipt proves which driver revision built the fixture: the builder hashes the *reused*
    # driver with the driver's own helper, and that digest is the driver's actual bytes.
    assert "_hash_sources([DRIVER_PATH" in source, "the receipt must hash the reused driver"
    assert DRIVER.is_file()
    driver = _fixture_module().load_driver()
    rows = driver._hash_sources([DRIVER])
    assert isinstance(rows, list) and rows and rows[0].get("exists") is True, rows
    assert rows[0].get("sha256") == hashlib.sha256(DRIVER.read_bytes()).hexdigest(), (
        "the builder's receipt must hash the driver's actual bytes"
    )


def test_chain_c_fixture_derived_value_artifact_matches_the_plan():
    assert DERIVED_SOURCE.is_file(), "the best-effort Derived Values source is missing"
    module = _fixture_module()
    assert module.DERIVED_VALUE_SOURCE == "tools/java/Phase4ChainCDerivedValue.java"
    assert module.DERIVED_VALUE_ENTRYPOINT.split("#")[0] == DERIVED_SOURCE.stem
    source = DERIVED_SOURCE.read_text(encoding="utf-8")
    assert "// effect: WRITE" in source, "the Java artifact must declare its effect"
    assert "public static Object run(Model model, Map<String, Object> arguments)" in source
    # The result-node step goes through the published control-plane fallback, not
    # through a guessed tool name: the route is described before it is called.
    assert "published_trusted_code_tool" in _source()
    assert "operation_describe" in _source() and "attempt_result_nodes" in _source()


def test_chain_c_fixture_result_node_artifact_matches_the_route():
    """The result-node Java artifact must exist, declare itself, and compile.

    ``javac`` and the installed COMSOL API jar are both optional on a developer
    machine, so the compile is skipped (never silently passed) when either is
    absent; when both are present the artifact is compiled against the *installed*
    API jar - the same jar the worker compiles against - which is the strongest
    offline evidence this repository can produce for a Java artifact.
    """
    artifact = ROOT / "tools" / "java" / "Phase4ChainCResultNodes.java"
    module = _fixture_module()
    assert artifact.is_file(), "the result-node source is missing"
    assert module.RESULT_NODE_SOURCE == "tools/java/Phase4ChainCResultNodes.java"
    assert module.RESULT_NODE_ENTRYPOINT.split("#")[0] == artifact.stem
    source = artifact.read_text(encoding="utf-8")
    assert "// effect: WRITE" in source, "the Java artifact must declare its effect"
    assert "public static Object run(Model model, Map<String, Object> arguments)" in source
    # The documented association is the numerical node's own `table` property.
    assert '.set("table"' in source
    assert '.table().create(' in source and '.numerical().create(' in source
    assert "REFUSED" in source, "a refusal must be recorded, never reported as a pass"

    javac = shutil.which("javac")
    jar = _comsol_api_jar()
    if javac is None or jar is None:
        pytest.skip(f"javac={javac!r} comsol_api_jar={jar!r}: the compile check needs both")
    with tempfile.TemporaryDirectory() as out:
        reply = subprocess.run([str(javac), "-Xlint:all", "-cp", str(jar), "-d", out, str(artifact)],
                               capture_output=True, text=True, timeout=300)
        assert reply.returncode == 0, reply.stdout + reply.stderr
        assert (Path(out) / "Phase4ChainCResultNodes.class").is_file()


def _comsol_api_jar() -> Path | None:
    candidates = []
    root = os.environ.get("COMSOL_ROOT")
    if root:
        candidates.append(Path(root) / "apiplugins" / "com.comsol.api_1.0.0.jar")
    candidates.append(Path("/Applications/COMSOL64/Multiphysics/apiplugins/com.comsol.api_1.0.0.jar"))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


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
    # The build blocks before the result-node step, so that step is "not_reached"
    # (never "not_requested", which would mean the caller opted out).
    assert receipt["result_nodes"]["mode"] == "not_reached", receipt["result_nodes"]
    assert receipt["derived_values"] == receipt["result_nodes"], "one object, one source"
    assert receipt["external_blockers"] == [], "a blocked build is not an external blocker"
    assert (run_dir / "primary.engine.log").is_file()


def test_chain_c_fixture_self_check_is_green_offline():
    """``--self-check`` replays the recorded metadata through the pure helpers."""
    reply = subprocess.run([sys.executable, str(FIXTURE), "--self-check"], cwd=ROOT,
                           capture_output=True, text=True, timeout=120, env=_clean_env())
    assert reply.returncode == 0, reply.stdout + reply.stderr
    payload = json.loads(reply.stdout[:reply.stdout.rindex("}") + 1])
    assert payload["ok"] is True, payload
    # The property is selected from the engine's own enumeration, not remembered.
    assert payload["initial_temperature_property"] == "Tinit"
    assert payload["initial_temperature_source_property"] == "Tinit_src"
    assert payload["initial_temperature_absent_candidates"] == ["T0"]
    # The recorded mesh feature is found although its type_id is MeshSizeDefault.
    assert payload["mesh_size_feature"] == "size"
    assert payload["mesh_size_selection_rule"] == "documented_tag"
    # The recorded material *node* schema does not describe the property group names.
    assert payload["material_node_publishes_no_property_group_names"] is True
    # The recorded deployment blocks the route, and unblocking it makes it available.
    assert payload["result_route_available_on_a_blocked_deployment"] is False
    assert payload["result_route_available_when_unblocked"] is True
    assert set(payload["result_route_blocker_kinds"]) == {"permission_not_enabled",
                                                          "result_operations_not_implemented"}
    assert payload["missing_plan_steps"] == []
    assert "transcript.json" in payload["recorded_metadata_source"]


def test_chain_c_fixture_selects_the_initial_property_from_the_engine_enumeration():
    """The v11 build wrote a remembered ``T0`` and the engine does not have one."""
    module = _fixture_module()
    select = module.select_initial_temperature_property

    # The recorded enumeration: Tinit + Tinit_src exist, T0 does not.
    rows = [{"name": "Tinit_src", "kind": "string", "shape_rank": 0, "allowed_values": ["userdef"]},
            {"name": "Tinit", "kind": "string", "shape_rank": 0},
            {"name": "StudyStep", "kind": "string", "shape_rank": 0}]
    choice = select(rows)
    assert choice["property"] == "Tinit"
    assert choice["source_property"] == "Tinit_src"
    assert choice["absent"] == ["T0"]
    assert choice["published"] == ["StudyStep", "Tinit", "Tinit_src"]

    # A feature that publishes only the older name is still writable.
    legacy = select([{"name": "T0", "kind": "float64", "shape_rank": 0}])
    assert legacy["property"] == "T0" and legacy["absent"] == ["Tinit"]

    # A feature that publishes neither is refused, never guessed.
    empty = select([{"name": "StudyStep", "kind": "string", "shape_rank": 0}])
    assert empty["property"] is None and empty["absent"] == ["Tinit", "T0"]


def test_chain_c_fixture_selects_the_mesh_size_feature_by_tag_not_by_type_id():
    """The recorded sequence reports ``MeshSizeDefault`` for the feature ``size``."""
    module = _fixture_module()
    select = module.select_mesh_size_feature

    recorded = {"current_feature": "size",
                "features": [{"tag": "size", "type_id": "MeshSizeDefault"}]}
    choice = select(recorded)
    assert choice["tag"] == "size" and choice["rule"] == "documented_tag"

    # Without a current_feature the documented tag still finds it.
    assert select({"features": [{"tag": "size", "type_id": "MeshSizeDefault"}]})["tag"] == "size"
    # A build that reports the type family instead is also matched.
    family = select({"features": [{"tag": "msz1", "type_id": "MeshSizeExplicit"}]})
    assert family["tag"] == "msz1" and family["rule"] == "type_family"
    # An unrelated feature is never mistaken for the size feature.
    other = select({"current_feature": "ftri1",
                    "features": [{"tag": "ftri1", "type_id": "FreeTri"}]})
    assert other["tag"] is None and other["observed"]
    # The equality test the v11 build used would have missed the recorded sequence.
    assert not [row for row in recorded["features"] if row["type_id"] == "Size"], "the recorded type_id"


def test_chain_c_fixture_documented_group_metadata_is_the_three_heat_transfer_names():
    module = _fixture_module()
    assert module.MATERIAL_PROPERTY_GROUP == "def"
    documented = module.documented_group_metadata(("thermalconductivity", "density", "heatcapacity", "unknown"))
    assert sorted(documented) == ["density", "heatcapacity", "thermalconductivity"]
    assert documented["thermalconductivity"]["shape_rank"] == 2
    assert documented["density"]["shape_rank"] == 0
    assert module.documented_group_metadata(("resistivity",)) == {}


def test_chain_c_fixture_result_route_needs_the_deployment_to_allow_it():
    """The route is available only when all three recorded facts line up."""
    module = _fixture_module()
    resolve = module.resolve_trusted_code_route
    supported = {name: "SUPPORTED" for name in module.RESULT_NODE_OPERATIONS}

    blocked = resolve(describe_status="SUPPORTED_UNVERIFIED", trusted_code_enabled=False,
                      published_tools=("model_create", "operation_call"), profile="full",
                      result_operations={name: "PROPOSED_NOT_IMPLEMENTED"
                                         for name in module.RESULT_NODE_OPERATIONS})
    assert blocked["available"] is False
    assert [item["kind"] for item in blocked["blockers"]] == ["permission_not_enabled",
                                                              "result_operations_not_implemented"]
    assert blocked["tool_published"] is False
    assert blocked["trusted_code_config"] == "COMSOL_MCP_TRUSTED_CODE"

    unblocked = resolve(describe_status="SUPPORTED_UNVERIFIED", trusted_code_enabled=True,
                        published_tools=("model_create", "operation_call"), profile="expert",
                        result_operations=supported)
    assert unblocked["available"] is True and unblocked["blockers"] == []

    undescribed = resolve(describe_status=None, trusted_code_enabled=True, published_tools=(),
                          profile="expert", result_operations=supported)
    assert undescribed["available"] is False
    assert [item["kind"] for item in undescribed["blockers"]] == ["operation_not_described"]


def test_chain_c_fixture_every_published_call_gets_its_own_idempotency_key():
    """A repeated stem must never reuse a dispatched key (IDEMPOTENCY_CONFLICT)."""
    module = _fixture_module()
    fixture = object.__new__(module.ChainCFixture)
    fixture.call_sequence = 0
    keys = []
    for stem in ("fixture-init-init1-schema", "fixture-init-init1", "fixture-init-init1-readback"):
        fixture.call_sequence += 1
        keys.append(fixture.dispatch_key(stem))
    assert len(set(keys)) == 3, keys
    assert keys[0].startswith("fixture-init-init1-schema-")
    assert keys[0].split("-")[-1] == "001" and keys[-1].split("-")[-1] == "003"


def test_chain_c_fixture_plan_carries_the_user_style_extras():
    module = _fixture_module()
    steps = {item["step"]: item for item in module.PLAN_STEPS}
    assert module.DEFAULT_SIBLING_GEOMETRY_TAG == "geom2"
    assert "sibling_geometry" in steps and "geometry.sequence_create" in steps["sibling_geometry"]["operation"]
    assert "result_nodes" in steps and "code.execute_java" in steps["result_nodes"]["operation"]
    for step in ("physics_initial_values", "mesh_size", "manual_solver_edit", "study_step_tlist"):
        assert step in steps, step
    plan = subprocess.run([sys.executable, str(FIXTURE), "--dry-run"], cwd=ROOT,
                          capture_output=True, text=True, timeout=120, env=_clean_env())
    assert plan.returncode == 0, plan.stdout + plan.stderr
    assert "sibling geom2" in plan.stdout or "geom2" in plan.stdout
    assert "Phase4ChainCResultNodes.java" in plan.stdout
    assert "COMSOL_MCP_TRUSTED_CODE" in plan.stdout
    assert "exit code 4" in plan.stdout, "the default outcome of a missing result node must be visible"
    tolerated = subprocess.run([sys.executable, str(FIXTURE), "--dry-run", "--allow-missing-result-nodes"],
                               cwd=ROOT, capture_output=True, text=True, timeout=120, env=_clean_env())
    assert tolerated.returncode == 0, tolerated.stdout + tolerated.stderr
    assert "tolerated" in tolerated.stdout
    refused = subprocess.run([sys.executable, str(FIXTURE), "--dry-run", "--no-result-nodes",
                              "--with-derived-value"],
                             cwd=ROOT, capture_output=True, text=True, timeout=120, env=_clean_env())
    assert refused.returncode == 2 and "--with-derived-value" in refused.stderr


# --- T034: the missing-dependency fixture (offline half) ----------------------


def test_chain_c_fixture_missing_dependency_plan_is_reproducible():
    """The T034 path must be spelled out as steps, not as "see the driver"."""
    reply = subprocess.run([sys.executable, str(FIXTURE), "--missing-dependency-plan"], cwd=ROOT,
                           capture_output=True, text=True, timeout=120, env=_clean_env())
    assert reply.returncode == 0, reply.stdout + reply.stderr
    plan = json.loads(reply.stdout[:reply.stdout.rindex("}") + 1])
    assert plan["kind"] == "phase4-t034-missing-dependency-plan"
    assert plan["output"] == "missing_dependency.mph"
    # The source file's bytes are fixed, so two people produce the same dependency.
    assert plan["source"]["content"] == "0 0\n1 1\n"
    assert plan["source"]["sha256"] == hashlib.sha256(b"0 0\n1 1\n").hexdigest()
    steps = [item["step"] for item in plan["steps"]]
    assert steps == ["source-file", "scratch-model", "interpolation-function", "save",
                     "delete-source-and-unload", "reload", "dependency-readback"], steps
    assert {"function.create", "model.save", "model.load", "model.remove"} <= set(plan["operations"])
    assert plan["function_tag"] == "phase4_missing_dep"
    # No .mph is claimed offline; the missing model is the whole point of T034.
    assert "no .mph is produced offline" in " ".join(plan["unverified"])
    assert plan["engine_side_artifact"]["entrypoint"].split("#")[0] == "Phase4MissingDependency"


def test_chain_c_fixture_missing_dependency_plan_records_both_external_sub_items():
    reply = subprocess.run([sys.executable, str(FIXTURE), "--missing-dependency-plan"], cwd=ROOT,
                           capture_output=True, text=True, timeout=120, env=_clean_env())
    plan = json.loads(reply.stdout[:reply.stdout.rindex("}") + 1])
    sub_items = plan["external_sub_items"]
    assert sub_items["interpolation_file_dependency"]["status"] == "EXTERNALLY_BLOCKED_OFFLINE"
    cad = sub_items["cad_import"]
    assert cad["status"] == "EXTERNALLY_BLOCKED_WITHOUT_A_CAD_PRODUCT"
    assert "CAD" in cad["reason"] and "only this sub-item" in cad["reason"]


def _donor() -> Path:
    module = _fixture_module()
    donor = ROOT / module.MISSING_DEPENDENCY_DONOR
    if not donor.is_file():
        pytest.skip(f"the recorded donor container is not in this checkout: {donor}")
    return donor


def _inspect(path: Path) -> tuple[int, dict[str, Any]]:
    reply = subprocess.run([sys.executable, str(FIXTURE), "--inspect-missing-dependency", str(path)],
                           cwd=ROOT, capture_output=True, text=True, timeout=180, env=_clean_env())
    return reply.returncode, json.loads(reply.stdout[:reply.stdout.rindex("}") + 1])


def _recorded_probe() -> Path:
    probe = (ROOT / "evidence" / "phase4" / "runs" / "20260920T130620Z-g3-live" / "driver5c"
             / "local dir with spaces" / "中文目录" / "missing_dependency.mph")
    if not probe.is_file():
        pytest.skip(f"the recorded T034 probe is not in this checkout: {probe}")
    return probe


def test_chain_c_fixture_missing_dependency_inspector_rejects_the_recorded_probe():
    """R-10's text probe must be *detected*, not accepted as the fixture."""
    module = _fixture_module()
    probe = _recorded_probe()
    assert probe.read_bytes() == module.MISSING_DEPENDENCY_PROBE_BYTES
    code, payload = _inspect(probe)
    assert code == 1, payload
    assert payload["status"] == "FAIL"
    assert "not a COMSOL container" in payload["reason"]
    assert payload["container"]["probe"] is True
    assert payload["container"]["probe_bytes"] == "phase4 missing-dependency probe\n"
    assert payload["container"]["sha256"] == hashlib.sha256(probe.read_bytes()).hexdigest()


def test_chain_c_fixture_missing_dependency_inspector_reads_a_real_container():
    """The donor is a container COMSOL wrote; its dependency is recorded but unset."""
    donor = _donor()
    code, payload = _inspect(donor)
    assert code == 2, payload  # cannot decide: nothing is referenced yet
    assert payload["status"] == "NOT_RUN"
    container = payload["container"]
    assert container["is_container"] is True
    assert container["fileversion"].startswith("2092:COMSOL 6.4")
    # The CAD product fact the plan records comes from this very file.
    assert "CADIMPORT" in container["used_licenses"]
    dependency = payload["dependency"]
    assert dependency["found"] is True
    assert dependency["model_entity_path"] == "/func/int1"
    assert dependency["property"] == "filename"
    assert dependency["value"] == "", "the recorded function references no file"
    assert "CSV" in dependency["file_types"]


def test_chain_c_fixture_missing_dependency_producer_round_trip(tmp_path):
    """A produced fixture must pass the same validator a reviewer would run."""
    donor = _donor()
    out_dir = tmp_path / "t034 fixture" / "中文目录"
    source = out_dir / "missing_dependency_source.csv"
    reply = subprocess.run([sys.executable, str(FIXTURE), "--missing-dependency-donor", str(donor),
                            "--missing-dependency-out", str(out_dir), "--missing-dependency-source", str(source)],
                           cwd=ROOT, capture_output=True, text=True, timeout=300, env=_clean_env())
    assert reply.returncode == 0, reply.stdout + reply.stderr
    produced = json.loads(reply.stdout[:reply.stdout.rindex("}") + 1])
    assert produced["status"] == "PRODUCED", produced.get("reason")
    assert produced["function_tag"] == "int1"
    output = Path(produced["output"]["path"])
    assert output.is_file() and produced["output"]["members"] > 5
    # The referenced file is absent, and the container says so.
    assert not source.exists()
    assert produced["dependency_record"]["value"] == str(source)
    assert produced["dependency_record"]["referenced_file_exists"] is False
    assert "that COMSOL reports the dependency" in " ".join(produced["unverified"])
    code, payload = _inspect(output)
    assert code == 0, payload
    assert payload["status"] == "PASS"
    assert payload["dependency"]["value"] == str(source)
    assert payload["unverified"], "the live reload stays unverified and must say so"

    # Refuse to overwrite, then allow it explicitly.
    again = subprocess.run([sys.executable, str(FIXTURE), "--missing-dependency-donor", str(donor),
                            "--missing-dependency-out", str(out_dir), "--missing-dependency-source", str(source)],
                           cwd=ROOT, capture_output=True, text=True, timeout=300, env=_clean_env())
    assert again.returncode == 2, again.stdout + again.stderr
    assert "exists" in json.loads(again.stdout[:again.stdout.rindex("}") + 1])["reason"]
    forced = subprocess.run([sys.executable, str(FIXTURE), "--missing-dependency-donor", str(donor),
                             "--missing-dependency-out", str(out_dir), "--missing-dependency-source", str(source),
                             "--overwrite"], cwd=ROOT, capture_output=True, text=True, timeout=300, env=_clean_env())
    assert forced.returncode == 0, forced.stdout + forced.stderr


def test_chain_c_fixture_missing_dependency_source_needs_its_out_dir():
    refused = subprocess.run([sys.executable, str(FIXTURE), "--missing-dependency-donor", "x.mph"],
                             cwd=ROOT, capture_output=True, text=True, timeout=120, env=_clean_env())
    assert refused.returncode == 2 and "--missing-dependency-out" in refused.stderr
    both = subprocess.run([sys.executable, str(FIXTURE), "--missing-dependency-plan", "--self-check"],
                          cwd=ROOT, capture_output=True, text=True, timeout=120, env=_clean_env())
    assert both.returncode == 2 and "one offline action" in both.stderr


def test_chain_c_fixture_missing_dependency_source_artifact_matches_the_plan():
    artifact = ROOT / "tools" / "java" / "Phase4MissingDependency.java"
    module = _fixture_module()
    assert artifact.is_file(), "the engine-side T034 artifact is missing"
    assert module.MISSING_DEPENDENCY_SOURCE_ARTIFACT == "tools/java/Phase4MissingDependency.java"
    assert module.MISSING_DEPENDENCY_ENTRYPOINT.split("#")[0] == artifact.stem
    source = artifact.read_text(encoding="utf-8")
    assert "// effect: WRITE" in source
    assert "model.func().create(" in source and "model.save(" in source
    # The property names are the ones a container COMSOL wrote enumerates.
    for property_name in ("sourcetype", "filename", "funcname", "filetype", "dseparator"):
        assert '"%s"' % property_name in source, property_name
    assert "harness steps" in " ".join(source.split()), "the harness deletes the file, not the artifact"

    javac = shutil.which("javac")
    jar = _comsol_api_jar()
    if javac is None or jar is None:
        pytest.skip(f"javac={javac!r} comsol_api_jar={jar!r}: the compile check needs both")
    with tempfile.TemporaryDirectory() as out:
        reply = subprocess.run([str(javac), "-Xlint:all", "-cp", str(jar), "-d", out, str(artifact)],
                               capture_output=True, text=True, timeout=300)
        assert reply.returncode == 0, reply.stdout + reply.stderr
        assert (Path(out) / "Phase4MissingDependency.class").is_file()
