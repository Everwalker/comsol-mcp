#!/usr/bin/env python3
"""Generate a static, source-traced map for the universal COMSOL MCP workpack.

This tool parses JSON and Python syntax trees only. It does not import product
modules, start a Worker, connect to COMSOL, or execute acceptance tests.
"""
from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CATALOG = Path("comsol_mcp/data/g2/02_ACTION_CATALOG.json")
PIN = Path("docs/universal_mcp_execution/PIN.json")
RESTORE = Path("docs/universal_mcp_execution/RESTORE_RECEIPT.json")
MASTER = Path("docs/universal_mcp_execution/MASTER_GOAL.md")
SCOPE = Path("docs/universal_mcp_execution/SCOPE_RESET.md")
DOD = Path("docs/universal_mcp_execution/RELEASE_DOD.md")
ARCH = Path("docs/universal_mcp_execution/ARCHITECTURE.md")
TARGETS = Path("docs/universal_mcp_execution/config/TARGETS.json")
ACCEPTANCE = Path("docs/comsol_mcp_design_v1/03_ACCEPTANCE.md")
OLD_MATRIX = Path("docs/full_project_execution/state/ACCEPTANCE_MATRIX.json")
OLD_ACTIONS = Path("docs/full_project_execution/state/ACTION_COVERAGE.json")
OLD_DECISIONS = Path("docs/full_project_execution/USER_SCOPE_DECISIONS.json")
OUTPUT = Path("docs/universal_mcp_execution/state/GENERAL_COVERAGE.json")
INITIAL_CATALOG = Path("docs/comsol_mcp_design_v1/02_ACTION_CATALOG.json")
API_SOURCE = Path("comsol_mcp/_g2_public_api.py")
API_ACTION_IDS = ("api.describe", "api.invoke", "api.probe")
MAPPING_KIND = "static_source_route_and_frozen_acceptance_scope"
CASE_CORE_RULE = "案例中的CORE_REQUIRED指底层通用义务，可以由等价通用模型履行，不要求完成或重跑原案例科学端点；427个子断言ID和分类保持冻结。"
API_VERSION_POLICY = (
    "VERSION is a compatibility constant only. Invocation is gated by supported_releases "
    "and the actual reviewed receiver/method/signature; it is not locked to the VERSION build. "
    "api.describe metadata does not widen invoke permission. api.probe's invoke branch "
    "reuses that policy; its create_feature branch uses typed node-action validation."
)
INITIAL_UNIT_RUN = Path("docs/universal_mcp_execution/evidence/unit-default/initial.json")
POSTFIX_UNIT_RUN = Path("docs/universal_mcp_execution/evidence/unit-default/first-regression.json")
POSTFIX_UNIT_JUNIT = Path("docs/universal_mcp_execution/evidence/unit-default/junit.xml")
ADJACENT_UNIT_JUNIT = Path("docs/universal_mcp_execution/evidence/unit-default/affected-junit.xml")

PINNED_STATIC_FILES = {
    CATALOG,
    ACCEPTANCE,
    OLD_MATRIX,
    OLD_ACTIONS,
    OLD_DECISIONS,
    Path("comsol_mcp/_g2_registry.py"),
    Path("comsol_mcp/_g2_tools.py"),
    Path("comsol_mcp/_g2_public_api.py"),
    Path("comsol_mcp/_g2_engine.py"),
    Path("comsol_mcp/_g2_checkpoint_ops.py"),
    Path("comsol_mcp/_g2_checkpoint_diff.py"),
    Path("comsol_mcp/_g2_model_compare.py"),
    Path("comsol_mcp/_g2_transactions.py"),
    Path("comsol_mcp/_g3_results.py"),
    Path("comsol_mcp/_g3_ops.py"),
    Path("comsol_mcp/_managed_backend.py"),
    Path("comsol_mcp/_control_daemon.py"),
    Path("comsol_mcp/_mcp_gateway.py"),
    Path("comsol_mcp/mcp_server.py"),
    Path("tests/test_g3_results.py"),
    Path("tests/test_interp_native_default_units.py"),
}
OVERLAY_FILES = {
    MASTER, SCOPE, DOD, ARCH, TARGETS, PIN, RESTORE,
    INITIAL_UNIT_RUN, POSTFIX_UNIT_RUN, POSTFIX_UNIT_JUNIT, ADJACENT_UNIT_JUNIT,
}

CORE_DEFAULT = "CORE_REQUIRED"
CASE_SPECIAL = "CASE_SPECIALIZED"

DESKTOP_ADAPTER_METHODS = {
    "desktop.bind": {"coordinator": "_bind", "adapter": ["observe_model"]},
    "desktop.show_model": {"coordinator": "_show_model", "adapter": ["show_model"]},
    "desktop.select_node": {"coordinator": "_execute_operation", "adapter": ["select_node"]},
    "desktop.capture": {"coordinator": "_execute_operation", "adapter": ["capture"]},
    "desktop.action": {"coordinator": "_execute_operation", "adapter": ["resolve_action", "perform_action"]},
    "desktop.shell_execute": {"coordinator": "_execute_operation", "adapter": ["shell_execute"]},
    "desktop.migrate_standalone": {
        "coordinator": "_migrate",
        "adapter": ["save_copy", "save_copy_completion_state", "verify_source_preserved"],
    },
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_json(path: Path) -> Any:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def source_text(path: Path) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def static_assignment(path: Path, name: str, *, text: str | None = None) -> Any:
    tree = ast.parse(source_text(path) if text is None else text, filename=str(path))
    assignments = module_assignments(tree)
    return assignments.get(name, (None, None))[0]


def parse_api_capabilities(path: Path, *, text: str | None = None) -> list[dict[str, Any]]:
    """Statically expand the API module's _cap declarations and typed-family loop."""
    tree = ast.parse(source_text(path) if text is None else text, filename=str(path))
    capability_class = next(
        (node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Capability"),
        None,
    )
    helper = next(
        (node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_cap"),
        None,
    )
    if capability_class is None or helper is None:
        raise ValueError("public API static capability declaration shape changed")
    field_names = [
        node.target.id for node in capability_class.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    ]
    return_node = next(
        (node.value for node in helper.body if isinstance(node, ast.Return) and isinstance(node.value, ast.Call)),
        None,
    )
    if return_node is None or not isinstance(return_node.func, ast.Name) or return_node.func.id != "Capability":
        raise ValueError("public API _cap helper no longer returns a static Capability constructor")
    env: dict[str, Any] = {}

    def bind_names(target: ast.AST, value: Any) -> None:
        if isinstance(target, ast.Name):
            env[target.id] = value
            return
        if isinstance(target, (ast.Tuple, ast.List)) and isinstance(value, (tuple, list)) and len(target.elts) == len(value):
            for child, part in zip(target.elts, value):
                bind_names(child, part)
            return
        raise ValueError(f"unsupported public API static assignment target: {ast.dump(target)}")

    def capability_from_call(node: ast.Call) -> dict[str, Any]:
        positional = [*helper.args.posonlyargs, *helper.args.args]
        names = [arg.arg for arg in positional]
        defaults = [None] * (len(names) - len(helper.args.defaults)) + list(helper.args.defaults)
        local = {
            name: evaluate(default) if default is not None else None
            for name, default in zip(names, defaults)
        }
        keyword_only = [arg.arg for arg in helper.args.kwonlyargs]
        for name, default in zip(keyword_only, helper.args.kw_defaults):
            local[name] = evaluate(default) if default is not None else None
        if len(node.args) > len(names) or any(isinstance(arg, ast.Starred) for arg in node.args):
            raise ValueError("unsupported positional form in public API _cap declaration")
        for name, arg in zip(names, node.args):
            local[name] = evaluate(arg)
        for keyword in node.keywords:
            if keyword.arg is None or keyword.arg not in local:
                raise ValueError("unsupported keyword form in public API _cap declaration")
            local[keyword.arg] = evaluate(keyword.value)
        arguments = [evaluate(arg, local) for arg in return_node.args]
        kwargs = {
            keyword.arg: evaluate(keyword.value, local)
            for keyword in return_node.keywords if keyword.arg is not None
        }
        values = {name: value for name, value in zip(field_names, arguments)}
        values.update(kwargs)
        if set(values) != set(field_names):
            raise ValueError("public API Capability constructor fields could not be statically resolved")
        return values

    def evaluate(node: ast.AST | None, local: dict[str, Any] | None = None) -> Any:
        if node is None:
            return None
        names = env if local is None else {**env, **local}
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id not in names:
                raise ValueError(f"unresolved public API static name: {node.id}")
            return names[node.id]
        if isinstance(node, ast.Tuple):
            return tuple(evaluate(value, local) for value in node.elts)
        if isinstance(node, ast.List):
            return [evaluate(value, local) for value in node.elts]
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return evaluate(node.left, local) + evaluate(node.right, local)
        if isinstance(node, ast.IfExp):
            return evaluate(node.body if evaluate(node.test, local) else node.orelse, local)
        if isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], ast.Eq):
            return evaluate(node.left, local) == evaluate(node.comparators[0], local)
        if isinstance(node, ast.JoinedStr):
            return "".join(
                str(evaluate(value.value, local)) if isinstance(value, ast.FormattedValue)
                else str(evaluate(value, local)) for value in node.values
            )
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_cap":
            return capability_from_call(node)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "tuple":
            if len(node.args) != 1:
                raise ValueError("unsupported tuple() use in public API declarations")
            return tuple(evaluate(node.args[0], local))
        raise ValueError(f"unsupported public API static expression: {ast.dump(node, include_attributes=False)}")

    def execute(statement: ast.stmt) -> None:
        if isinstance(statement, (ast.Assign, ast.AnnAssign)):
            value_node = statement.value
            if value_node is None:
                return
            targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
            try:
                value = evaluate(value_node)
            except ValueError:
                # The module has unrelated dynamic assignments. Names needed by
                # the capability table will fail closed when the table is read.
                return
            for target in targets:
                bind_names(target, value)
            return
        if isinstance(statement, ast.For):
            try:
                values = evaluate(statement.iter)
            except ValueError:
                if "_CAPABILITY_ROWS" in ast.dump(statement):
                    raise
                return
            for value in values:
                bind_names(statement.target, value)
                for child in statement.body:
                    execute(child)
            return
        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):
            call = statement.value
            if (isinstance(call.func, ast.Attribute) and call.func.attr == "append"
                    and isinstance(call.func.value, ast.Name) and call.func.value.id == "_CAPABILITY_ROWS"):
                rows = env.get("_CAPABILITY_ROWS")
                if not isinstance(rows, list) or len(call.args) != 1:
                    raise ValueError("public API capability append target is not a static list")
                rows.append(evaluate(call.args[0]))

    for statement in tree.body:
        execute(statement)
    capabilities = env.get("CAPABILITIES")
    if not isinstance(capabilities, tuple) or not capabilities or not all(isinstance(row, dict) for row in capabilities):
        raise ValueError("public API CAPABILITIES could not be statically expanded")
    return capabilities


def source_location(path: Path, symbol: str, *, text: str | None = None) -> dict[str, Any]:
    tree = ast.parse(source_text(path) if text is None else text, filename=str(path))
    parts = symbol.split(".")
    current: ast.AST = tree
    found: ast.AST | None = None
    for part in parts:
        candidates = [
            node for node in getattr(current, "body", [])
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == part
        ]
        if not candidates:
            candidates = [
                statement
                for statement in getattr(current, "body", [])
                if isinstance(statement, (ast.Assign, ast.AnnAssign))
                and any(
                    isinstance(target, ast.Name) and target.id == part
                    for target in (
                        statement.targets if isinstance(statement, ast.Assign)
                        else [statement.target]
                    )
                )
            ]
        if not candidates:
            found = None
            break
        found = candidates[0]
        current = found
    return {
        "path": path.as_posix(),
        "symbol": symbol,
        "line": getattr(found, "lineno", None),
        "end_line": getattr(found, "end_lineno", None),
    }


def source_definitions(path: Path) -> dict[str, ast.AST]:
    tree = ast.parse(source_text(path), filename=str(path))
    result: dict[str, ast.AST] = {}

    def walk(node: ast.AST, prefix: str = "") -> None:
        for child in getattr(node, "body", []):
            if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                name = f"{prefix}.{child.name}" if prefix else child.name
                result[name] = child
                walk(child, name)

    walk(tree)
    return result


def eval_static(node: ast.AST, env: dict[str, Any]) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return env.get(node.id, {"$name": node.id})
    if isinstance(node, ast.Dict):
        output: dict[Any, Any] = {}
        for key, value in zip(node.keys, node.values):
            if key is None:
                return {"$dynamic": ast.unparse(node)}
            k = eval_static(key, env)
            if isinstance(k, dict):
                return {"$dynamic": ast.unparse(node)}
            output[k] = value
        return output
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        values = [eval_static(item, env) for item in node.elts]
        return tuple(values) if isinstance(node, ast.Tuple) else values
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "frozenset":
        if node.args:
            value = eval_static(node.args[0], env)
            return set(value) if isinstance(value, (list, tuple, set)) else {"$dynamic": ast.unparse(node)}
        return set()
    return {"$dynamic": ast.unparse(node)}


def module_assignments(tree: ast.Module) -> dict[str, tuple[Any, ast.AST]]:
    env: dict[str, Any] = {}
    nodes: dict[str, ast.AST] = {}
    for statement in tree.body:
        if isinstance(statement, ast.Assign):
            names = [target.id for target in statement.targets if isinstance(target, ast.Name)]
            value_node = statement.value
        elif isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
            names = [statement.target.id]
            value_node = statement.value
        else:
            continue
        if value_node is None:
            continue
        value = eval_static(value_node, env)
        for name in names:
            env[name] = value
            nodes[name] = value_node
    return {name: (value, nodes[name]) for name, value in env.items() if name in nodes}


def parse_g3_operations() -> tuple[dict[str, dict[str, Any]], list[str], dict[str, dict[str, Any]]]:
    aggregate_path = Path("comsol_mcp/_g3_ops.py")
    tree = ast.parse(source_text(aggregate_path), filename=str(aggregate_path))
    assignments = module_assignments(tree)
    module_values = assignments.get("_MODULES", ([], None))[0]
    if not isinstance(module_values, (tuple, list)) or not all(isinstance(x, str) for x in module_values):
        raise ValueError("_g3_ops._MODULES is not a statically readable tuple")
    routes: dict[str, dict[str, Any]] = {}
    unresolved: list[str] = []
    file_effects: dict[str, Any] = {}
    raw_fallback = assignments.get("_FALLBACK_EFFECTS", ({}, None))[0]
    if isinstance(raw_fallback, dict):
        for operation_id, value_node in raw_fallback.items():
            if isinstance(operation_id, str) and isinstance(value_node, ast.Constant) and isinstance(value_node.value, str):
                file_effects[operation_id] = value_node.value
    for module_name in module_values:
        path = Path("comsol_mcp") / f"{module_name}.py"
        if not (ROOT / path).is_file():
            unresolved.append(module_name)
            continue
        mod_tree = ast.parse(source_text(path), filename=str(path))
        values = module_assignments(mod_tree)
        operations_value = values.get("OPERATIONS", (None, None))[0]
        if not isinstance(operations_value, dict):
            if operations_value not in (None, {}):
                unresolved.append(module_name)
            continue
        definitions = source_definitions(path)
        for operation_id, value_node in operations_value.items():
            if not isinstance(operation_id, str) or not isinstance(value_node, ast.AST):
                unresolved.append(module_name)
                continue
            handler_expr = ast.unparse(value_node)
            handler_name = handler_expr if re.fullmatch(r"[A-Za-z_]\w*", handler_expr) else handler_expr
            definition = definitions.get(handler_name)
            routes[operation_id] = {
                "module": module_name,
                "handler": handler_name,
                "path": path.as_posix(),
                "line": getattr(value_node, "lineno", None),
                "handler_line": getattr(definition, "lineno", None),
                "static_operation_table": f"{module_name}.OPERATIONS",
                "body_present": definition is not None,
            }
    return routes, unresolved, file_effects


def parse_registry_sets() -> tuple[dict[str, dict[str, int]], dict[str, int]]:
    path = Path("comsol_mcp/_g2_registry.py")
    tree = ast.parse(source_text(path), filename=str(path))
    found: dict[str, dict[str, int]] = {}
    for statement in tree.body:
        if not isinstance(statement, ast.Assign):
            continue
        if len(statement.targets) != 1 or not isinstance(statement.targets[0], ast.Name):
            continue
        name = statement.targets[0].id
        if name not in {
            "IMPLEMENTED_OPERATIONS",
            "CONTROL_IMPLEMENTED_OPERATIONS",
            "DESKTOP_COORDINATOR_ONLY_OPERATIONS",
        }:
            continue
        call = statement.value
        if not isinstance(call, ast.Call) or not call.args or not isinstance(call.args[0], ast.Set):
            raise ValueError(f"{name} is not a static frozenset literal")
        found[name] = {
            item.value: item.lineno
            for item in call.args[0].elts
            if isinstance(item, ast.Constant) and isinstance(item.value, str)
        }
    required = {
        "IMPLEMENTED_OPERATIONS",
        "CONTROL_IMPLEMENTED_OPERATIONS",
        "DESKTOP_COORDINATOR_ONLY_OPERATIONS",
    }
    if set(found) != required:
        raise ValueError(f"registry static set mismatch: {sorted(set(found) ^ required)}")
    return found, {name: statement.lineno for statement in tree.body
                   if isinstance(statement, ast.Assign)
                   and len(statement.targets) == 1
                   and isinstance(statement.targets[0], ast.Name)
                   and statement.targets[0].id in required}


def source_occurrences(operation_id: str) -> list[dict[str, Any]]:
    candidates = (
        Path("comsol_mcp/_managed_backend.py"),
        Path("comsol_mcp/_control_daemon.py"),
        Path("comsol_mcp/_g2_engine.py"),
        Path("comsol_mcp/_g2_public_api.py"),
        Path("comsol_mcp/_g2_checkpoint_ops.py"),
        Path("comsol_mcp/_g2_checkpoint_diff.py"),
        Path("comsol_mcp/_g2_model_compare.py"),
        Path("comsol_mcp/_g2_registry.py"),
        Path("comsol_mcp/_g3_ops.py"),
    )
    output: list[dict[str, Any]] = []
    for path in candidates:
        lines = source_text(path).splitlines()
        definitions = source_definitions(path)
        containing: list[tuple[int, int, str]] = []
        for symbol, node in definitions.items():
            start, end = getattr(node, "lineno", 0), getattr(node, "end_lineno", 0)
            if start and end:
                containing.append((start, end, symbol))
        for line_no, line in enumerate(lines, 1):
            if operation_id not in line:
                continue
            enclosing = [item for item in containing if item[0] <= line_no <= item[1]]
            symbol = min(enclosing, key=lambda item: item[1] - item[0])[2] if enclosing else None
            output.append({
                "path": path.as_posix(),
                "line": line_no,
                "symbol": symbol,
                "text": line.strip()[:240],
                "role": "literal_occurrence_only_not_proof",
            })
    priorities = {
        "_managed_backend.py": 0,
        "_control_daemon.py": 1,
        "_g2_engine.py": 2,
        "_g2_public_api.py": 3,
        "_g2_checkpoint_ops.py": 4,
        "_g2_checkpoint_diff.py": 5,
        "_g2_model_compare.py": 6,
        "_g3_ops.py": 7,
        "_g2_registry.py": 8,
    }
    output.sort(key=lambda item: (
        priorities.get(Path(item["path"]).name, 99),
        0 if item["symbol"] else 1,
        item["line"],
    ))
    return output[:8]


def registry_location(path: Path, symbol: str) -> dict[str, Any]:
    return source_location(path, symbol)


def operation_input_summary(item: dict[str, Any]) -> dict[str, Any]:
    schema = item.get("input_schema")
    if not isinstance(schema, dict):
        schema = {}
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    summary: dict[str, Any] = {}
    for name, definition in properties.items():
        if not isinstance(definition, dict):
            summary[name] = {"schema_node": type(definition).__name__}
            continue
        value: dict[str, Any] = {}
        for key in ("type", "$ref", "format", "const", "enum", "minimum", "maximum", "minLength", "maxLength"):
            if key in definition:
                entry = definition[key]
                if key == "enum" and isinstance(entry, list) and len(entry) > 16:
                    value["enum_count"] = len(entry)
                else:
                    value[key] = entry
        if isinstance(definition.get("items"), dict):
            item_schema = definition["items"]
            value["items"] = {
                key: item_schema[key] for key in ("type", "$ref", "format")
                if key in item_schema
            }
        if "required" in definition and isinstance(definition["required"], list):
            value["required"] = definition["required"]
        summary[name] = value
    compact = json.dumps(schema, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return {
        "arguments_signature": item.get("arguments_signature"),
        "top_level_required": schema.get("required", []),
        "top_level_properties": summary,
        "additional_properties": schema.get("additionalProperties"),
        "input_schema_sha256": sha256_bytes(compact),
    }


def binding_summary(item: dict[str, Any], operation_id: str) -> dict[str, Any]:
    scope = item.get("scope")
    if scope == "model":
        identity = "project/session/ModelRef envelope; model operations use the managed service revision gate"
        if operation_id == "runtime.capabilities" or operation_id == "runtime.license_inspect":
            identity = "runtime-scoped; optional ModelRef is inventory context only and expected_revision is forbidden"
        return {"catalog_scope": scope, "managed_subject": identity}
    if scope == "project":
        return {
            "catalog_scope": scope,
            "managed_subject": "project_id and project-scoped authorization/artifact store; no ModelRef unless an operation schema explicitly requires it",
        }
    if scope == "control":
        return {
            "catalog_scope": scope,
            "managed_subject": "runtime/session/control identity as declared by the operation; no model identity inferred",
        }
    return {"catalog_scope": scope, "managed_subject": "UNRESOLVED_FROM_CATALOG"}


def g2_handler_hint(operation_id: str) -> tuple[str, list[str]]:
    if operation_id.startswith("node."):
        return "generic_api", [
            "ManagedBackend._invoke_g2_model",
            "_g2_engine.prepare_node_action",
            "_g2_engine.execute_node_action",
        ]
    if operation_id == "api.describe":
        return "generic_api", ["ManagedBackend._invoke_g2_model", "_g2_public_api.describe_api"]
    if operation_id == "api.invoke":
        return "generic_api", [
            "ManagedBackend._invoke_g2_model",
            "_g2_public_api.prepare_invoke",
            "_g2_public_api.execute_prepared",
        ]
    if operation_id == "api.probe":
        return "generic_api", ["ManagedBackend._invoke_g2_model", "_g2_checkpoint_ops.invoke_owned"]
    if operation_id == "checkpoint.branch":
        return "managed_g2", ["ManagedBackend._invoke_g2_model", "_g2_checkpoint_ops.invoke_owned"]
    if operation_id == "checkpoint.diff":
        return "managed_g2", ["ManagedBackend._invoke_g2_model", "_g2_checkpoint_diff.invoke_diff"]
    if operation_id == "model.compare":
        return "managed_g2", ["ManagedBackend._invoke_g2_model", "_g2_model_compare.invoke_compare"]
    if operation_id == "model.adopt":
        return "managed_g2", ["ManagedBackend._invoke_g2_model", "ManagedBackend.adopt"]
    if operation_id == "model.inspect":
        return "managed_g2", ["ManagedBackend._invoke_g2_model"]
    if operation_id == "artifact.register":
        return "control_plane", ["ManagedBackend.invoke", "ManagedBackend._register_project_artifact"]
    if operation_id.startswith("registry."):
        return "control_plane", ["ManagedBackend.invoke", "_g2_registry.registry_*"]
    if operation_id == "desktop.status":
        return "control_plane", ["ControlDaemon.dispatch", "ControlDaemon._dispatch_desktop_control"]
    if operation_id == "job.resume":
        return "control_plane", ["ControlDaemon.dispatch", "ControlDaemon._dispatch_catalog_job_resume"]
    if operation_id.startswith("transaction."):
        target = {
            "transaction.preview": "ManagedBackend._invoke_g2_control",
            "transaction.trial": "ManagedBackend._run_trial",
            "transaction.apply": "ManagedBackend._transaction_via_service",
            "transaction.verify": "ManagedBackend._verify_transaction_record",
            "transaction.recover": "ManagedBackend._restore_checkpoint",
        }.get(operation_id, "ManagedBackend._invoke_g2_model")
        return "managed_g2", ["ManagedBackend._invoke_g2_model", target]
    if operation_id == "checkpoint.create":
        return "managed_g2", ["ManagedBackend._invoke_g2_model", "ManagedBackend._checkpoint_via_service"]
    if operation_id == "checkpoint.restore":
        return "managed_g2", ["ManagedBackend._invoke_g2_model", "ManagedBackend._restore_checkpoint"]
    if operation_id in {"checkpoint.list", "checkpoint.inspect"}:
        return "managed_g2", ["ManagedBackend._invoke_g2_control"]
    if operation_id == "code.execute_java":
        return "controlled_java", ["ManagedBackend._invoke_g2_model", "ManagedBackend._run_g2_action", "PersistentJavaWorker.execute_java"]
    if operation_id == "code.compile_java":
        return "controlled_java", ["ManagedBackend._invoke_g2_control", "PersistentJavaWorker.compile_java"]
    if operation_id == "code.describe_java":
        return "controlled_java", ["ManagedBackend._invoke_g2_control", "describe_source"]
    if operation_id == "code.inspect_run":
        return "control_plane", ["ManagedBackend._invoke_g2_control", "OperationStore.job", "OperationStore.events"]
    if operation_id.startswith("docs."):
        return "control_plane", ["ManagedBackend._invoke_g2_control", "versioned documentation index"]
    if operation_id in {"checkpoint.create", "checkpoint.restore"}:
        return "managed_g2", ["ManagedBackend._invoke_g2_model"]
    return "managed_g2", ["ManagedBackend.invoke", "ManagedBackend._invoke_g2_model"]


def resolve_symbol_location(symbol: str) -> dict[str, Any] | None:
    paths = {
        "ManagedBackend": Path("comsol_mcp/_managed_backend.py"),
        "ControlDaemon": Path("comsol_mcp/_control_daemon.py"),
        "_g2_engine": Path("comsol_mcp/_g2_engine.py"),
        "_g2_public_api": Path("comsol_mcp/_g2_public_api.py"),
        "_g2_checkpoint_ops": Path("comsol_mcp/_g2_checkpoint_ops.py"),
        "_g2_checkpoint_diff": Path("comsol_mcp/_g2_checkpoint_diff.py"),
        "_g2_model_compare": Path("comsol_mcp/_g2_model_compare.py"),
        "_g3_ops": Path("comsol_mcp/_g3_ops.py"),
        "PersistentJavaWorker": Path("comsol_mcp/_java_worker.py"),
        "OperationStore": Path("comsol_mcp/_operation_store.py"),
        "_g2_registry": Path("comsol_mcp/_g2_registry.py"),
        "_g2_tools": Path("comsol_mcp/_g2_tools.py"),
        "DesktopCoordinator": Path("comsol_mcp/_desktop_service.py"),
        "MacOSDesktopAdapter": Path("comsol_mcp/_desktop_macos.py"),
        "WindowsDesktopAdapter": Path("comsol_mcp/_desktop_windows.py"),
        "_desktop_platforms": Path("comsol_mcp/_desktop_platforms.py"),
        "_desktop_selector_profile": Path("comsol_mcp/_desktop_selector_profile.py"),
    }
    if symbol.startswith("ManagedBackend."):
        path = paths["ManagedBackend"]
        return source_location(path, symbol)
    if symbol.startswith("ControlDaemon."):
        path = paths["ControlDaemon"]
        return source_location(path, symbol)
    if symbol.startswith("OperationStore."):
        return source_location(paths["OperationStore"], symbol.removeprefix("OperationStore."))
    for owner in ("DesktopCoordinator", "MacOSDesktopAdapter", "WindowsDesktopAdapter"):
        if symbol.startswith(owner + "."):
            return source_location(paths[owner], symbol)
    for module in ("_desktop_platforms", "_desktop_selector_profile"):
        if symbol.startswith(module + "."):
            return source_location(paths[module], symbol.removeprefix(module + "."))
    if symbol.startswith("_g2_tools."):
        return source_location(paths["_g2_tools"], symbol.removeprefix("_g2_tools."))
    if symbol.startswith("_g2_engine."):
        return source_location(paths["_g2_engine"], symbol.removeprefix("_g2_engine."))
    if symbol.startswith("_g2_public_api."):
        return source_location(paths["_g2_public_api"], symbol.removeprefix("_g2_public_api."))
    if symbol.startswith("_g2_checkpoint_ops."):
        return source_location(paths["_g2_checkpoint_ops"], symbol.removeprefix("_g2_checkpoint_ops."))
    if symbol.startswith("_g2_checkpoint_diff."):
        return source_location(paths["_g2_checkpoint_diff"], symbol.removeprefix("_g2_checkpoint_diff."))
    if symbol.startswith("_g2_model_compare."):
        return source_location(paths["_g2_model_compare"], symbol.removeprefix("_g2_model_compare."))
    if symbol.startswith("_g3_ops."):
        return source_location(paths["_g3_ops"], symbol.removeprefix("_g3_ops."))
    if symbol.startswith("_java_worker."):
        return source_location(Path("comsol_mcp/_java_worker.py"), symbol.removeprefix("_java_worker."))
    if symbol.startswith("PersistentJavaWorker."):
        return source_location(paths["PersistentJavaWorker"], symbol)
    if symbol.startswith("_g2_registry."):
        path = paths["_g2_registry"]
        try:
            return source_location(path, symbol.removeprefix("_g2_registry."))
        except (ValueError, SyntaxError):
            return None
    if symbol == "versioned documentation index":
        return source_location(paths["ManagedBackend"], "ManagedBackend._invoke_g2_control")
    if symbol == "_g3_ops.DISPATCH":
        return source_location(paths["_g3_ops"], "DISPATCH")
    if symbol.startswith("_g2_public_api."):
        return source_location(paths["_g2_public_api"], symbol.removeprefix("_g2_public_api."))
    if symbol == "_g2_checkpoint_ops.invoke_owned":
        return source_location(paths["_g2_checkpoint_ops"], "invoke_owned")
    if symbol == "_g2_checkpoint_diff.invoke_diff":
        return source_location(paths["_g2_checkpoint_diff"], "invoke_diff")
    if symbol == "_g2_model_compare.invoke_compare":
        return source_location(paths["_g2_model_compare"], "invoke_compare")
    if symbol == "describe_source":
        return source_location(Path("comsol_mcp/_g2_code.py"), "describe_source")
    return None


def desktop_route_evidence(operation_id: str) -> dict[str, Any]:
    methods = DESKTOP_ADAPTER_METHODS[operation_id]["adapter"]
    macos_methods = [
        source_location(Path("comsol_mcp/_desktop_macos.py"), f"MacOSDesktopAdapter.{method}")
        for method in methods
    ]
    windows_methods = [
        source_location(Path("comsol_mcp/_desktop_windows.py"), f"WindowsDesktopAdapter.{method}")
        for method in methods
    ]
    macos_limitations: list[str] = []
    windows_limitations: list[str] = []
    if operation_id == "desktop.capture":
        macos_limitations.extend([
            "macOS 14+ ScreenCaptureKit API gate",
            "Screen Recording permission is checked for the exact target window",
        ])
        windows_limitations.append("capture is restricted to the exact leased target HWND and window region")
    if operation_id == "desktop.shell_execute":
        macos_limitations.append("COMSOL Java Shell is explicitly unsupported on macOS")
        windows_limitations.append("requires the complete host-observed Java Shell UIA selector set")
    if operation_id in {"desktop.bind", "desktop.show_model", "desktop.select_node", "desktop.action", "desktop.migrate_standalone"}:
        macos_limitations.append("requires a protected host-observed selector profile matching exact window/process identity and macOS build")
        windows_limitations.append("requires a protected host-observed selector profile matching exact executable ProductVersion, OS build, and window identity")
    if operation_id == "desktop.migrate_standalone":
        macos_limitations.append("requires observed standalone identity/dirty state and Save Copy/completion selectors")
        windows_limitations.append("requires observed standalone identity/dirty state and Save Copy/completion selectors")
    return {
        "darwin": {
            "factory_target": "MacOSDesktopAdapter",
            "factory_location": source_location(Path("comsol_mcp/_desktop_platforms.py"), "create_native_metadata_adapter"),
            "adapter_class_location": source_location(Path("comsol_mcp/_desktop_macos.py"), "MacOSDesktopAdapter"),
            "method_locations": macos_methods,
            "availability": (
                "EXPLICITLY_UNSUPPORTED_PLATFORM" if operation_id == "desktop.shell_execute"
                else "STATIC_ADAPTER_METHOD_PRESENT_NATIVE_UNVERIFIED"
            ),
            "limitations": macos_limitations,
            "profile_loader_location": source_location(Path("comsol_mcp/_desktop_selector_profile.py"), "load_selector_profile"),
            "native_evidence": "NOT_RUN",
        },
        "win32": {
            "factory_target": "WindowsDesktopAdapter",
            "factory_location": source_location(Path("comsol_mcp/_desktop_platforms.py"), "create_native_metadata_adapter"),
            "adapter_class_location": source_location(Path("comsol_mcp/_desktop_windows.py"), "WindowsDesktopAdapter"),
            "method_locations": windows_methods,
            "availability": "STATIC_ADAPTER_METHOD_PRESENT_NATIVE_UNVERIFIED",
            "limitations": windows_limitations,
            "profile_loader_location": source_location(Path("comsol_mcp/_desktop_selector_profile.py"), "load_selector_profile"),
            "native_evidence": "NOT_RUN",
        },
        "coordinator_method": DESKTOP_ADAPTER_METHODS[operation_id]["coordinator"],
        "implementation_side_effects": {
            "desktop.bind": "reads current UI model identity; may run and restore a managed label nonce challenge",
            "desktop.show_model": "invokes a profiled UI control and verifies the displayed model binding",
            "desktop.select_node": "selects the NodePath mapped by an observed selector profile and verifies the selected path",
            "desktop.capture": "captures the exact target window and publishes verified bytes to the project ArtifactStore",
            "desktop.action": "invokes one profiled UI action after precondition checks and verifies its observed postcondition",
            "desktop.shell_execute": "executes trusted immutable source through Windows Java Shell; macOS explicitly rejects this workflow",
            "desktop.migrate_standalone": "saves an MPH to private staging, verifies completion/source preservation, publishes no-clobber, and loads the copy into the target session",
        }[operation_id],
        "version_constraints": {
            "darwin": "selector profile binds the observed COMSOL version string, host/window/process identity, and macOS build; app-bundle reporting does not certify COMSOL 6.3/6.4 native acceptance",
            "win32": "adapter profile binds exact COMSOL executable ProductVersion, Windows build, and window identity",
            "verified_comsol_versions": [],
            "source_locations": [
                source_location(Path("comsol_mcp/_desktop_platforms.py"), "versioned_native_profile"),
                source_location(Path("comsol_mcp/_desktop_macos.py"), "MacOSDesktopAdapter._profile"),
                source_location(Path("comsol_mcp/_desktop_windows.py"), "WindowsDesktopAdapter._profile"),
                source_location(Path("comsol_mcp/_desktop_selector_profile.py"), "validate_profile"),
            ],
            "note": "Static policy only; no host profile, permission state, COMSOL build, or native action was observed in this mapping.",
        },
    }


def operation_route(
    item: dict[str, Any],
    g2: dict[str, dict[str, int]],
    g3: dict[str, dict[str, Any]],
    effects_fallback: dict[str, Any],
) -> dict[str, Any]:
    operation_id = item["operation_id"]
    domain = str(item.get("domain") or operation_id.split(".", 1)[0])
    desktop_only = operation_id in g2["DESKTOP_COORDINATOR_ONLY_OPERATIONS"]
    control_only = operation_id in g2["CONTROL_IMPLEMENTED_OPERATIONS"] or operation_id == "job.resume"

    if desktop_only and operation_id in DESKTOP_ADAPTER_METHODS:
        desktop_methods = DESKTOP_ADAPTER_METHODS[operation_id]
        kind = "desktop_native_adapter"
        handler_symbols = [
            "ControlDaemon.dispatch",
            "ControlDaemon._dispatch_desktop_control",
            "DesktopCoordinator.dispatch",
            f"DesktopCoordinator.{desktop_methods['coordinator']}",
        ]
        if desktop_methods["coordinator"] != "_execute_operation":
            handler_symbols.append("DesktopCoordinator._execute_operation")
        if operation_id in {"desktop.bind", "desktop.migrate_standalone"}:
            handler_symbols.append("DesktopCoordinator._observe_model")
        handler_symbols.append("_desktop_platforms.create_native_metadata_adapter")
        for owner in ("MacOSDesktopAdapter", "WindowsDesktopAdapter"):
            for method in desktop_methods["adapter"]:
                handler_symbols.append(f"{owner}.{method}")
        if operation_id == "desktop.capture":
            handler_symbols.append("MacOSDesktopAdapter.preflight_capture")
        if operation_id == "desktop.show_model":
            handler_symbols.append("MacOSDesktopAdapter.preflight_show_model")
        handler_symbols.append("_desktop_selector_profile.load_selector_profile")
        entrypoint = "ControlDaemon -> DesktopCoordinator -> create_native_metadata_adapter -> profile-gated host adapter"
        route_status = "STATIC_DESKTOP_ADAPTER_PRESENT_UNVERIFIED"
    elif desktop_only:
        kind = "coordinator_only"
        handler_symbols = ["ControlDaemon.dispatch", "ControlDaemon._dispatch_desktop_control"]
        entrypoint = "ControlDaemon desktop coordinator; no operation-specific native adapter mapping was found"
        route_status = "COORDINATOR_ONLY_NOT_EXECUTABLE"
    elif operation_id in g3:
        kind = "runtime" if domain == "runtime" else "domain"
        if operation_id == "artifact.publish":
            handler_symbols = [
                "ManagedBackend.invoke",
                "ManagedBackend._invoke_project_artifact_publish",
                f"{g3[operation_id]['module']}.{g3[operation_id]['handler']}",
            ]
        elif domain == "runtime":
            handler_symbols = [
                "ManagedBackend.invoke",
                "ManagedBackend._invoke_runtime_scoped",
                "_g3_ops.DISPATCH",
                f"{g3[operation_id]['module']}.{g3[operation_id]['handler']}",
            ]
        else:
            handler_symbols = [
                "ManagedBackend.invoke",
                "ManagedBackend._invoke_g2_model",
                "_g3_ops.DISPATCH",
                f"{g3[operation_id]['module']}.{g3[operation_id]['handler']}",
            ]
        entrypoint = "registry_call or operation_call with operation_id in arguments"
        route_status = "STATIC_DOMAIN_HANDLER_PRESENT_UNVERIFIED"
    elif control_only:
        kind = "control_plane"
        if operation_id.startswith("job."):
            handler_symbols = ["ControlDaemon.dispatch", "ControlDaemon._dispatch_catalog_job_control_impl"]
        elif operation_id.startswith("artifact."):
            handler_symbols = ["ControlDaemon.dispatch", "ControlDaemon._dispatch_artifact_control_read"]
        elif operation_id.startswith("project."):
            handler_symbols = ["ControlDaemon.dispatch", "ControlDaemon._dispatch_project_control"]
        elif operation_id.startswith("session."):
            handler_symbols = ["ControlDaemon.dispatch", "ControlDaemon._dispatch_session_control"]
        elif operation_id.startswith("experiment.stage_define"):
            handler_symbols = ["ControlDaemon.dispatch", "ControlDaemon._dispatch_experiment_stage_define"]
        elif operation_id.startswith("experiment.stage_run"):
            handler_symbols = ["ControlDaemon.dispatch", "ControlDaemon._dispatch_experiment_stage_run"]
        elif operation_id.startswith("experiment."):
            handler_symbols = ["ControlDaemon.dispatch", "ControlDaemon._dispatch_experiment_durable_read"]
        elif operation_id == "solver.log_read":
            handler_symbols = ["ControlDaemon.dispatch", "ControlDaemon._dispatch_admitted"]
        elif operation_id == "job.resume":
            handler_symbols = ["ControlDaemon.dispatch", "ControlDaemon._dispatch_catalog_job_resume"]
        else:
            handler_symbols = ["ControlDaemon.dispatch", "ControlDaemon._dispatch_admitted"]
        entrypoint = "ControlDaemon dispatch; registry fallback is routed to the durable control plane"
        route_status = "STATIC_CONTROL_HANDLER_PRESENT_UNVERIFIED"
    elif operation_id in g2["IMPLEMENTED_OPERATIONS"]:
        kind, handler_symbols = g2_handler_hint(operation_id)
        entrypoint = "registry_call or operation_call with operation_id in arguments"
        route_status = (
            "STATIC_GENERIC_API_HANDLER_PRESENT_UNVERIFIED" if kind == "generic_api"
            else "STATIC_CONTROLLED_JAVA_HANDLER_PRESENT_UNVERIFIED" if kind == "controlled_java"
            else "STATIC_MANAGED_HANDLER_PRESENT_UNVERIFIED"
        )
    else:
        kind = "none"
        handler_symbols = ["_g2_registry.validate_call"]
        entrypoint = "registry_call/operation_call validation refusal; no executable handler was found"
        route_status = "NO_EXECUTABLE_OPERATION_HANDLER"

    locations: list[dict[str, Any]] = []
    if operation_id in g2["IMPLEMENTED_OPERATIONS"]:
        locations.append({
            "path": "comsol_mcp/_g2_registry.py",
            "symbol": "IMPLEMENTED_OPERATIONS",
            "line": g2["IMPLEMENTED_OPERATIONS"][operation_id],
            "role": "allowlist_membership_only",
        })
    if operation_id in g2["CONTROL_IMPLEMENTED_OPERATIONS"]:
        locations.append({
            "path": "comsol_mcp/_g2_registry.py",
            "symbol": "CONTROL_IMPLEMENTED_OPERATIONS",
            "line": g2["CONTROL_IMPLEMENTED_OPERATIONS"][operation_id],
            "role": "control_route_membership",
        })
    if desktop_only:
        locations.append({
            "path": "comsol_mcp/_g2_registry.py",
            "symbol": "DESKTOP_COORDINATOR_ONLY_OPERATIONS",
            "line": g2["DESKTOP_COORDINATOR_ONLY_OPERATIONS"][operation_id],
            "role": "coordinator_only_membership",
        })
    if operation_id in g3:
        module = g3[operation_id]
        locations.append({
            "path": module["path"],
            "symbol": module["static_operation_table"],
            "line": module["line"],
            "handler": module["handler"],
            "handler_line": module["handler_line"],
            "role": "static_operation_to_handler_mapping",
        })
    if operation_id == "api.describe":
        locations.extend([
            source_location(Path("comsol_mcp/_g2_public_api.py"), "CAPABILITIES"),
            source_location(Path("comsol_mcp/_g2_public_api.py"), "describe_api"),
        ])
    elif operation_id == "api.invoke":
        locations.extend([
            source_location(Path("comsol_mcp/_g2_public_api.py"), "CAPABILITIES"),
            source_location(Path("comsol_mcp/_g2_public_api.py"), "prepare_invoke"),
            source_location(Path("comsol_mcp/_g2_public_api.py"), "execute_prepared"),
        ])
    elif operation_id == "api.probe":
        locations.append(source_location(Path("comsol_mcp/_g2_public_api.py"), "CAPABILITIES"))
    chain_locations = []
    for symbol in handler_symbols:
        location = resolve_symbol_location(symbol)
        if location:
            chain_locations.append({"role": "dispatch_or_handler", **location})
    if operation_id in g2["DESKTOP_COORDINATOR_ONLY_OPERATIONS"]:
        action_hits = source_occurrences(operation_id)
    elif operation_id not in g3 and operation_id not in g2["CONTROL_IMPLEMENTED_OPERATIONS"]:
        action_hits = source_occurrences(operation_id)
    else:
        action_hits = []

    if operation_id in g3:
        effective_effect = item.get("effect")
        effect_source = "catalog_effect_used_by_G3_effect_resolver"
        if operation_id == "study.sweep_manage":
            effective_effect, effect_source = "COMPUTE", "explicit_w21_native_execution_override"
        elif operation_id == "validate.solution":
            effective_effect, effect_source = "READ", "registered_observation_read_only_validation_override"
        elif not isinstance(item.get("effect"), str) or not item.get("effect"):
            if operation_id in effects_fallback:
                effective_effect, effect_source = effects_fallback[operation_id], "explicit_fallback_effect_table"
            else:
                effective_effect, effect_source = "WRITE", "fail_closed_default"
        elif isinstance(item.get("effect"), str) and item.get("effect"):
            effective_effect = item["effect"]
            effect_source = "catalog_effect_used_by_G3_effect_resolver"
    elif operation_id in g2["IMPLEMENTED_OPERATIONS"] or operation_id in g2["CONTROL_IMPLEMENTED_OPERATIONS"]:
        effective_effect = item.get("effect")
        effect_source = "catalog_effect_plus_managed_execution_contract"
    else:
        effective_effect, effect_source = None, "not_effectively_dispatched"

    route_record = {
        "primary_route": kind,
        "entrypoint": entrypoint,
        "handler_chain": handler_symbols,
        "route_status": route_status,
        "handler_locations": locations + chain_locations,
        "entrypoint_sources": [
            source_location(Path("comsol_mcp/_g2_tools.py"), "registry_call"),
            source_location(Path("comsol_mcp/_g2_tools.py"), "operation_call"),
            source_location(Path("comsol_mcp/_g2_tools.py"), "register"),
        ],
        "supporting_operation_mentions": action_hits,
        "catalog_mcp_tool_name": item.get("mcp_tool_name"),
        "catalog_name_is_not_implementation_proof": True,
        "implementation_membership": (
            "G3_OPERATION_TABLE" if operation_id in g3 else
            "DESKTOP_COORDINATOR_ADMISSION_WITH_NATIVE_ADAPTER" if desktop_only and operation_id in DESKTOP_ADAPTER_METHODS else
            "CONTROL_COORDINATOR_ONLY" if desktop_only else
            "CONTROL_IMPLEMENTED" if operation_id in g2["CONTROL_IMPLEMENTED_OPERATIONS"] else
            "G2_IMPLEMENTED_ALLOWLIST" if operation_id in g2["IMPLEMENTED_OPERATIONS"] else
            "NO_STATIC_IMPLEMENTATION_MEMBERSHIP"
        ),
        "side_effect": {
            "catalog_effect": item.get("effect"),
            "effective_policy_effect": effective_effect,
            "policy_source": effect_source,
            "catalog_notes": item.get("notes"),
            "interpretation": (
                "A declared effect is a permission/serialization contract, not proof that the native action ran."
            ),
        },
    }
    if operation_id in DESKTOP_ADAPTER_METHODS:
        route_record["platform_adapter_routes"] = desktop_route_evidence(operation_id)
    return route_record


def test_case_sections(markdown: str) -> list[dict[str, Any]]:
    heading = re.compile(r"^## (T\d{3})\s+—\s+(.+)$", re.MULTILINE)
    matches = list(heading.finditer(markdown))
    cases = []
    for i, match in enumerate(matches):
        block = markdown[match.end():matches[i + 1].start() if i + 1 < len(matches) else len(markdown)]
        raw_sections: dict[str, str] = {}
        for name, key in (("执行", "execution"), ("通过标准", "acceptance"), ("必须保留", "evidence")):
            section = re.search(rf"\*\*{name}：\*\*\s*(.+)", block)
            raw_sections[key] = section.group(1).strip() if section else ""
        stage = re.search(r"阶段：([^；。\n]+)", block)
        scope = re.search(r"范围：([^；。\n]+)", block)
        state = re.search(r"状态：([^；。\n]+)", block)
        cases.append({
            "id": match.group(1),
            "title": match.group(2).strip(),
            "stage": stage.group(1).strip() if stage else None,
            "original_scope": scope.group(1).strip() if scope else None,
            "original_state": state.group(1).strip() if state else None,
            **raw_sections,
        })
    return cases


def split_assertions(text: str, test_id: str, section: str) -> list[str]:
    value = text.strip()
    if test_id == "T046" and section == "EXECUTION":
        value = value.replace("，归一化输入，", "；归一化输入；")
        value = value.replace("导出复场并计算模式耦合与偏移扫描", "导出复场；计算模式耦合与偏移扫描")
    if test_id == "T046" and section == "PASS":
        value = value.replace("，误差门槛事先固定", "；误差门槛事先固定")
    if test_id == "T047" and section == "EXECUTION":
        value = value.replace("冷却小模型并传递状态", "冷却小模型；传递状态")
    parts = [part.strip() for part in re.split(r"[；;→。]+", value) if part.strip()]
    if test_id == "T045" and section == "EXECUTION":
        parts = [expanded for part in parts for expanded in (
            ["L扫描", "功率优化"] if part == "L扫描/功率优化" else [part]
        )]
    return parts or ([value] if value else [])


def classify_assertion(test_id: str, section: str, text: str) -> tuple[str, str, list[str]]:
    links: list[str] = []
    reason = "该子断言属于已冻结通用核心要求；没有与当前固定源及该断言精确绑定的可靠继承验收证据。"
    if test_id == "T016":
        return CORE_DEFAULT, "导入数据类型、角向样本与插值文件hash/外推/单位来源的保真属于通用输入和数值安全；具体光斑物理结论仍为案例专项。", ["T008", "T013", "T014", "T015", "T021", "T034", "T035", "T040", "T049"]
    if test_id == "T045":
        if section == "EXECUTION":
            if text == "小规模参数化环阵列":
                return CASE_SPECIAL, "此处的环阵列几何是可选VCSEL配方；通用参数化几何能力仍由既有核心测试覆盖。", ["T009", "T018", "T019"]
            if text == "材料/散热":
                return CASE_SPECIAL, "此处具体材料/散热配置属于VCSEL物理配方；通用材料与物理接口仍由既有核心测试覆盖。", ["T017", "T019"]
            if text == "非轴对称插值热流":
                return CORE_DEFAULT, "插值输入类型、文件身份、单位与数值完整性属于核心；该热流的具体物理分布属于案例专项。", ["T008", "T015", "T016", "T034", "T035", "T049"]
            if text in {"求解", "L扫描", "功率优化", "图/数据"}:
                return CORE_DEFAULT, "真实计算、参数扫描/优化入口及结果/文件身份属于通用核心；案例物理目标值仍属专项。", ["T019", "T021", "T040", "T049", "T059", "T060"]
            return CASE_SPECIAL, "此步骤属于VCSEL专属物理工作流；可复用建模和计算能力仍由核心测试覆盖。", ["T017", "T019", "T021"]
        if "角向信息" in text:
            return CORE_DEFAULT, "保留原始空间数据属于通用数值保真要求；VCSEL物理评价本身可选。", ["T008", "T014", "T016"]
        if "功率预算" in text or "ROI" in text:
            return CASE_SPECIAL, "具体功率预算与ROI判据属于VCSEL科学专项；输入、输出类型和来源身份仍为required。", ["T014", "T021", "T040", "T049"]
        if "不可行" in text:
            return CORE_DEFAULT, "目标不可行时真实报告属于通用失败语义；目标函数与科学判据仍是案例专项。", ["T022", "T026", "T059"]
    if test_id == "T046":
        if section == "EVIDENCE":
            return CORE_DEFAULT, "端口/网格配置、归一化和扫描表作为数值输入及来源身份记录必须保留；相应物理阈值仍属于案例专项。", ["T008", "T014", "T021", "T040", "T049", "T060"]
        if text == "eta_mode与eta_capture分开":
            return CORE_DEFAULT, "不同数值指标必须保持各自名称、定义和输出身份，不能合并或互相冒充。", ["T014", "T021", "T040", "T049"]
        if text == "误差门槛事先固定":
            return CORE_DEFAULT, "预先固定数值误差阈值属于通用验收诚实性要求。", ["T021", "T036", "T059"]
        if any(word in text for word in ("归一化输入", "导出复场", "复场")):
            return CORE_DEFAULT, "复数类型、归一化输入和真实场数据的身份/数值保真属于通用核心。", ["T008", "T014", "T021", "T040", "T049"]
        if "构建小型可算" in text or "光场/端口模型" in text:
            return CASE_SPECIAL, "光纤端口模型是可选案例配方；通用模型/物理接口仍由既有核心测试覆盖。", ["T007", "T017", "T019"]
        if "模式耦合" in text or "偏移扫描" in text:
            return CASE_SPECIAL, "耦合/偏移的科学目标值属于光纤案例专项；通用扫描、输入类型和真实结果身份仍是核心。", ["T014", "T021", "T040", "T049"]
        if "真实求解证据" in text:
            return CORE_DEFAULT, "结果必须来自真实绑定的引擎作业；该光纤案例的科学收敛标准仍属专项。", ["T019", "T021", "T040", "T059"]
        return CASE_SPECIAL, "端口、耦合、偏移扫描及其光学阈值属于光纤案例科学专项。", ["T014", "T021", "T040"]
    if test_id == "T047":
        if section == "EVIDENCE":
            return CORE_DEFAULT, "各阶段文件、模型/解身份、数值表和假设来源必须按通用产物与验证契约追溯。", ["T017", "T021", "T029", "T036", "T040", "T049", "T057", "T060"]
        if section == "EXECUTION" and text != "传递状态":
            return CASE_SPECIAL, "UV、固化、冷却及胶形分支属于可选案例科学链；独立的一般状态传递与模型身份要求仍是核心。", ["T017", "T019", "T021", "T057", "T060"]
        if any(word in text for word in ("传递状态", "参考态", "不被重置", "体积误差", "胶量误差", "温度", "固化", "应力")):
            return CORE_DEFAULT, "状态/模型身份连续性和可追溯数值属于通用核心；材料本构和案例接受阈值仍属专项。", ["T017", "T021", "T029", "T040", "T057", "T060"]
        if "校准与验证分开" in text:
            return CORE_DEFAULT, "校准与独立验证不可混同属于通用验证诚实性要求。", ["T036", "T059"]
        return CASE_SPECIAL, "特定胶形分支及UV固化冷却科学链属于可选专项。", ["T017", "T019", "T021"]
    if test_id == "T058":
        if section == "EXECUTION" and text.startswith("给任务缺失"):
            return CASE_SPECIAL, "材料曲线、接触角或光场参数的具体缺项是领域案例；其输入来源、类型和估值标记安全仍属核心。", ["T017", "T036", "T059"]
        if "敏感性" in text or "assumption" in text and "source" not in text:
            return CASE_SPECIAL, "具体物理假设敏感性属于案例科学专项。", ["T017", "T021"]
        if any(word in text for word in ("估值", "estimated", "来源", "独立验证", "估值后运行")):
            return CORE_DEFAULT, "显式标注估值来源、区分拟合与独立验证属于通用数据/验证安全。", ["T017", "T036", "T059"]
        if section == "EVIDENCE":
            return CORE_DEFAULT, "假设表和来源标记属于通用数据来源追溯；专项acceptance数值仍可选。", ["T036", "T059", "T060"]
    return CORE_DEFAULT, reason, links


def stable_assertion_id(test_id: str, section: str, index: int, text: str) -> str:
    digest = sha256_bytes(f"{test_id}\0{section}\0{text}".encode("utf-8"))[:8]
    return f"{test_id}.{section}.{index:02d}-{digest}"


def target_matrix_key(target_id: str) -> str:
    if target_id.startswith("windows-x64-"):
        return target_id.replace("windows-x64-", "windows-x64/comsol")
    if target_id.startswith("macos-arm64-"):
        return target_id.replace("macos-arm64-", "macos-arm64/comsol")
    if target_id.startswith("macos-x64-"):
        return target_id.replace("macos-x64-", "macos-x86_64/comsol")
    raise ValueError(f"unknown target id: {target_id}")


def build_test_mapping(
    acceptance: str,
    matrix: dict[str, Any],
    target_config: dict[str, Any],
) -> dict[str, Any]:
    source_cases = test_case_sections(acceptance)
    matrix_cases = {row["id"]: row for row in matrix.get("tests", [])}
    target_rows = target_config.get("targets", [])
    targets_by_matrix_key = {target_matrix_key(row["id"]): row for row in target_rows}
    if len(source_cases) != 60 or len(matrix_cases) != 60:
        raise ValueError(f"expected 60 source and matrix cases, found {len(source_cases)} and {len(matrix_cases)}")
    assertions: list[dict[str, Any]] = []
    tests: list[dict[str, Any]] = []
    expected_ids = {f"T{i:03d}" for i in range(1, 61)}
    if {case["id"] for case in source_cases} != expected_ids or set(matrix_cases) != expected_ids:
        raise ValueError("original acceptance case IDs are incomplete or duplicated")
    target_summary = []
    for target in target_rows:
        key = target_matrix_key(target["id"])
        target_summary.append({
            "id": target["id"],
            "matrix_key": key,
            "native_scope": target.get("native"),
            "distribution_scope": target.get("distribution"),
            "effective_native_evidence_status": (
                target["native"] if str(target.get("native", "")).startswith("USER_REQUESTED_SKIP")
                else "NOT_RUN"
            ),
            "distribution_evidence_status": "NOT_ASSESSED_BY_STATIC_MAPPING",
        })
    for case in source_cases:
        matrix_case = matrix_cases[case["id"]]
        if case["original_scope"] != matrix_case.get("original_scope"):
            raise ValueError(f"{case['id']} scope differs between acceptance sources")
        ids: list[str] = []
        for section_name, source_key in (("EXECUTION", "execution"), ("PASS", "acceptance"), ("EVIDENCE", "evidence")):
            text = case[source_key]
            clauses = split_assertions(text, case["id"], section_name)
            if section_name == "EVIDENCE":
                # Required evidence is retained as evidence obligations; its
                # safety/provenance stays core even when the artifact is for a
                # case-specialized scientific endpoint.
                clauses = [item.strip() for item in re.split(r"[、，,]+", text) if item.strip()] or clauses
            for idx, clause in enumerate(clauses, 1):
                scope_class, rationale, linked = classify_assertion(case["id"], section_name, clause)
                assertion_id = stable_assertion_id(case["id"], section_name, idx, clause)
                ids.append(assertion_id)
                assertion = {
                    "assertion_id": assertion_id,
                    "test_id": case["id"],
                    "kind": section_name,
                    "text": clause,
                    "scope_class": scope_class,
                    "frozen_disposition": (
                        "CORE_REQUIRED_AFFECTED_RETEST" if scope_class == CORE_DEFAULT
                        else "CASE_SPECIALIZED_NOT_A_RELEASE_GATE"
                    ),
                    "verification_treatment": (
                        "AFFECTED_RETEST" if scope_class == CORE_DEFAULT else "CASE_SPECIALIZED_NOT_A_RELEASE_GATE"
                    ),
                    "evidence_status": "NOT_RUN",
                    "reliable_inheritance": "NONE_CASE_LEVEL_EVIDENCE",
                    "rationale": rationale,
                    "linked_existing_core_tests": linked,
                }
                assertions.append(assertion)
        target_states = []
        for key, prior in matrix_case.get("targets", {}).items():
            configured = targets_by_matrix_key.get(key)
            if configured is None:
                raise ValueError(f"{case['id']} has unknown target {key}")
            native = configured.get("native")
            native_status = native if str(native).startswith("USER_REQUESTED_SKIP") else "NOT_RUN"
            blocker = prior.get("blocker")
            target_states.append({
                "target": configured["id"],
                "original_matrix_status": prior.get("status"),
                "native_scope": native,
                "native_evidence_status": native_status,
                "distribution_scope": configured.get("distribution"),
                "distribution_evidence_status": "NOT_ASSESSED_BY_STATIC_MAPPING",
                "external_blocker": blocker,
                "external_blocked": bool(blocker) or prior.get("status") == "BLOCKED",
                "native_scope_disposition": (
                    native if str(native).startswith("USER_REQUESTED_SKIP") else
                    "EXTERNAL_BLOCKED" if bool(blocker) or prior.get("status") == "BLOCKED" else
                    "CORE_REQUIRED_AFFECTED_RETEST"
                ),
                "skip_applies_to": "native evidence only",
            })
        tests.append({
            "id": case["id"],
            "title": case["title"],
            "stage": case["stage"],
            "original_scope": case["original_scope"],
            "original_required": matrix_case.get("required"),
            "original_acceptance_state": case["original_state"],
            "current_case_status": "NOT_RUN",
            "execution_text": case["execution"],
            "pass_text": case["acceptance"],
            "evidence_text": case["evidence"],
            "assertion_ids": ids,
            "target_states": target_states,
            "matrix_source": f"{OLD_MATRIX.as_posix()}#/tests/{case['id']}",
        })
    status_counts = Counter(a["scope_class"] for a in assertions)
    treatment_counts = Counter(a["verification_treatment"] for a in assertions)
    target_skip = [row for row in target_summary if str(row["native_scope"]).startswith("USER_REQUESTED_SKIP")]
    external_cells = [
        {"test_id": test["id"], **state}
        for test in tests for state in test["target_states"] if state["external_blocked"]
    ]
    old_skip_targets = sorted({
        key
        for row in matrix["tests"]
        for key, state in row.get("targets", {}).items()
        if state.get("effective_scope_disposition") == "USER_REQUESTED_SKIP"
    })
    ids = [a["assertion_id"] for a in assertions]
    if len(ids) != len(set(ids)):
        raise ValueError("test subassertion IDs are not unique")
    return {
        "source_documents": {
            "acceptance_contract": ACCEPTANCE.as_posix(),
            "prior_target_matrix": OLD_MATRIX.as_posix(),
            "effective_scope_contract": SCOPE.as_posix(),
            "effective_targets": TARGETS.as_posix(),
            "release_dod": DOD.as_posix(),
        },
        "scope_rules": [
            "原60项全部保留；执行步骤、通过标准逐分句拆成稳定ID，原始整句仍保留。",
            CASE_CORE_RULE,
            "普通模型、身份、类型、文件、数值、权限、失败语义、job和交付断言保持CORE_REQUIRED。",
            "仅T016、T045-T047、T058中的案例物理终点/专属指标属于CASE_SPECIALIZED；其中通用类型、文件、模型/解身份、数值来源与诚实失败断言仍是CORE_REQUIRED，并链接到既有核心测试。",
            "RELIABLE_INHERITANCE须有与子断言和固定源精确绑定的旧验收证据。现有原60项矩阵全部NOT_RUN且证据为空，因此本次没有任何原验收子断言可继承为PASS；历史软件测试只记其自身范围。",
            "只把明确记录的环境/许可证阻塞写EXTERNAL_BLOCKED。当前60项矩阵的360个target blocker均为空，所以本映射不新造外部阻塞。",
            "USER_REQUESTED_SKIP只豁免TARGETS.json中精确列出的原生硬件格；发行/架构构建范围继续按TARGETS.json要求。ALL_NO_ENGINE子断言不因原生跳过而跳过。",
        ],
        "assertions": assertions,
        "tests": tests,
        "summary": {
            "test_case_count": len(tests),
            "subassertion_count": len(assertions),
            "subassertions_by_scope_class": dict(sorted(status_counts.items())),
            "subassertions_by_verification_treatment": dict(sorted(treatment_counts.items())),
            "reliable_inherited_acceptance_subassertions": 0,
            "external_blocked_target_cells": len(external_cells),
            "external_blocked_cells": external_cells,
            "original_matrix_skip_targets": old_skip_targets,
            "effective_native_skip_targets": target_skip,
            "target_matrix": target_summary,
            "assertion_status_counts": dict(sorted(Counter(a["evidence_status"] for a in assertions).items())),
        },
    }


def test_file_mentions(operation_ids: set[str]) -> dict[str, list[str]]:
    pattern = re.compile("|".join(re.escape(x) for x in sorted(operation_ids, key=len, reverse=True)))
    found: dict[str, set[str]] = defaultdict(set)
    for path in sorted((ROOT / "tests").glob("test_*.py")):
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        for match in pattern.finditer(content):
            found[match.group(0)].add(path.relative_to(ROOT).as_posix())
    return {key: sorted(value) for key, value in found.items()}


def junit_record(path: Path, run_id: str, run_scope: str) -> dict[str, Any]:
    root = ET.parse(ROOT / path).getroot()
    cases: list[dict[str, Any]] = []
    for case in root.iter("testcase"):
        failures = case.findall("failure")
        errors = case.findall("error")
        skipped = case.findall("skipped")
        cases.append({
            "classname": case.get("classname"),
            "name": case.get("name"),
            "status": "FAILED" if failures or errors else "SKIPPED" if skipped else "PASSED",
            "failure_or_error": [
                {
                    "kind": "failure" if node in failures else "error",
                    "message": node.get("message"),
                    "details": (node.text or "")[:1800],
                }
                for node in failures + errors
            ],
        })
    classes: dict[str, dict[str, int]] = {}
    for case in cases:
        name = str(case.get("classname"))
        counts = classes.setdefault(name, {"tests": 0, "passed": 0, "failed": 0, "skipped": 0})
        counts["tests"] += 1
        key = {"PASSED": "passed", "FAILED": "failed", "SKIPPED": "skipped"}[case["status"]]
        counts[key] += 1
    totals = {
        "tests": len(cases),
        "passed": sum(row["passed"] for row in classes.values()),
        "failed": sum(row["failed"] for row in classes.values()),
        "skipped": sum(row["skipped"] for row in classes.values()),
        "errors": sum(
            1 for case in cases
            if any(row["kind"] == "error" for row in case["failure_or_error"])
        ),
    }
    return {
        "run_id": run_id,
        "scope": run_scope,
        "source": path.as_posix(),
        "sha256": sha256_bytes((ROOT / path).read_bytes()),
        "timestamp": root.find("testsuite").get("timestamp") if root.find("testsuite") is not None else None,
        "host": root.find("testsuite").get("hostname") if root.find("testsuite") is not None else None,
        "counts": totals,
        "class_counts": classes,
        "testcases": cases,
    }


def software_test_evidence() -> dict[str, Any]:
    initial = read_json(INITIAL_UNIT_RUN)
    initial_output = str(initial.get("output", ""))
    initial_failed = re.search(r"(\d+) failed", initial_output)
    initial_passed = re.search(r"(\d+) passed", initial_output)
    if not initial_failed or not initial_passed:
        raise ValueError("initial native-default regression counts are not present in evidence")
    pre_fix_counts = {
        "failed": int(initial_failed.group(1)),
        "passed": int(initial_passed.group(1)),
        "tests": int(initial_failed.group(1)) + int(initial_passed.group(1)),
        "skipped": 0,
        "exit_code": initial.get("exit_code"),
    }
    run = junit_record(
        POSTFIX_UNIT_JUNIT,
        "UNIT-NATIVE-DEFAULT-POSTFIX",
        "tests/test_interp_native_default_units.py plus tests/test_g3_results.py; software regression only",
    )
    if run["counts"] != {"tests": 257, "passed": 256, "failed": 1, "skipped": 0, "errors": 0}:
        raise ValueError(f"post-fix unit test evidence counts differ from its junit report: {run['counts']}")
    captured_post = read_json(POSTFIX_UNIT_RUN)
    failure = next((case for case in run["testcases"] if case["status"] == "FAILED"), None)
    if failure is None or failure["name"] != "test_actual014_raw_reproduces_original_r5_exact_label_rejection":
        raise ValueError("post-fix unit evidence failure identity does not match the recorded historical-fixture failure")
    failure_text = "\n".join(item["details"] for item in failure["failure_or_error"])
    old_path_match = re.search(r"No such file or directory: '([^']+)'", failure_text)
    old_path = old_path_match.group(1) if old_path_match else None
    test_source = source_text(Path("tests/test_g3_results.py"))
    old_hash_match = re.search(
        r"hashlib\.sha256\(old\.read_bytes\(\)\)\.hexdigest\(\)\s*==\s*['\"]([0-9a-f]{64})['\"]",
        test_source,
    )
    initial_record = {
        "run_id": "UNIT-NATIVE-DEFAULT-PRE-FIX",
        "scope": initial.get("scope"),
        "source": INITIAL_UNIT_RUN.as_posix(),
        "sha256": sha256_bytes((ROOT / INITIAL_UNIT_RUN).read_bytes()),
        "command": initial.get("command"),
        "counts": pre_fix_counts,
        "interpretation": "预修复软件回归：native-default表达式通用化与空白unit语义尚未满足新增测试；不是COMSOL运行证据。",
    }
    run["source_evidence_json"] = POSTFIX_UNIT_RUN.as_posix()
    run["source_evidence_json_sha256"] = sha256_bytes((ROOT / POSTFIX_UNIT_RUN).read_bytes())
    run["captured_exit_code"] = captured_post.get("exit_code")
    run["failure_disposition"] = {
        "test_id": failure["name"],
        "status": "FAILED_FIXTURE_MISSING",
        "failure_was_converted_to_skip": False,
        "legacy_fixture_path": old_path,
        "legacy_fixture_exists_in_current_workspace": Path(old_path).exists() if old_path else None,
        "legacy_fixture_sha256_expected_by_test": old_hash_match.group(1) if old_hash_match else None,
        "reason": "测试引用旧R5源码的绝对路径并在读文件阶段遇到FileNotFoundError，未到达对旧label拒绝行为的比较；保留FAIL，不能解释成产品PASS或USER_REQUESTED_SKIP。",
    }
    adjacent = [
        {"classname": case["classname"], "name": case["name"], "status": case["status"]}
        for case in run["testcases"]
        if case["classname"] == "tests.test_interp_native_default_units"
    ]
    run["passing_corrected_behavior_tests"] = [
        case for case in adjacent if case["status"] == "PASSED"
    ]
    broader = junit_record(
        ADJACENT_UNIT_JUNIT,
        "UNIT-AFFECTED-ADJACENT-SUITE",
        "separate broader software-only affected suite; not merged with the two-file result above",
    )
    broader_failures = [case for case in broader["testcases"] if case["status"] == "FAILED"]
    broader["failure_disposition"] = [
        {
            "test_id": case["name"],
            "status": "FAILED_MISSING_TEST_INPUT",
            "failure_was_converted_to_skip": False,
            "details": case["failure_or_error"],
            "interpretation": "独立受影响套件中的测试输入/临时源清单缺失；不得与native-default两文件套件合并，也不构成COMSOL原生阻塞。",
        }
        for case in broader_failures
    ]
    return {
        "evidence_scope": "SOFTWARE_UNIT_TESTS_ONLY; no COMSOL process or native simulation was run",
        "pre_fix_run": initial_record,
        "post_fix_two_file_run": run,
        "separate_broader_affected_run": broader,
        "corrected_behavior_limit": "相邻software tests支持native-default统一表达式路径与空白unit状态修正的局部行为；不证明COMSOL原生集成、数值/物理结果或版本兼容验收。",
        "related_existing_acceptance_tests": ["T008", "T014", "T015", "T021", "T040", "T059"],
    }


def previous_action_record(operation_id: str, old_rows: dict[str, dict[str, Any]]) -> dict[str, Any]:
    row = old_rows.get(operation_id)
    if not row:
        return {
            "source": OLD_ACTIONS.as_posix(),
            "record_present": False,
            "status": "NO_PRIOR_ACTION_ROW",
            "fresh_for_current_mapping": False,
        }
    return {
        "source": OLD_ACTIONS.as_posix(),
        "record_present": True,
        "prior_status": row.get("implementation_status"),
        "prior_production_route": row.get("production_route"),
        "prior_source_paths": row.get("source_paths", []),
        "prior_evidence_refs": row.get("evidence", []),
        "prior_coverage_note": row.get("coverage_note"),
        "fresh_for_current_mapping": False,
        "interpretation": "历史台账指针；不代表本轮源码执行、当前COMSOL状态或当前原生验收。",
    }


def version_constraints(item: dict[str, Any], route: dict[str, Any]) -> dict[str, Any]:
    if route["primary_route"] == "desktop_native_adapter":
        return {
            "catalog_requirement": item.get("capability_requirements"),
            **route["platform_adapter_routes"]["version_constraints"],
        }
    if route["primary_route"] == "generic_api":
        operation_id = item["operation_id"]
        if operation_id.startswith("node."):
            return {
                "catalog_requirement": item.get("capability_requirements"),
                "source_constraint": "Typed node operations use the managed Worker/public typed getter-setter route; runtime descriptors and actual build decide support. This is distinct from the narrow api.invoke Capability table.",
                "source_locations": [
                    source_location(Path("comsol_mcp/_g2_engine.py"), "prepare_node_action"),
                    source_location(Path("comsol_mcp/_g2_engine.py"), "execute_node_action"),
                ],
                "verified_versions": [],
                "note": "No COMSOL runtime was queried in this mapping.",
            }
        api_version = static_assignment(Path("comsol_mcp/_g2_public_api.py"), "VERSION")
        return {
            "catalog_requirement": item.get("capability_requirements"),
            "source_constraint": API_VERSION_POLICY,
            "compatibility_version_constant": api_version,
            "source_supported_releases": sorted(static_assignment(Path("comsol_mcp/_g2_public_api.py"), "SUPPORTED_RELEASES") or []),
            "source_locations": [
                source_location(Path("comsol_mcp/_g2_public_api.py"), "VERSION"),
                source_location(Path("comsol_mcp/_g2_public_api.py"), "CAPABILITIES"),
            ],
            "api_capability_source_snapshot": {
                "path": "comsol_mcp/_g2_public_api.py",
                "sha256": sha256_bytes((ROOT / Path("comsol_mcp/_g2_public_api.py")).read_bytes()),
                "refresh_scope": ["api.describe", "api.invoke", "api.probe", "GAP-API-SURFACE"],
            },
            "verified_versions": [],
            "note": "No COMSOL runtime was queried in this mapping.",
        }
    if route["primary_route"] in {"control_plane", "coordinator_only"}:
        return {
            "catalog_requirement": item.get("capability_requirements"),
            "source_constraint": "Control-plane path is not evidence of COMSOL build compatibility.",
            "verified_versions": [],
        }
    return {
        "catalog_requirement": item.get("capability_requirements"),
        "source_constraint": "No operation-specific multi-build acceptance is inferred from a source handler; use actual runtime capability/version checks per the frozen contract.",
        "source_version_literals": sorted(set(re.findall(
            r"\b6\.[34](?:\.\d+){0,2}\b",
            source_text(Path(route["handler_locations"][0]["path"]))
        ))) if route.get("handler_locations") else [],
        "verified_versions": [],
    }


def action_uncovered_reason(item: dict[str, Any], route: dict[str, Any], operation_id: str) -> list[str]:
    if route["primary_route"] == "desktop_native_adapter":
        reasons = [
            "当前源码已存在ControlDaemon→DesktopCoordinator→Darwin/Windows原生adapter的可执行调用路径；本轮缺少宿主观察到的selector profile、对应OS权限状态及实际native evidence，因此保持UNVERIFIED。",
        ]
        if operation_id == "desktop.shell_execute":
            reasons.append("macOS adapter明确拒绝COMSOL Java Shell；该平台限制不表示Windows受控UIA handler缺失。")
        return reasons
    if route["primary_route"] == "coordinator_only":
        return [
            "静态源码显示该Desktop操作只有协调器准入/拒绝路径，未发现可执行的host/native adapter；不得因schema或注册名计为实现。",
        ]
    if route["primary_route"] == "none":
        return [
            "在当前G2/G3静态分发集合中未发现该逻辑动作的可执行handler；catalog定义不构成实现。",
            "通用code.execute_java是独立项目级代码原语，不自动成为此operation_id的handler或覆盖证据。",
        ]
    if route["primary_route"] == "generic_api":
        return [
            "通用API路径受审能力表很窄；只有精确接收器/方法/签名/版本组合可被调用，不能推广为目录内其他COMSOL API均已覆盖。",
            "当前源码路径和历史软件证据不替代本轮真实COMSOL验收。",
        ]
    if route["primary_route"] == "controlled_java":
        return [
            "受控Java源码/编译/执行路径存在，但需要项目级源代码和trusted_code权限；它不是OS沙箱，也不能证明任意目录动作、未知API或物理结果通过。",
            "本轮没有在COMSOL中编译/执行该动作。",
        ]
    return [
        "静态源码只确认handler可达；本轮没有执行MCP handler或COMSOL，不能据此判定该操作、版本、许可证、数值或物理验收通过。",
        "目录required_tests和历史台账均需按其原始证据范围解释；未运行内容保留NOT_RUN/UNVERIFIED。",
    ]


def verify_pinned_inputs(paths: set[Path], receipt: dict[str, Any]) -> list[dict[str, Any]]:
    receipt_files = {
        row["path"]: row
        for row in receipt.get("files", [])
        if isinstance(row, dict) and isinstance(row.get("path"), str)
    }
    verified: list[dict[str, Any]] = []
    for path in sorted(paths, key=lambda x: x.as_posix()):
        file_path = ROOT / path
        actual = sha256_bytes(file_path.read_bytes())
        record = receipt_files.get(path.as_posix())
        if record is None:
            verified.append({
                "path": path.as_posix(),
                "sha256": actual,
                "pin_receipt_match": False,
                "note": "workpack overlay or untracked mapping input; hash records the file actually read",
            })
            continue
        expected = record.get("sha256")
        verified.append({
            "path": path.as_posix(),
            "sha256": actual,
            "pin_receipt_match": expected == actual,
            "receipt_sha256": expected,
            "git_blob": record.get("blob"),
            "source_state": (
                "RESTORED_PIN_MATCH" if expected == actual else
                "CURRENT_WORKTREE_DIFFERS_FROM_RESTORE_RECEIPT"
            ),
            "interpretation": (
                "current bytes match the restored source receipt" if expected == actual else
                "current bytes are recorded as the local candidate; the fixed PIN and restore receipt remain unchanged"
            ),
        })
    return verified


def build_mapping() -> dict[str, Any]:
    catalog = read_json(CATALOG)
    pin = read_json(PIN)
    receipt = read_json(RESTORE)
    targets = read_json(TARGETS)
    matrix = read_json(OLD_MATRIX)
    old_action_rows = read_json(OLD_ACTIONS).get("actions", [])
    old_actions = {row["operation_id"]: row for row in old_action_rows if isinstance(row, dict) and row.get("operation_id")}
    if receipt.get("commit") != pin.get("commit") or receipt.get("tree") != pin.get("tree"):
        raise ValueError("pinned source receipt does not match PIN.json")
    if receipt.get("status") != "PINNED_SOURCE_RESTORED":
        raise ValueError("pinned source receipt status is not PINNED_SOURCE_RESTORED")
    operations = catalog.get("operations")
    if not isinstance(operations, list):
        raise ValueError("catalog.operations is not an array")
    operation_ids = [item.get("operation_id") for item in operations if isinstance(item, dict)]
    if len(operation_ids) != len(operations) or len(operation_ids) != len(set(operation_ids)):
        raise ValueError("catalog operation IDs are missing or duplicated")
    declared_count = catalog.get("operation_count")
    if declared_count != len(operations):
        raise ValueError(f"catalog operation_count mismatch: {declared_count} != {len(operations)}")

    g3, unresolved_g3_modules, fallback_effects = parse_g3_operations()
    g2, _set_lines = parse_registry_sets()
    test_mapping = build_test_mapping(source_text(ACCEPTANCE), matrix, targets)
    mentions = test_file_mentions(set(operation_ids))
    software_evidence = software_test_evidence()

    rows: list[dict[str, Any]] = []
    for item in operations:
        operation_id = item["operation_id"]
        route = operation_route(item, g2, g3, fallback_effects)
        route["version_constraints"] = version_constraints(item, route)
        route["status"] = "NOT_RUN" if route["primary_route"] in {"none", "coordinator_only"} else "UNVERIFIED"
        row = {
            "operation_id": operation_id,
            "catalog": {
                "mcp_tool_name": item.get("mcp_tool_name"),
                "domain": item.get("domain"),
                "purpose": item.get("purpose"),
                "effect": item.get("effect"),
                "scope": item.get("scope"),
                "gate": item.get("gate"),
                "implementation_status_claim": item.get("implementation_status"),
                "route_claim": item.get("route"),
                "required_tests": item.get("required_tests", []),
            },
            "input": operation_input_summary(item),
            "model_binding": binding_summary(item, operation_id),
            "actual_route": route,
            "evidence": {
                "current_static_sources": route["handler_locations"],
                "candidate_unit_test_files": mentions.get(operation_id, []),
                "candidate_test_file_note": "按operation_id字面出现检索；只说明可检查的文件位置，不证明断言通过或本轮执行。",
                "historical_action_mapping": previous_action_record(operation_id, old_actions),
                "current_execution": {
                    "status": route["status"],
                    "comsol_contacted": False,
                    "reason": "本轮授权限定为静态源码映射，明确不运行COMSOL。",
                },
            },
            "uncovered_reasons": action_uncovered_reason(item, route, operation_id),
        }
        rows.append(row)

    route_counts = Counter(row["actual_route"]["primary_route"] for row in rows)
    status_counts = Counter(row["actual_route"]["status"] for row in rows)
    old_ids = set(old_actions)
    new_ids = set(operation_ids)
    result_module_actions = {
        row["operation_id"] for row in rows
        if any(location.get("path") == "comsol_mcp/_g3_results.py"
               for location in row["actual_route"]["handler_locations"])
    }
    for row in rows:
        if row["operation_id"] in result_module_actions:
            row["evidence"]["supplemental_software_test_runs"] = [
                "UNIT-NATIVE-DEFAULT-PRE-FIX",
                "UNIT-NATIVE-DEFAULT-POSTFIX",
            ]
            row["evidence"]["supplemental_software_test_scope"] = (
                "Suite-level fixture/software evidence for _g3_results; no per-operation native or scientific PASS is inferred."
            )

    source_paths = set(PINNED_STATIC_FILES) | OVERLAY_FILES
    for row in rows:
        for location in row["actual_route"]["handler_locations"]:
            candidate = Path(location["path"])
            if (ROOT / candidate).is_file():
                source_paths.add(candidate)
    fingerprints = verify_pinned_inputs(source_paths, receipt)

    cap_path = Path("comsol_mcp/_g2_public_api.py")
    api_capability_source_sha256 = sha256_bytes((ROOT / cap_path).read_bytes())
    api_capabilities = [
        {
            **capability,
            "compatibility_version_constant": static_assignment(cap_path, "VERSION"),
            "supported_releases": sorted(static_assignment(cap_path, "SUPPORTED_RELEASES") or []),
        }
        for capability in parse_api_capabilities(cap_path)
    ]
    api_capability_snapshot = {
        "source_path": cap_path.as_posix(),
        "source_sha256": api_capability_source_sha256,
        "fixed_source_commit": pin["commit"],
        "extraction": "static AST expansion of _CAPABILITY_ROWS _cap calls and the source-declared typed-property family loop; no product module import",
        "affected_action_rows": ["api.describe", "api.invoke", "api.probe"],
        "refresh_scope": ["api.describe", "api.invoke", "api.probe", "GAP-API-SURFACE", "this source fingerprint"],
        "refresh_rule": "If this module SHA changes, refresh only these API-surface rows and their source fingerprint; do not reuse a prior capability conclusion for the changed source snapshot.",
    }
    dispatch_ids = set(g2["IMPLEMENTED_OPERATIONS"]) | set(g2["CONTROL_IMPLEMENTED_OPERATIONS"]) | set(g3)
    coordinator_only_ids = set(g2["DESKTOP_COORDINATOR_ONLY_OPERATIONS"])
    desktop_adapter_ids = set(DESKTOP_ADAPTER_METHODS) & coordinator_only_ids
    action_gaps = [
        {
            "operation_id": row["operation_id"],
            "gap_kind": (
                "NATIVE_EVIDENCE_NOT_MISSING_HANDLER" if row["actual_route"]["primary_route"] == "desktop_native_adapter"
                else "MISSING_EXECUTABLE_HANDLER"
            ),
            "reason": row["uncovered_reasons"],
            "source_locations": row["actual_route"]["handler_locations"],
        }
        for row in rows if row["actual_route"]["primary_route"] in {"none", "coordinator_only", "desktop_native_adapter"}
    ]
    gaps = [
        {
            "id": "GAP-DESKTOP-EXEC",
            "source_contract": "RELEASE_DOD U10",
            "finding": f"{len(desktop_adapter_ids)}个Desktop动作已有真实profile-gated Darwin/Windows native adapter调用路径；缺口是宿主selector profile、所需OS权限与当前native evidence。",
            "gap_kind": "HOST_PROFILE_PERMISSION_AND_NATIVE_EVIDENCE_NOT_MISSING_CODE",
            "count": len(desktop_adapter_ids),
            "operations": sorted(desktop_adapter_ids),
            "actions_missing_static_native_adapter": sorted(coordinator_only_ids - desktop_adapter_ids),
            "route_by_operation": {
                row["operation_id"]: {
                    "primary_route": row["actual_route"]["primary_route"],
                    "route_status": row["actual_route"]["route_status"],
                    "coordinator_method": row["actual_route"].get("platform_adapter_routes", {}).get("coordinator_method"),
                    "platforms": {
                        name: {
                            "adapter": platform_row["factory_target"],
                            "method_locations": platform_row["method_locations"],
                            "availability": platform_row["availability"],
                            "limitations": platform_row["limitations"],
                            "native_evidence": platform_row["native_evidence"],
                        }
                        for name, platform_row in row["actual_route"].get("platform_adapter_routes", {}).items()
                        if name in {"darwin", "win32"}
                    },
                }
                for row in rows if row["operation_id"] in desktop_adapter_ids
            },
            "native_evidence": "NOT_RUN",
            "current_status": "STATIC_ADAPTERS_PRESENT_HOST_PROFILE_PERMISSION_AND_NATIVE_EVIDENCE_UNVERIFIED",
        },
        {
            "id": "GAP-API-SURFACE",
            "source_contract": "RELEASE_DOD U03",
            "finding": (
                f"api.invoke受当前源码声明的精确接收器/方法/签名能力表约束；静态AST从当前snapshot抽取{len(api_capabilities)}项。"
                f"VERSION={static_assignment(cap_path, 'VERSION')}仅为兼容常量；真实门控为supported_releases与实际receiver/method/signature，不锁定该build。"
            ),
            "operations": ["api.describe", "api.invoke", "api.probe"],
            "capabilities": api_capabilities,
            "source_snapshot": api_capability_snapshot,
            "current_status": "SOURCE_ROUTE_PRESENT_NATIVE_UNVERIFIED",
        },
        {
            "id": "GAP-CONTROLLED-JAVA-EVIDENCE",
            "source_contract": "RELEASE_DOD U04/U08",
            "finding": "受控Java路线存在且有trusted_code门控，但本映射没有执行它；路线存在不证明任意新Java、未知COMSOL API或OS隔离验收。",
            "operations": sorted(op for op in operation_ids if op.startswith("code.")),
            "current_status": "UNVERIFIED",
        },
        {
            "id": "GAP-NATIVE-ACCEPTANCE",
            "source_contract": "RELEASE_DOD U01-U12",
            "finding": "源码handler、测试文件和历史台账都不替代当前引擎/目标机证据；本轮没有COMSOL运行，所有映射状态保留NOT_RUN/UNVERIFIED。",
            "operations_with_static_dispatch": len((dispatch_ids | desktop_adapter_ids) & new_ids),
            "operations_without_executable_dispatch": sorted(new_ids - dispatch_ids - coordinator_only_ids),
            "current_status": "NOT_RUN",
        },
        {
            "id": "GAP-TEST-INHERITANCE",
            "source_contract": "SCOPE_RESET and RELEASE_DOD U12",
            "finding": "原60项接受矩阵全部NOT_RUN且target evidence为空；没有可继承为PASS的原验收子断言。历史软件测试只保留其原始软件/fixture范围。",
            "reliable_inherited_acceptance_subassertions": 0,
            "affected_retest_core_subassertions": test_mapping["summary"]["subassertions_by_scope_class"].get(CORE_DEFAULT, 0),
            "current_status": "NOT_RUN",
        },
    ]
    prior_only = sorted(old_ids - new_ids)
    new_only = sorted(new_ids - old_ids)
    prior_vs_current = {
        "prior_action_mapping_count": len(old_ids),
        "current_catalog_count": len(new_ids),
        "prior_only_operation_ids": prior_only,
        "current_only_operation_ids": new_only,
    }
    source_deviations = [
        row for row in fingerprints
        if row.get("receipt_sha256") is not None and not row.get("pin_receipt_match")
    ]
    provenance_notes = [
        "当前固定源码身份来自docs/universal_mcp_execution/PIN.json、其RESTORE_RECEIPT.json与state/RESUME.json；恢复回执声明没有Git历史。",
        "根目录RESTORE_RECEIPT.json保留了旧source_commit 2cb4627924d1a3240818ea7cd00453d4bd2d2da8；本映射不将它用作当前基线，当前嵌套回执与PIN均为指定cd472 commit/tree。",
        (
            f"旧ACTION_COVERAGE为{len(old_ids)}项，当前真实目录为{len(new_ids)}项；差异由prior_only/current_only_operation_ids给出。"
            "当前目录新动作仍按当前源码逐项映射并保持UNVERIFIED，旧映射缺席不补造历史证据。"
        ),
        "恢复回执与PIN定义固定基线；source_fingerprints若记录CURRENT_WORKTREE_DIFFERS_FROM_RESTORE_RECEIPT，按当前候选源码记录，不改写PIN或恢复回执。",
        "tests/test_interp_native_default_units.py和unit-default证据目录属于当前工作树附加内容；单测范围不等同COMSOL原生、数值或物理验收。",
        "目录内catalog声明的baseline_commit是历史设计目录基线；产品源码固定身份仍单独记录为cd47281777eae6f4ea0d40c5c754dca9982eda86。",
    ]
    mapping = {
        "schema_version": 1,
        "mapping_kind": MAPPING_KIND,
        "source_identity": {
            "commit": pin["commit"],
            "tree": pin["tree"],
            "current_candidate_source_state": (
                "RESTORED_PIN_MATCH" if not source_deviations else "PINNED_BASE_PLUS_LOCAL_SOURCE_OVERLAY"
            ),
            "current_source_files_differing_from_restore_receipt": [
                {"path": row["path"], "receipt_sha256": row["receipt_sha256"], "current_sha256": row["sha256"]}
                for row in source_deviations
            ],
            "catalog_declared_baseline_commit": catalog.get("baseline_commit"),
            "restore_status": receipt.get("status"),
            "git_history_ready": receipt.get("git_history_ready"),
            "restore_file_count": receipt.get("file_count"),
        },
        "method": {
            "static_only": True,
            "product_modules_imported": False,
            "comsol_contacted": False,
            "acceptance_tests_run_by_mapping_agent": False,
            "other_software_test_evidence_incorporated": True,
            "software_test_evidence_is_native_acceptance": False,
            "catalog_mcp_tool_name_is_implementation_evidence": False,
            "generic_java_is_automatic_action_coverage": False,
            "route_status_semantics": {
                "STATIC_*_PRESENT_UNVERIFIED": "当前源码存在可追踪路由；不代表本轮执行、COMSOL版本兼容或验收通过。",
                "STATIC_DESKTOP_ADAPTER_PRESENT_UNVERIFIED": "当前源码存在协调器至Darwin/Windows profile-gated native adapter的可追踪路径；profile、权限和native evidence未验证。",
                "COORDINATOR_ONLY_NOT_EXECUTABLE": "仅在没有对应adapter路径时表示有准入/拒绝控制但没有动作handler；Desktop registry membership本身不表示缺少adapter。",
                "NO_EXECUTABLE_OPERATION_HANDLER": "未找到该逻辑动作的当前可执行分发路径。",
            },
        },
        "source_documents": {
            "scope_contracts": [MASTER.as_posix(), SCOPE.as_posix(), DOD.as_posix(), ARCH.as_posix()],
            "action_catalog": CATALOG.as_posix(),
            "original_acceptance_contract": ACCEPTANCE.as_posix(),
            "effective_targets": TARGETS.as_posix(),
            "historical_action_mapping": OLD_ACTIONS.as_posix(),
            "historical_acceptance_matrix": OLD_MATRIX.as_posix(),
        },
        "source_fingerprints": fingerprints,
        "api_capability_snapshot_binding": api_capability_snapshot,
        "routing_rules": [
            "优先使用当前G3 domain OPERATIONS字典和函数；再区分G2受管handler、通用API、受控Java、持久ControlDaemon和仅协调器路径。",
            "entrypoint/allowlist/manifest只证明可发现或可路由；逐行handler路径和接受状态分开记录。",
            "G3副作用按当前_effect_sources规则解析；G2/Control路径保留catalog effect与managed execution contract来源；effect不是引擎观测。",
            "代码级通用Java只映射到code.*实际handler，不作为其他catalog operation的隐式handler。",
            "Desktop coordinator allowlist membership is distinct from native adapter implementation; Darwin/Windows adapter method paths are recorded per action, with host profile/permission/native evidence kept UNVERIFIED.",
            "Public API capabilities are extracted from and SHA-bound to the exact current _g2_public_api.py snapshot; a later source change refreshes only api.describe/api.invoke/api.probe and GAP-API-SURFACE.",
        ],
        "catalog_summary": {
            "schema_version": catalog.get("schema_version"),
            "declared_operation_count": declared_count,
            "actual_operation_count": len(rows),
            "unique_operation_id_count": len(set(operation_ids)),
            "catalog_status_claims": dict(sorted(Counter(str(x.get("implementation_status")) for x in operations).items())),
            "prior_action_mapping_count": len(old_ids),
            "prior_only_operation_ids": prior_only,
            "current_only_operation_ids": new_only,
            "static_route_counts": dict(sorted(route_counts.items())),
            "current_acceptance_status_counts": dict(sorted(status_counts.items())),
            "static_g3_operation_count": len(g3),
            "static_g2_allowlist_count": len(g2["IMPLEMENTED_OPERATIONS"]),
            "static_control_operation_count": len(g2["CONTROL_IMPLEMENTED_OPERATIONS"]),
            "desktop_coordinator_admission_member_count": len(coordinator_only_ids),
            "desktop_native_adapter_route_count": len(desktop_adapter_ids),
            "unresolved_g3_modules": unresolved_g3_modules,
        },
        "actions": rows,
        "tests_mapping": test_mapping,
        "supplemental_software_test_evidence": software_evidence,
        "prior_vs_current_action_mapping": prior_vs_current,
        "gap_list_for_main_agent": gaps,
        "coverage_gaps_by_action": action_gaps,
        "provenance_notes": provenance_notes,
    }
    validate_mapping(mapping)
    return mapping


def validate_mapping(mapping: dict[str, Any]) -> None:
    if mapping.get("schema_version") != 1 or mapping.get("mapping_kind") != MAPPING_KIND:
        raise ValueError("expected an existing frozen coverage mapping")
    actions = mapping.get("actions")
    if not isinstance(actions, list):
        raise ValueError("actions is not an array")
    ids = [row.get("operation_id") for row in actions]
    actual_count = mapping["catalog_summary"]["actual_operation_count"]
    if len(actions) != actual_count or len(ids) != len(set(ids)):
        raise ValueError("action count or operation_id uniqueness check failed")
    if mapping["catalog_summary"]["declared_operation_count"] != len(actions):
        raise ValueError("catalog declared count does not match generated rows")
    catalog_ids = {row["operation_id"] for row in read_json(CATALOG)["operations"]}
    if set(ids) != catalog_ids:
        raise ValueError("stored action IDs differ from the frozen catalog")
    action_by_id = {row["operation_id"]: row for row in actions}
    desktop_rows = [action_by_id.get(operation_id) for operation_id in DESKTOP_ADAPTER_METHODS]
    if any(row is None or row["actual_route"]["primary_route"] != "desktop_native_adapter" for row in desktop_rows):
        raise ValueError("a coordinator-admitted Desktop action lost its statically traced native adapter route")
    for row in desktop_rows:
        platforms = row["actual_route"].get("platform_adapter_routes", {})
        for platform in ("darwin", "win32"):
            platform_row = platforms.get(platform, {})
            locations = platform_row.get("method_locations", [])
            if not locations or any(not isinstance(location.get("line"), int) for location in locations):
                raise ValueError(f"{row['operation_id']} has no source-located {platform} adapter method")
        if row["operation_id"] == "desktop.shell_execute":
            if (platforms["darwin"]["availability"] != "EXPLICITLY_UNSUPPORTED_PLATFORM"
                    or platforms["win32"]["factory_target"] != "WindowsDesktopAdapter"):
                raise ValueError("Java Shell platform support must retain the macOS limitation and Windows handler")
    desktop_gap = next(
        (gap for gap in mapping["gap_list_for_main_agent"] if gap.get("id") == "GAP-DESKTOP-EXEC"),
        None,
    )
    if (desktop_gap is None or desktop_gap.get("count") != len(DESKTOP_ADAPTER_METHODS)
            or desktop_gap.get("actions_missing_static_native_adapter") != []):
        raise ValueError("Desktop gap must describe native profile/permission/evidence, not missing adapter code")
    api_snapshot = mapping.get("api_capability_snapshot_binding", {})
    if (api_snapshot.get("source_path") != API_SOURCE.as_posix()
            or set(api_snapshot.get("affected_action_rows", [])) != set(API_ACTION_IDS)):
        raise ValueError("API snapshot refresh scope must contain exactly the three API actions")
    api_fingerprint = next(
        (row for row in mapping.get("source_fingerprints", [])
         if row.get("path") == api_snapshot.get("source_path")),
        None,
    )
    if not api_snapshot.get("source_sha256") or not api_fingerprint or api_fingerprint.get("sha256") != api_snapshot.get("source_sha256"):
        raise ValueError("API capability extraction is not bound to the exact current source fingerprint")
    api_gap = next((gap for gap in mapping["gap_list_for_main_agent"] if gap.get("id") == "GAP-API-SURFACE"), None)
    if not api_gap or api_gap.get("source_snapshot", {}).get("source_sha256") != api_snapshot.get("source_sha256"):
        raise ValueError("API capability gap conclusion is not bound to its source snapshot")
    for operation_id in api_snapshot.get("affected_action_rows", []):
        row = action_by_id.get(operation_id)
        bound = row and row["actual_route"].get("version_constraints", {}).get("api_capability_source_snapshot", {})
        if not bound or bound.get("sha256") != api_snapshot.get("source_sha256"):
            raise ValueError(f"{operation_id} lacks the current API capability source snapshot binding")
    tests = mapping["tests_mapping"]["tests"]
    expected_test_ids = {f"T{i:03d}" for i in range(1, 61)}
    test_ids = [test["id"] for test in tests]
    if len(tests) != 60 or set(test_ids) != expected_test_ids or len(test_ids) != len(set(test_ids)):
        raise ValueError("test ID completeness/uniqueness check failed")
    assertions = mapping["tests_mapping"]["assertions"]
    assertion_ids = [item["assertion_id"] for item in assertions]
    if len(assertion_ids) != len(set(assertion_ids)):
        raise ValueError("test assertion ID uniqueness check failed")
    referenced = [item for test in tests for item in test["assertion_ids"]]
    if sorted(referenced) != sorted(assertion_ids):
        raise ValueError("not every assertion is referenced by exactly one original test")
    if any(item.get("scope_class") not in {CORE_DEFAULT, CASE_SPECIAL} for item in assertions):
        raise ValueError("unknown subassertion scope class")
    expected_scope = {}
    for case in test_case_sections(source_text(ACCEPTANCE)):
        for section, key in (("EXECUTION", "execution"), ("PASS", "acceptance"), ("EVIDENCE", "evidence")):
            clauses = split_assertions(case[key], case["id"], section)
            if section == "EVIDENCE":
                clauses = [part.strip() for part in re.split(r"[、，,]+", case[key]) if part.strip()] or clauses
            for index, text in enumerate(clauses, 1):
                assertion_id = stable_assertion_id(case["id"], section, index, text)
                expected_scope[assertion_id] = (case["id"], section, text, classify_assertion(case["id"], section, text)[0])
    stored_scope = {
        row["assertion_id"]: (row["test_id"], row["kind"], row["text"], row["scope_class"])
        for row in assertions
    }
    if stored_scope != expected_scope:
        raise ValueError("frozen assertion IDs/text/classification changed")
    if CASE_CORE_RULE not in mapping["tests_mapping"]["scope_rules"]:
        raise ValueError("case CORE_REQUIRED equivalent-model scope rule is missing")
    target_rows = mapping["tests_mapping"]["summary"]["target_matrix"]
    configured_targets = {row["id"]: row for row in read_json(TARGETS)["targets"]}
    if {row["id"] for row in target_rows} != set(configured_targets):
        raise ValueError("frozen target IDs changed")
    for row in target_rows + [state for test in tests for state in test["target_states"]]:
        configured = configured_targets.get(row.get("target", row.get("id")))
        if (configured is None or row.get("native_scope") != configured.get("native")
                or row.get("distribution_scope") != configured.get("distribution")):
            raise ValueError("frozen native/distribution scope changed; evidence statuses may advance independently")
    native_skips = sorted(
        row["id"] for row in target_rows
        if str(row["native_scope"]).startswith("USER_REQUESTED_SKIP")
    )
    expected_skips = ["macos-arm64-6.3", "macos-x64-6.3", "macos-x64-6.4"]
    if native_skips != expected_skips:
        raise ValueError(f"frozen native skip target set differs: {native_skips}")


def render(mapping: dict[str, Any]) -> str:
    return json.dumps(mapping, ensure_ascii=False, indent=2, sort_keys=False) + "\n"


def require_initial_template(mapping: dict[str, Any]) -> None:
    if "mapping_kind" in mapping:
        raise ValueError("frozen mapping: --write cannot overwrite progress; use --refresh-api-snapshot for API metadata")
    raw = (ROOT / INITIAL_CATALOG).read_bytes()
    operations = json.loads(raw)["operations"]
    expected = {
        "schema_version": 1,
        "original_catalog_sha256": sha256_bytes(raw),
        "operation_count": len(operations),
        "rows": [{
            "operation_id": row["operation_id"], "domain": row.get("domain"), "effect": row.get("effect"),
            "classification": "UNASSESSED", "implementation_route": None,
            "implementation_status": "NOT_REVIEWED", "evidence_status": "NOT_RUN", "evidence_refs": [],
        } for row in operations],
        "test_assertion_mapping": "TO_BE_REVIEWED_FROM_ORIGINAL_03_ACCEPTANCE_NO_OLD_STATE_OVERWRITE",
    }
    if mapping != expected:
        raise ValueError("--write requires the untouched init_state UNASSESSED template; existing evidence/state is preserved")


def check_api_snapshot(mapping: dict[str, Any]) -> None:
    current_sha = sha256_bytes((ROOT / API_SOURCE).read_bytes())
    stored_sha = mapping["api_capability_snapshot_binding"]["source_sha256"]
    if current_sha != stored_sha:
        raise ValueError(
            f"API_SNAPSHOT_CHANGED: stored={stored_sha} current={current_sha}; "
            "after M1 is stable run --refresh-api-snapshot; frozen progress was not changed"
        )


def api_gap_finding(capabilities: list[dict[str, Any]], version: str) -> str:
    return (
        f"api.invoke受当前snapshot声明的精确receiver/method/signature能力表约束；静态AST抽取{len(capabilities)}项。"
        f"VERSION={version}仅为兼容常量；真实门控为supported_releases与实际signature，不锁定该build。"
    )


def refresh_api_snapshot(mapping: dict[str, Any]) -> dict[str, Any]:
    """Refresh only API static metadata; every progress/evidence field is retained."""
    validate_mapping(mapping)
    raw = (ROOT / API_SOURCE).read_bytes()
    text = raw.decode("utf-8")
    digest = sha256_bytes(raw)
    version = static_assignment(API_SOURCE, "VERSION", text=text)
    releases = sorted(static_assignment(API_SOURCE, "SUPPORTED_RELEASES", text=text) or [])
    capabilities = [{
        **capability, "compatibility_version_constant": version, "supported_releases": releases,
    } for capability in parse_api_capabilities(API_SOURCE, text=text)]
    updated = copy.deepcopy(mapping)
    snapshot = updated["api_capability_snapshot_binding"]
    snapshot["source_sha256"] = digest
    snapshot["refresh_rule"] = "After M1 is stable, use --refresh-api-snapshot to update only the three API actions, API gap/capabilities, source binding and API fingerprint; retain all progress and tests_mapping."
    for row in updated["actions"]:
        if row["operation_id"] not in API_ACTION_IDS:
            continue
        route = row["actual_route"]
        constraints = route["version_constraints"]
        constraints.pop("source_default_version", None)
        constraints["compatibility_version_constant"] = version
        constraints["source_supported_releases"] = releases
        constraints["source_constraint"] = API_VERSION_POLICY
        constraints["api_capability_source_snapshot"]["sha256"] = digest
        for locations in (route["handler_locations"], constraints["source_locations"], row["evidence"]["current_static_sources"]):
            for location in locations:
                if location.get("path") == API_SOURCE.as_posix() and location.get("symbol"):
                    current = source_location(API_SOURCE, location["symbol"], text=text)
                    if current["line"] is None:
                        raise ValueError(f"API source symbol disappeared: {location['symbol']}; inspect this API row before refresh")
                    location.update(current)
        mentions = []
        for mention in route.get("supporting_operation_mentions", []):
            if mention.get("path") == API_SOURCE.as_posix():
                matches = [index for index, line in enumerate(text.splitlines(), 1) if line.strip()[:240] == mention.get("text")]
                if not matches:
                    continue
                mention["line"] = matches[0]
            mentions.append(mention)
        route["supporting_operation_mentions"] = mentions
    gap = next(row for row in updated["gap_list_for_main_agent"] if row["id"] == "GAP-API-SURFACE")
    gap["capabilities"] = capabilities
    gap["finding"] = api_gap_finding(capabilities, version)
    gap["source_snapshot"] = copy.deepcopy(snapshot)
    fingerprint = next(row for row in updated["source_fingerprints"] if row["path"] == API_SOURCE.as_posix())
    fingerprint["sha256"] = digest
    matched = fingerprint.get("receipt_sha256") == digest
    fingerprint["pin_receipt_match"] = matched
    fingerprint["source_state"] = "RESTORED_PIN_MATCH" if matched else "CURRENT_WORKTREE_DIFFERS_FROM_RESTORE_RECEIPT"
    fingerprint["interpretation"] = (
        "current bytes match the restored source receipt" if matched else
        "current bytes are recorded as the local candidate; the fixed PIN and restore receipt remain unchanged"
    )
    validate_mapping(updated)
    check_api_snapshot(updated)
    return updated


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="initialize once from the untouched init_state UNASSESSED template")
    mode.add_argument("--refresh-api-snapshot", action="store_true", help="refresh only API static metadata; preserve all progress/evidence")
    mode.add_argument("--check", action="store_true", help="validate stored frozen scope/structure and API source binding without rebuilding progress")
    args = parser.parse_args(argv)
    try:
        destination = ROOT / OUTPUT
        if not destination.is_file():
            raise ValueError(f"coverage state is missing; run init_state first: {OUTPUT}")
        original = destination.read_bytes()
        mapping = json.loads(original)
        if args.write:
            require_initial_template(mapping)
            mapping = build_mapping()
            action = "WROTE"
        elif args.refresh_api_snapshot:
            mapping = refresh_api_snapshot(mapping)
            action = "REFRESHED_API_SNAPSHOT"
        else:
            validate_mapping(mapping)
            check_api_snapshot(mapping)
            action = "CHECKED"
        if args.write or args.refresh_api_snapshot:
            if destination.read_bytes() != original:
                raise ValueError("coverage state changed during preparation; existing progress was not overwritten")
            destination.write_text(render(mapping), encoding="utf-8")
        summary = mapping["catalog_summary"]
        tests = mapping["tests_mapping"]["summary"]
        print(
            f"{action}: actions={summary['actual_operation_count']} unique={summary['unique_operation_id_count']} "
            f"routes={json.dumps(summary['static_route_counts'], sort_keys=True)}; "
            f"tests={tests['test_case_count']} subassertions={tests['subassertion_count']} "
            f"scope={json.dumps(tests['subassertions_by_scope_class'], sort_keys=True)} "
            f"external_blocked={tests['external_blocked_target_cells']} "
            f"native_skips={len(tests['effective_native_skip_targets'])}"
        )
        return 0
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
