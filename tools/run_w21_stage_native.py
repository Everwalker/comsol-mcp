#!/usr/bin/env python3
"""Prepare and explicitly execute the bounded W21 field-identity MCP probe.

``prepare`` is metadata-only. ``execute`` uses the public stdio MCP surface,
the production project/session routes, and the daemon-owned Server/Worker
lifecycle. It builds one task-owned HeatTransfer fixture and captures the
read-only W21 field-identity probe; it never runs a Study or solver.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import sys
import textwrap
import time
from typing import Any, Mapping
from uuid import uuid4


SCHEMA = "W21_FIELD_IDENTITY_MCP_RUN_V1"
RUN_BUDGET_S = 900
RPC_WAIT_S = 45
CLEANUP_RESERVE_S = 60
REPOSITORY = Path(__file__).resolve().parents[1]
FIXTURE = REPOSITORY / "tools/java/W21Fixture.java"
PROBE = REPOSITORY / "tools/java/W21FieldIdentityProbe.java"
PROCESS_HELPER = REPOSITORY / "tools/run_function_evaluate_probe.py"
REQUIRED_TOOLS = ("operation_call", "operation_describe", "model_create")
LOGICAL_OPERATIONS = (
    "project.create", "session.start", "session.connect", "model.inspect",
    "artifact.register", "code.execute_java", "session.disconnect", "session.stop",
    "job.list", "job.status",
)


class RunnerError(RuntimeError):
    """A frozen-input, public-route, identity, or bounded-run refusal."""


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_value(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def write_json_atomic(path: Path, value: Mapping[str, Any]) -> str:
    """Write one durable receipt without replacing a pre-existing file."""
    if path.exists() or path.is_symlink():
        raise RunnerError(f"refusing to replace evidence: {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False,
                     allow_nan=False).encode("utf-8") + b"\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(fd)
    return hashlib.sha256(raw).hexdigest()


def _source_files(root: Path) -> list[Path]:
    package = root / "comsol_mcp"
    if not package.is_dir():
        raise RunnerError("source checkout does not contain comsol_mcp")
    files = set(package.rglob("*.py"))
    files.update((root / "comsol_mcp/data/g2").glob("*.json"))
    files.add(root / "comsol_mcp/worker_java/PersistentComsolWorker.java")
    files.update(root / relative for relative in (
        "tools/run_w21_stage_native.py", "tools/java/W21Fixture.java",
        "tools/java/W21FieldIdentityProbe.java", "tools/run_function_evaluate_probe.py",
        "docs/comsol_mcp_design_v1/02_ACTION_CATALOG.json",
    ))
    # exFAT may expose AppleDouble resource-fork sidecars next to source files.
    # They are filesystem metadata, never part of the frozen Python/catalog
    # closure; requested primary inputs above remain exact-path checked.
    files = {path for path in files if not path.name.startswith("._")}
    checked: list[Path] = []
    for path in sorted(files):
        if not path.is_file() or path.is_symlink():
            raise RunnerError(f"frozen source file is missing or aliased: {path.name}")
        try:
            path.resolve(strict=True).relative_to(root.resolve(strict=True))
        except ValueError as exc:
            raise RunnerError("frozen source closure escaped the checkout") from exc
        checked.append(path)
    return checked


def source_manifest(root: Path) -> dict[str, str]:
    root = root.resolve(strict=True)
    return {path.relative_to(root).as_posix(): sha256_file(path)
            for path in _source_files(root)}


def _python_identity() -> dict[str, Any]:
    executable = Path(sys.executable).resolve(strict=True)
    inventory: list[tuple[str, str]] = []
    try:
        for distribution in importlib.metadata.distributions():
            name = distribution.metadata.get("Name")
            if isinstance(name, str) and name.strip():
                normalized = name.strip().casefold().replace("_", "-")
                inventory.append((normalized, distribution.version))
    except Exception as exc:
        raise RunnerError(f"Python dependency inventory is unreadable: {type(exc).__name__}") from None
    inventory.sort()
    mcp_spec = importlib.util.find_spec("mcp")
    if mcp_spec is None or not isinstance(mcp_spec.origin, str):
        raise RunnerError("selected Python environment cannot resolve the MCP client package")
    return {
        "executable_sha256": sha256_file(executable),
        "python_version": platform.python_version(),
        "implementation": platform.python_implementation(),
        "mcp_distribution_version": importlib.metadata.version("mcp"),
        "mcp_import_origin": str(Path(mcp_spec.origin).resolve(strict=True)),
        "distribution_count": len(inventory),
        "distribution_inventory_sha256": sha256_value(inventory),
    }


def _published_tool_schemas(root: Path) -> dict[str, Any]:
    """Read the schemas from the actual registered FastMCP tool registry."""
    root_text = str(root.resolve(strict=True))
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    import comsol_mcp.mcp_server as server  # import registers tools; starts no service

    schemas = {}
    tools = server.mcp._tool_manager._tools
    for name in REQUIRED_TOOLS:
        item = tools.get(name)
        if item is None or not isinstance(item.parameters, dict):
            raise RunnerError(f"required public MCP tool is not registered: {name}")
        schemas[name] = item.parameters
    return schemas


def _logical_schemas(root: Path) -> dict[str, Any]:
    root_text = str(root.resolve(strict=True))
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    from comsol_mcp import _g2_registry

    result: dict[str, Any] = {}
    for operation in LOGICAL_OPERATIONS:
        entry = _g2_registry.BY_ID.get(operation)
        if entry is None or not isinstance(entry.input_schema, Mapping):
            raise RunnerError(f"logical operation schema is unavailable: {operation}")
        result[operation] = dict(entry.input_schema)
    return result


def _jdk_identity(home: Path) -> dict[str, Any]:
    home = home.resolve(strict=True)
    release = home / "release"
    java = home / "bin/java.exe"
    javac = home / "bin/javac.exe"
    if not all(path.is_file() for path in (release, java, javac)):
        raise RunnerError("selected Windows JDK must contain release, bin/java.exe, and bin/javac.exe")
    release_text = release.read_text(encoding="utf-8", errors="replace")
    version = next((line.split("=", 1)[1].strip('"') for line in release_text.splitlines()
                    if line.startswith("JAVA_VERSION=")), None)
    return {
        "home": str(home), "java_sha256": sha256_file(java),
        "javac_sha256": sha256_file(javac), "release_sha256": sha256_file(release),
        "java_version": version,
    }


def _comsol_identity(root: Path, version: str) -> dict[str, Any]:
    from comsol_mcp._runtime_installation import inspect_installation, runtime_id_for_root

    runtime_id = runtime_id_for_root(root)
    observed = inspect_installation(runtime_id, system="Windows")["installation"]
    version_value = observed.get("version", {}).get("value")
    build_value = observed.get("build", {}).get("value")
    if not isinstance(version_value, str) or not version_value.startswith(version):
        raise RunnerError(f"installed COMSOL version does not match selected {version}")
    executable = Path(root).resolve(strict=True) / "bin/win64/comsolmphserver.exe"
    if not executable.is_file():
        raise RunnerError("selected COMSOL installation lacks bin/win64/comsolmphserver.exe")
    return {
        "runtime_id": runtime_id, "root": str(Path(root).resolve(strict=True)),
        "version": version_value, "build": str(build_value) if build_value is not None else None,
        "server_executable_sha256": sha256_file(executable),
    }


def _check_64_receipt(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise RunnerError("6.3 preparation requires a real 6.4 probe receipt")
    value = json.loads(path.read_text(encoding="utf-8"))
    if (value.get("schema") != SCHEMA or value.get("status") != "FIELD_PROBE_CAPTURED_ONLY_NOT_ADMISSION"
            or value.get("selected_comsol", {}).get("version", "").startswith("6.4") is False
            or value.get("cleanup", {}).get("status") != "CLEANUP_COMPLETE"):
        raise RunnerError("6.3 preparation requires a cleaned-up 6.4 metadata-only probe receipt")
    return {"path": str(path.resolve()), "sha256": sha256_file(path)}


def prepare(*, version: str, comsol_root: Path, jdk_home: Path,
            evidence_root: Path, source_root: Path = REPOSITORY,
            prerequisite_64_receipt: Path | None = None) -> dict[str, Any]:
    if version not in {"6.4", "6.3"}:
        raise RunnerError("selected version must be exactly 6.4 or 6.3")
    if platform.system() != "Windows":
        raise RunnerError("prepare is metadata-only but must run on the target Windows host")
    source_root = source_root.resolve(strict=True)
    if str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))
    if os.environ.get("COMSOL_MCP_TOOL_PROFILE", "full").casefold() != "full":
        raise RunnerError("COMSOL_MCP_TOOL_PROFILE must be full to freeze the published W21 routes")
    evidence_root = evidence_root.resolve(strict=True)
    if evidence_root == source_root or source_root in evidence_root.parents:
        raise RunnerError("evidence root must be outside the source checkout")

    prerequisite = None
    if version == "6.3":
        if prerequisite_64_receipt is None:
            raise RunnerError("6.3 cannot be prepared before a successful cleaned-up 6.4 probe")
        prerequisite = _check_64_receipt(prerequisite_64_receipt)
    elif prerequisite_64_receipt is not None:
        raise RunnerError("6.4 is the first-version step and accepts no earlier receipt")

    comsol = _comsol_identity(comsol_root, version)
    jdk = _jdk_identity(jdk_home)
    manifest = source_manifest(source_root)
    tool_schemas = _published_tool_schemas(source_root)
    operation_schemas = _logical_schemas(source_root)
    python_identity = _python_identity()

    run_id = f"w21-{version.replace('.', '')}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
    run_root = evidence_root / version / run_id
    if run_root.exists() or run_root.is_symlink():
        raise RunnerError("run evidence path already exists")
    run_root.mkdir(parents=True, mode=0o700)
    workspace_root = run_root / "workspaces"
    workspace_root.mkdir(mode=0o700)
    project_workspace = workspace_root / "field-identity-probe"

    request_ids = {name: str(uuid4()) for name in (
        "project_create", "session_start", "session_connect", "model_create",
        "model_inspect_before_fixture", "fixture_register", "fixture_execute",
        "model_inspect_after_fixture", "probe_register", "probe_execute",
        "session_disconnect", "session_stop", "unknown_query",
        "failure_session_disconnect", "failure_session_stop",
    )}
    idempotency = {name: str(uuid4()) for name in (
        "project_create", "session_start", "session_connect", "fixture_register",
        "fixture_execute", "probe_register", "probe_execute", "session_disconnect", "session_stop",
        "failure_session_disconnect", "failure_session_stop",
    )}
    plan: dict[str, Any] = {
        "schema": SCHEMA, "run_id": run_id, "run_root": str(run_root.resolve()),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "requested_version": version, "selected_comsol": comsol, "selected_jdk": jdk,
        "python": python_identity, "source_root": str(source_root),
        "source_manifest": manifest, "source_manifest_sha256": sha256_value(manifest),
        "fixture_sha256": manifest["tools/java/W21Fixture.java"],
        "probe_sha256": manifest["tools/java/W21FieldIdentityProbe.java"],
        "process_preflight_sha256": manifest["tools/run_function_evaluate_probe.py"],
        "published_tool_schemas": tool_schemas,
        "published_tool_schemas_sha256": sha256_value(tool_schemas),
        "logical_operation_schemas": operation_schemas,
        "logical_operation_schemas_sha256": sha256_value(operation_schemas),
        "project_workspace": str(project_workspace),
        "stdio_home": str(run_root / "stdio-home"),
        "server_home": str(run_root / "server-home"),
        "isolation_receipt": str(run_root / "owned_server_isolation.json"),
        "scratch": str(run_root / "scratch"),
        "prerequisite_64_receipt": prerequisite,
        "request_ids": request_ids, "idempotency_keys": idempotency,
        "budgets": {
            "server_births_max": 1, "worker_births_max": 1,
            "seconds_from_session_start_dispatch": RUN_BUDGET_S,
            "ordinary_rpc_wait_seconds": RPC_WAIT_S,
            "geometry_run": 1, "mesh_run": 1, "study_dispatch": 0, "solver_dispatch": 0,
        },
        "route": "public stdio MCP; ControlDaemon; OwnedServerLauncher; one registered session",
        "prepare_side_effects": "filesystem receipts/directories only; no MCP call or COMSOL process",
        "acceptance_scope": "field identity metadata capture only; native admission remains UNVERIFIED",
    }
    plan["freeze_sha256"] = sha256_value(plan)
    write_json_atomic(run_root / "freeze.json", plan)
    state = {
        "schema": SCHEMA, "run_id": run_id, "freeze_sha256": plan["freeze_sha256"],
        "status": "PREPARED", "action_history": [], "server_births_possible": 0,
        "worker_births_possible": 0, "study_dispatch": 0, "solver_dispatch": 0,
        "job_ids": [], "unknown_action": None,
    }
    write_json_atomic(run_root / "state.json", state)
    (run_root / "scratch").mkdir(mode=0o700)
    return {"run_root": str(run_root), "freeze_sha256": plan["freeze_sha256"],
            "status": "PREPARED_ONLY", "plan": plan}


def verify_plan(plan: Mapping[str, Any], *, expected_sha256: str,
                source_root: Path = REPOSITORY) -> None:
    candidate = dict(plan)
    observed_hash = candidate.pop("freeze_sha256", None)
    if observed_hash != expected_sha256 or sha256_value(candidate) != expected_sha256:
        raise RunnerError("freeze hash mismatch; execute requires the exact prepared candidate")
    current = source_manifest(source_root)
    if current != plan.get("source_manifest") or sha256_value(current) != plan.get("source_manifest_sha256"):
        raise RunnerError("frozen source manifest drifted; no MCP call was made")
    if _python_identity() != plan.get("python"):
        raise RunnerError("Python/interpreter identity differs from the frozen candidate")
    if _published_tool_schemas(source_root) != plan.get("published_tool_schemas"):
        raise RunnerError("registered MCP tool schemas differ from the frozen candidate")
    if _logical_schemas(source_root) != plan.get("logical_operation_schemas"):
        raise RunnerError("logical operation schemas differ from the frozen candidate")
    observed = _comsol_identity(Path(plan["selected_comsol"]["root"]), plan["requested_version"])
    if observed != plan.get("selected_comsol"):
        raise RunnerError("COMSOL installation/build identity differs from the frozen candidate")
    if _jdk_identity(Path(plan["selected_jdk"]["home"])) != plan.get("selected_jdk"):
        raise RunnerError("JDK identity differs from the frozen candidate")


def validate_model_binding(response: Mapping[str, Any], *, project_id: str,
                           session_id: str, server_instance_id: str,
                           worker_epoch: int) -> dict[str, Any]:
    execution = response.get("execution")
    data = response.get("data")
    ref = execution.get("model_ref") if isinstance(execution, Mapping) else None
    if not isinstance(data, Mapping) or not isinstance(execution, Mapping) or not isinstance(ref, Mapping):
        raise RunnerError("public model_create omitted execution ModelRef identity")
    model_tag = data.get("model_tag")
    if (response.get("success") is not True
            or execution.get("project_id") != project_id
            or execution.get("session_id") != session_id
            or not isinstance(model_tag, str) or not model_tag
            or ref.get("model_tag") != model_tag
            or ref.get("session_id") != session_id
            or ref.get("server_instance_id") != server_instance_id
            or type(ref.get("generation")) is not int
            or ref.get("generation") <= 0):
        raise RunnerError("public model_create returned a foreign or incomplete project/session/server/Worker binding")
    revision = execution.get("revision")
    if type(revision) is not int or revision < 0:
        raise RunnerError("public model_create omitted a non-negative model revision")
    return {"project_id": project_id, "session_id": session_id,
            "server_instance_id": server_instance_id,
            "model_tag": model_tag, "worker_epoch": worker_epoch,
            "model_ref": dict(ref), "revision": revision}


def _validate_model_inspect(response: Mapping[str, Any], binding: Mapping[str, Any]) -> int:
    execution = response.get("execution")
    if (response.get("success") is not True or not isinstance(execution, Mapping)
            or execution.get("project_id") != binding["project_id"]
            or execution.get("session_id") != binding["session_id"]
            or execution.get("model_ref") != binding["model_ref"]
            or type(execution.get("revision")) is not int
            or execution["revision"] != binding.get("revision")):
        raise RunnerError("model.inspect did not read back the exact project/session/ModelRef/revision")
    return execution["revision"]


def _is_unknown(response: Mapping[str, Any]) -> bool:
    data = response.get("data")
    error = response.get("error")
    return bool(response.get("execution_state_unknown") is True
                or (isinstance(data, Mapping) and (
                    data.get("execution_state_unknown") is True or data.get("status") == "UNKNOWN"))
                or (isinstance(error, Mapping) and error.get("execution_state_unknown") is True))


def _public_payload(result: Any) -> dict[str, Any]:
    """Extract only the structured MCP business envelope; text is fallback."""
    if isinstance(result, Mapping):
        if isinstance(result.get("structuredContent"), Mapping):
            payload = dict(result["structuredContent"])
        else:
            payload = dict(result)
        if result.get("isError") is True:
            payload.setdefault("success", False)
        return payload
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, Mapping):
        payload = dict(structured)
        if getattr(result, "isError", False):
            payload.setdefault("success", False)
        return payload
    for block in getattr(result, "content", ()) or ():
        text = getattr(block, "text", None)
        if isinstance(text, str):
            try:
                parsed = json.loads(text)
            except (ValueError, TypeError):
                continue
            if isinstance(parsed, Mapping):
                return dict(parsed)
    raise RunnerError("stdio MCP returned no structured business envelope")


def _response_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    data = payload.get("data") if isinstance(payload.get("data"), Mapping) else {}
    execution = payload.get("execution") if isinstance(payload.get("execution"), Mapping) else {}
    error = payload.get("error") if isinstance(payload.get("error"), Mapping) else {}
    # Keep just identity/status fields needed for audit and recovery; never log
    # full process command lines, environment, tokens, source text, or stderr.
    return {
        "success": payload.get("success"), "error_code": error.get("code"),
        "execution_state_unknown": _is_unknown(payload),
        "project_id": data.get("project_id") or execution.get("project_id"),
        "session_id": data.get("session_id") or execution.get("session_id"),
        "job_id": data.get("job_id") or execution.get("job_id") or payload.get("job_id"),
        "status": data.get("status") or data.get("state"),
    }


class _RunState:
    def __init__(self, path: Path, state: dict[str, Any]):
        self.path = path
        self.value = state

    def save(self) -> None:
        temporary = self.path.with_name(self.path.name + ".pending")
        if temporary.exists() or temporary.is_symlink():
            raise RunnerError("unfinished state write exists; refusing to resume or replace")
        raw = json.dumps(self.value, sort_keys=True, indent=2, ensure_ascii=False,
                         allow_nan=False).encode("utf-8") + b"\n"
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb", closefd=False) as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            os.close(fd)
        os.replace(temporary, self.path)

    def event(self, action: str, status: str, **extra: Any) -> None:
        row = {"action": action, "status": status,
               "at": datetime.now(timezone.utc).isoformat(), **extra}
        self.value["action_history"].append(row)
        self.save()


class _MCPCalls:
    def __init__(self, session: Any):
        self.session = session

    async def call(self, name: str, params: Mapping[str, Any]) -> dict[str, Any]:
        result = await asyncio.wait_for(
            self.session.call_tool(name, dict(params)), timeout=RPC_WAIT_S,
        )
        return _public_payload(result)


async def _validate_live_tools(client: _MCPCalls, expected: Mapping[str, Any]) -> None:
    listed = await asyncio.wait_for(client.session.list_tools(), timeout=RPC_WAIT_S)
    rows = getattr(listed, "tools", None)
    if rows is None and isinstance(listed, Mapping):
        rows = listed.get("tools")
    actual: dict[str, Any] = {}
    for row in rows or ():
        name = getattr(row, "name", None) if not isinstance(row, Mapping) else row.get("name")
        schema = getattr(row, "inputSchema", None) if not isinstance(row, Mapping) else row.get("inputSchema")
        if isinstance(name, str) and isinstance(schema, Mapping) and name in REQUIRED_TOOLS:
            actual[name] = dict(schema)
    if actual != expected:
        raise RunnerError("live stdio tools/list schemas differ from the frozen source registry")


def _operation_params(operation: str, arguments: Mapping[str, Any],
                      execution: Mapping[str, Any] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"operation_id": operation, "arguments": dict(arguments)}
    if execution is not None:
        result["execution"] = dict(execution)
    return result


def _assert_success(response: Mapping[str, Any], label: str) -> Mapping[str, Any]:
    if _is_unknown(response):
        raise RunnerError(f"{label} returned UNKNOWN")
    if response.get("success") is not True:
        error = response.get("error")
        code = error.get("code") if isinstance(error, Mapping) else "ROUTE_FAILED"
        raise RunnerError(f"{label} failed deterministically: {code}")
    data = response.get("data")
    return data if isinstance(data, Mapping) else {}


async def run_metadata_protocol(client: _MCPCalls, plan: Mapping[str, Any],
                                state: _RunState, *, clock=time.monotonic,
                                preflight=None) -> dict[str, Any]:
    """Run one prepared route sequence; tests inject only the stdio transport."""
    if state.value.get("status") != "PREPARED" or state.value.get("action_history"):
        raise RunnerError("state is not pristine PREPARED; replay is forbidden")
    preflight_records = preflight() if preflight is not None else []
    await _validate_live_tools(client, plan["published_tool_schemas"])

    request_ids = plan["request_ids"]
    keys = plan["idempotency_keys"]
    project_workspace = Path(plan["project_workspace"])
    workspace_root = project_workspace.parent
    project_id = session_id = None
    connected: dict[str, Any] | None = None
    binding: dict[str, Any] | None = None
    deadline: float | None = None
    any_unknown = False
    owned_session_verified = False

    async def query_unknown_once(name: str, request_id: str | None,
                                 response: Mapping[str, Any] | None) -> None:
        """Make one scoped read-only status query; never resubmit the action."""
        summary = _response_summary(response or {})
        reported_job_id = summary.get("job_id")
        reported_project_id = summary.get("project_id")
        if (isinstance(reported_job_id, str) and reported_job_id
                and reported_project_id == project_id):
            operation = "job.status"
            arguments = {"job_id": reported_job_id}
        elif name in {"fixture.execute", "probe.execute"} and isinstance(project_id, str):
            operation = "job.list"
            arguments = {"limit": 100, "project_id": project_id}
        else:
            state.value["recovery"]["read_only_query"] = {
                "status": "NOT_AVAILABLE_FOR_ACTION", "action": name,
            }
            state.save()
            return

        query_id = request_ids.get("unknown_query")
        params = _operation_params(operation, arguments, {
            "project_id": project_id, "request_id": query_id,
            **({"session_id": session_id} if isinstance(session_id, str) else {}),
            "rpc_timeout_s": RPC_WAIT_S,
        })
        query_record = {
            "status": "READ_ONLY_QUERY_INTENT", "operation": operation,
            "params_sha256": sha256_value(params), "request_id": query_id,
            "original_request_id": request_id,
            "job_id": reported_job_id if isinstance(reported_job_id, str) else None,
        }
        state.value["recovery"]["read_only_query"] = query_record
        state.save()
        try:
            query_response = await client.call("operation_call", params)
        except BaseException as exc:
            query_record.update({"status": "QUERY_UNKNOWN", "exception_type": type(exc).__name__})
            state.save()
            return

        query_record["response"] = _response_summary(query_response)
        query_record["status"] = "QUERY_RESPONSE_RECORDED"
        query_data = query_response.get("data")
        if operation == "job.status":
            observed_project_id = (query_data.get("project_id")
                                   if isinstance(query_data, Mapping) else None)
            observed_job_id = (query_data.get("job_id")
                               if isinstance(query_data, Mapping) else None)
            query_record["exact_job_identity_confirmed"] = (
                observed_project_id == project_id and observed_job_id == reported_job_id)
        if operation == "job.list" and isinstance(query_data, Mapping):
            rows = query_data.get("jobs")
            matches = []
            if isinstance(rows, list):
                for row in rows:
                    if not isinstance(row, Mapping):
                        continue
                    metadata = row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {}
                    execution = metadata.get("execution") if isinstance(metadata.get("execution"), Mapping) else {}
                    observed_request = (row.get("request_id") or metadata.get("request_id")
                                        or execution.get("request_id"))
                    if request_id and observed_request == request_id:
                        matches.append(row)
            query_record["matching_job_rows"] = [
                {key: row.get(key) for key in ("job_id", "status", "request_id", "project_id")}
                for row in matches
            ]
            for row in matches:
                job_id = row.get("job_id")
                if isinstance(job_id, str) and job_id and job_id not in state.value.setdefault("job_ids", []):
                    state.value["job_ids"].append(job_id)
        state.value["recovery"]["job_ids"] = list(state.value.get("job_ids", []))
        state.save()

    async def dispatch(name: str, tool: str, params: Mapping[str, Any], *,
                       counted_server_birth=False, counted_worker_birth=False) -> dict[str, Any]:
        nonlocal any_unknown, deadline
        if any_unknown or state.value.get("status") == "UNKNOWN":
            raise RunnerError("UNKNOWN is terminal; no retry, cleanup, or later mutation is allowed")
        if deadline is not None:
            is_cleanup = (name in {"session.disconnect", "session.stop"}
                          or name.startswith("failure_cleanup."))
            limit = deadline if is_cleanup else deadline - CLEANUP_RESERVE_S
            if clock() >= limit:
                raise RunnerError("15-minute birth budget reached its work/cleanup boundary")
        # The request identity and possible birth are durable before MCP can
        # dispatch. A transport failure after this line is never replayed.
        request_id = request_ids.get(name)
        idempotency_key = keys.get(name)
        operation_arguments = params.get("arguments")
        execution_binding = params.get("execution")
        for fields in (execution_binding, operation_arguments):
            if isinstance(fields, Mapping):
                if request_id is None and isinstance(fields.get("request_id"), str):
                    request_id = fields["request_id"]
                if idempotency_key is None and isinstance(fields.get("idempotency_key"), str):
                    idempotency_key = fields["idempotency_key"]
        intent = {"tool": tool, "params_sha256": sha256_value(params),
                  "request_id": request_id, "idempotency_key": idempotency_key}
        state.value["actions"] = state.value.get("actions", {})
        if name in state.value["actions"]:
            raise RunnerError(f"action already has a durable intent: {name}")
        state.value["actions"][name] = {"status": "DISPATCH_INTENT", **intent}
        if counted_server_birth:
            state.value["server_births_possible"] = 1
            deadline = clock() + RUN_BUDGET_S
        if counted_worker_birth:
            state.value["worker_births_possible"] = 1
        state.save()
        try:
            response = await client.call(tool, params)
        except BaseException as exc:
            state.value["actions"][name].update({"status": "UNKNOWN", "exception_type": type(exc).__name__})
            state.value["status"] = "UNKNOWN"
            state.value["unknown_action"] = name
            state.value["recovery"] = {"request_id": request_id,
                                        "idempotency_key": idempotency_key, "job_ids": list(state.value.get("job_ids", [])),
                                        "replay_permitted": False, "cleanup_permitted": False}
            state.save()
            any_unknown = True
            await query_unknown_once(name, request_id, None)
            raise RunnerError(f"{name} transport outcome is UNKNOWN; original IDs preserved") from None
        summary = _response_summary(response)
        state.value["actions"][name]["response"] = summary
        job_id = summary.get("job_id")
        if isinstance(job_id, str) and job_id and job_id not in state.value.setdefault("job_ids", []):
            state.value["job_ids"].append(job_id)
        if _is_unknown(response):
            state.value["actions"][name]["status"] = "UNKNOWN"
            state.value["status"] = "UNKNOWN"
            state.value["unknown_action"] = name
            state.value["recovery"] = {"request_id": request_id,
                                        "idempotency_key": idempotency_key, "job_ids": list(state.value["job_ids"]),
                                        "replay_permitted": False, "cleanup_permitted": False}
            state.save()
            any_unknown = True
            await query_unknown_once(name, request_id, response)
            raise RunnerError(f"{name} returned UNKNOWN; no later mutation is allowed")
        state.value["actions"][name]["status"] = "RESPONSE_RECORDED"
        state.save()
        return response

    def check_budget_cleanup() -> None:
        if deadline is not None and clock() >= deadline:
            raise RunnerError("15-minute Server/Worker birth budget expired")

    async def cleanup_after_deterministic_failure() -> None:
        existing = state.value.get("actions", {})
        if any(name in existing for name in (
                "session.disconnect", "session.stop",
                "failure_cleanup.session_disconnect", "failure_cleanup.session_stop")):
            state.value["failure_cleanup"] = {"status": "NOT_RETRIED_EXISTING_CLEANUP_INTENT"}
            state.save()
            return
        if (not owned_session_verified or not isinstance(project_id, str)
                or not isinstance(session_id, str)):
            state.value["failure_cleanup"] = {"status": "NOT_POSSIBLE_UNVERIFIED_SESSION_BINDING"}
            state.save()
            return

        cleanup = {"status": "DISCONNECT_INTENT"}
        state.value["failure_cleanup"] = cleanup
        state.save()
        try:
            disconnected_response = await dispatch("failure_cleanup.session_disconnect", "operation_call",
                _operation_params("session.disconnect", {
                    "project_id": project_id, "session_id": session_id, "retire_worker": True,
                    "idempotency_key": keys["failure_session_disconnect"],
                    "request_id": request_ids["failure_session_disconnect"],
                }, {"project_id": project_id, "session_id": session_id,
                    "idempotency_key": keys["failure_session_disconnect"],
                    "request_id": request_ids["failure_session_disconnect"],
                    "rpc_timeout_s": RPC_WAIT_S}))
            disconnected = _assert_success(disconnected_response, "failure cleanup session.disconnect")
            if (disconnected.get("project_id") != project_id
                    or disconnected.get("session_id") != session_id
                    or disconnected.get("worker_handle_preserved") is not False):
                raise RunnerError("failure cleanup lacked exact Worker retirement proof")
            cleanup.update({"status": "STOP_INTENT", "worker_retired": True})
            state.save()
            stopped_response = await dispatch("failure_cleanup.session_stop", "operation_call",
                _operation_params("session.stop", {
                    "project_id": project_id, "session_id": session_id,
                    "authorization_ref": f"Task-scoped W21 metadata probe cleanup {plan['run_id']}",
                    "idempotency_key": keys["failure_session_stop"],
                    "request_id": request_ids["failure_session_stop"],
                }, {"project_id": project_id, "session_id": session_id,
                    "idempotency_key": keys["failure_session_stop"],
                    "request_id": request_ids["failure_session_stop"],
                    "rpc_timeout_s": RPC_WAIT_S}))
            stopped = _assert_success(stopped_response, "failure cleanup session.stop")
            if (stopped.get("project_id") != project_id
                    or stopped.get("session_id") != session_id
                    or stopped.get("state") != "STOPPED"
                    or stopped.get("server_stopped") is not True):
                raise RunnerError("failure cleanup lacked exact owned Server stop proof")
            cleanup.update({"status": "CLEANUP_COMPLETE", "server_stopped": True})
            isolation_path = Path(plan["isolation_receipt"])
            if isolation_path.is_file() and not isolation_path.is_symlink():
                _mark_isolation_receipt_stopped(isolation_path)
            state.save()
        except Exception as cleanup_error:
            cleanup["status"] = ("UNKNOWN_NO_FURTHER_ACTION" if any_unknown
                                  else "INCOMPLETE_NO_RETRY")
            cleanup["error_type"] = type(cleanup_error).__name__
            state.save()

    state.value["status"] = "RUNNING"
    state.save()
    try:
        # The fixed helper returns only process Name/PID/missing flags and ran
        # before the first MCP request above.
        state.value["process_preflight"] = preflight_records

        env_project = {"request_id": request_ids["project_create"],
                       "idempotency_key": keys["project_create"]}
        create_response = await dispatch("project.create", "operation_call", _operation_params(
            "project.create", {"label": f"W21 {plan['requested_version']} field probe {plan['run_id']}",
                                "workspace": str(project_workspace.relative_to(workspace_root)),
                                "policy": {"permissions": ["inspect", "project_write", "trusted_code", "host_control"]},
                                "idempotency_key": keys["project_create"],
                                "request_id": request_ids["project_create"]}, env_project))
        data = _assert_success(create_response, "project.create")
        project_id = data.get("project_id")
        if not isinstance(project_id, str) or not project_id:
            raise RunnerError("project.create omitted its minted project_id")
        if not project_workspace.is_dir() or project_workspace.is_symlink():
            raise RunnerError("project.create did not create the exact task-owned workspace")

        start_args = {"project_id": project_id, "runtime_id": plan["selected_comsol"]["runtime_id"],
                      "options": {}, "resources": {}, "idempotency_key": keys["session_start"],
                      "request_id": request_ids["session_start"]}
        start_response = await dispatch("session.start", "operation_call",
            _operation_params("session.start", start_args,
                              {"project_id": project_id, "request_id": request_ids["session_start"],
                               "idempotency_key": keys["session_start"]}), counted_server_birth=True)
        started = _assert_success(start_response, "session.start")
        session_id = started.get("session_id")
        endpoint = started.get("endpoint")
        server_identity = started.get("server_process_identity")
        if (started.get("project_id") != project_id or started.get("runtime_id") != plan["selected_comsol"]["runtime_id"]
                or started.get("server_ownership") != "mcp_managed" or started.get("loopback_only_verified") is not True
                or not isinstance(session_id, str) or not session_id
                or not isinstance(endpoint, Mapping) or endpoint.get("host") != "127.0.0.1"
                or type(endpoint.get("port")) is not int or not isinstance(server_identity, Mapping)):
            raise RunnerError("session.start did not return an exact owned loopback Server/session identity")
        owned_session_verified = True

        connect_args = {"project_id": project_id, "session_id": session_id,
                        "runtime_id": plan["selected_comsol"]["runtime_id"], "endpoint": dict(endpoint),
                        "idempotency_key": keys["session_connect"], "request_id": request_ids["session_connect"]}
        connect_response = await dispatch("session.connect", "operation_call",
            _operation_params("session.connect", connect_args,
                              {"project_id": project_id, "session_id": session_id,
                               "request_id": request_ids["session_connect"],
                               "idempotency_key": keys["session_connect"], "rpc_timeout_s": RPC_WAIT_S}),
            counted_worker_birth=True)
        connected = dict(_assert_success(connect_response, "session.connect"))
        worker_epoch = connected.get("worker_epoch")
        server_instance = connected.get("server_instance_id")
        if (connected.get("project_id") != project_id or connected.get("session_id") != session_id
                or connected.get("endpoint") != dict(endpoint) or connected.get("server_ownership") != "mcp_managed"
                or not isinstance(server_instance, str) or not server_instance
                or not isinstance(connected.get("worker_instance_id"), str)
                or type(worker_epoch) is not int or worker_epoch <= 0
                or not str(connected.get("remote_engine_version", "")).startswith(plan["selected_comsol"]["version"])
                or str(connected.get("remote_engine_build")) != str(plan["selected_comsol"]["build"])):
            raise RunnerError("session.connect identity/version/build differs from the frozen Server/runtime")

        # model_create has no ModelRef yet, but is explicitly session-scoped.
        model_response = await dispatch("model_create", "model_create", {
            "name": f"W21_{plan['run_id']}",
            "execution": {"project_id": project_id, "session_id": session_id,
                          "request_id": request_ids["model_create"]},
        })
        binding = validate_model_binding(model_response, project_id=project_id,
                                         session_id=session_id, server_instance_id=server_instance,
                                         worker_epoch=worker_epoch)
        inspect_response = await dispatch("model.inspect", "operation_call", _operation_params(
            "model.inspect", {"detail": "summary"},
            {"project_id": project_id, "session_id": session_id, "model_ref": binding["model_ref"],
             "expected_revision": binding["revision"], "request_id": request_ids["model_inspect_before_fixture"],
             "rpc_timeout_s": RPC_WAIT_S}))
        binding["revision"] = _validate_model_inspect(inspect_response, binding)

        fixture_dir = project_workspace / "fixtures"
        fixture_dir.mkdir(mode=0o700)
        staged: dict[str, str] = {}
        for short_name, source in (("W21Fixture.java", FIXTURE), ("W21FieldIdentityProbe.java", PROBE)):
            source_hash = plan["source_manifest"][f"tools/java/{short_name}"]
            if sha256_file(source) != source_hash:
                raise RunnerError("Java source changed after prepare; no execution is allowed")
            destination = fixture_dir / short_name
            shutil.copyfile(source, destination)
            if sha256_file(destination) != source_hash:
                raise RunnerError("project-local Java source copy failed exact hash verification")
            staged[short_name] = str(destination)

        async def register_source(label: str, key: str, request_id: str, path: str) -> dict[str, Any]:
            response = await dispatch(label, "operation_call", _operation_params(
                "artifact.register", {"project_id": project_id, "path": path,
                    "role": "w21_probe_source", "classification": "task_owned_frozen_java_source",
                    "idempotency_key": key, "request_id": request_id},
                {"project_id": project_id, "idempotency_key": key, "request_id": request_id,
                 "rpc_timeout_s": RPC_WAIT_S}))
            result = _assert_success(response, label)
            if result.get("sha256") != sha256_file(Path(path)):
                raise RunnerError("artifact.register hash differs from staged frozen Java source")
            return response

        await register_source("fixture.register", keys["fixture_register"],
                              request_ids["fixture_register"], staged["W21Fixture.java"])

        # Production code.execute_java requires an independently checked owned
        # Server isolation receipt. It is configured before the stdio child
        # starts; this bridge records only the exact start response and an OS
        # command hash, never a process command line.
        _write_isolation_receipt(Path(plan["isolation_receipt"]), server_identity, endpoint)

        fixture_response = await dispatch("fixture.execute", "operation_call", _operation_params(
            "code.execute_java", {"source_artifact": staged["W21Fixture.java"],
                "entrypoint": "W21Fixture", "arguments": {}, "mode": "trusted", "timeout_s": 240},
            {"project_id": project_id, "session_id": session_id, "model_ref": binding["model_ref"],
             "expected_revision": binding["revision"], "idempotency_key": keys["fixture_execute"],
             "request_id": request_ids["fixture_execute"], "rpc_timeout_s": RPC_WAIT_S}))
        fixture_data = _assert_success(fixture_response, "code.execute_java(W21Fixture)")
        if fixture_data.get("execution_success") is not True:
            raise RunnerError("fixture Java execution did not return execution_success=true")
        fixture_readback = fixture_data.get("readback")
        if not isinstance(fixture_readback, Mapping) or fixture_readback.get("status") != "BUILT_NOT_SOLVED":
            raise RunnerError("fixture did not prove BUILT_NOT_SOLVED")
        fixture_execution = fixture_response.get("execution")
        if (not isinstance(fixture_execution, Mapping)
                or fixture_execution.get("project_id") != project_id
                or fixture_execution.get("session_id") != session_id
                or fixture_execution.get("model_ref") != binding["model_ref"]
                or type(fixture_execution.get("revision")) is not int
                or fixture_execution["revision"] < binding["revision"]):
            raise RunnerError("fixture reply omitted its exact model binding or resulting revision")
        binding["revision"] = fixture_execution["revision"]
        state.value["geometry_run"] = 1
        state.value["mesh_run"] = 1
        state.save()

        after_fixture = await dispatch("model.inspect.after_fixture", "operation_call", _operation_params(
            "model.inspect", {"detail": "summary"},
            {"project_id": project_id, "session_id": session_id, "model_ref": binding["model_ref"],
             "expected_revision": binding["revision"], "request_id": request_ids["model_inspect_after_fixture"],
             "rpc_timeout_s": RPC_WAIT_S}))
        binding["revision"] = _validate_model_inspect(after_fixture, binding)
        await register_source("probe.register", keys["probe_register"],
                              request_ids["probe_register"], staged["W21FieldIdentityProbe.java"])
        probe_response = await dispatch("probe.execute", "operation_call", _operation_params(
            "code.execute_java", {"source_artifact": staged["W21FieldIdentityProbe.java"],
                "entrypoint": "W21FieldIdentityProbe", "arguments": {
                    "expected_model_tag": binding["model_tag"], "physics_tag": "ht"},
                "mode": "trusted", "timeout_s": 240},
            {"project_id": project_id, "session_id": session_id, "model_ref": binding["model_ref"],
             "expected_revision": binding["revision"], "idempotency_key": keys["probe_execute"],
             "request_id": request_ids["probe_execute"], "rpc_timeout_s": RPC_WAIT_S}))
        probe_data = _assert_success(probe_response, "code.execute_java(W21FieldIdentityProbe)")
        if probe_data.get("execution_success") is not True:
            raise RunnerError("field identity probe did not return execution_success=true")
        probe_execution = probe_response.get("execution")
        if (not isinstance(probe_execution, Mapping)
                or probe_execution.get("project_id") != project_id
                or probe_execution.get("session_id") != session_id
                or probe_execution.get("model_ref") != binding["model_ref"]
                or type(probe_execution.get("revision")) is not int
                or probe_execution.get("revision") != binding["revision"]):
            raise RunnerError("probe reply omitted the exact project/session/ModelRef/revision")
        binding["revision"] = probe_execution["revision"]
        raw_probe = probe_data.get("readback")
        if isinstance(raw_probe, str):
            try:
                probe = json.loads(raw_probe)
            except json.JSONDecodeError as exc:
                raise RunnerError("field identity probe returned malformed JSON") from exc
        elif isinstance(raw_probe, Mapping):
            probe = dict(raw_probe)
        else:
            raise RunnerError("field identity probe returned no structured payload")
        if (probe.get("probe") != "W21FieldIdentityProbe"
                or probe.get("status") != "STRUCTURE_CAPTURED_ONLY"
                or probe.get("native_admission") != "UNVERIFIED"
                or probe.get("identity", {}).get("model_tag") != binding["model_tag"]):
            raise RunnerError("probe output is incomplete or claims a scope outside metadata capture")

        report = {
            "schema": SCHEMA, "run_id": plan["run_id"], "freeze_sha256": plan["freeze_sha256"],
            "status": "FIELD_PROBE_CAPTURED_ONLY_NOT_ADMISSION", "requested_version": plan["requested_version"],
            "selected_comsol": plan["selected_comsol"], "selected_jdk": plan["selected_jdk"],
            "project_id": project_id, "session_id": session_id,
            "owned_server": {"pid": server_identity.get("pid"),
                             "birth": server_identity.get("birth"),
                             "host": endpoint.get("host"), "port": endpoint.get("port")},
            "server_instance_id": server_instance, "worker_instance_id": connected["worker_instance_id"],
            "remote_engine_version": connected["remote_engine_version"],
            "remote_engine_build": connected["remote_engine_build"],
            "worker_epoch": worker_epoch, "model_binding": binding,
            "fixture_sha256": plan["fixture_sha256"], "probe_sha256": plan["probe_sha256"],
            "fixture_readback": dict(fixture_readback), "probe_readback": probe,
            "budgets": dict(plan["budgets"]), "native_admission": "UNVERIFIED",
            "physical_validation": "UNVERIFIED", "study_dispatch": 0, "solver_dispatch": 0,
            "cleanup": {"status": "PENDING"},
        }
        state.value["probe_capture"] = {"status": "CAPTURED", "payload_sha256": sha256_value(probe)}
        state.save()

        # Do not issue lifecycle cleanup after any ambiguous operation. The
        # exact public lifecycle routes own quiescence and process retirement.
        check_budget_cleanup()
        disconnect_args = {"project_id": project_id, "session_id": session_id, "retire_worker": True,
                           "idempotency_key": keys["session_disconnect"],
                           "request_id": request_ids["session_disconnect"]}
        disconnect = await dispatch("session.disconnect", "operation_call",
            _operation_params("session.disconnect", disconnect_args,
                              {"project_id": project_id, "session_id": session_id,
                               "idempotency_key": keys["session_disconnect"],
                               "request_id": request_ids["session_disconnect"],
                               "rpc_timeout_s": RPC_WAIT_S}))
        disconnected = _assert_success(disconnect, "session.disconnect(retire_worker=true)")
        if (disconnected.get("project_id") != project_id or disconnected.get("session_id") != session_id
                or disconnected.get("worker_handle_preserved") is not False):
            raise RunnerError("session.disconnect did not prove exact Worker retirement")

        stop_ref = f"Task-scoped W21 metadata probe cleanup {plan['run_id']}"
        stop_args = {"project_id": project_id, "session_id": session_id,
                     "authorization_ref": stop_ref, "idempotency_key": keys["session_stop"],
                     "request_id": request_ids["session_stop"]}
        stopped = await dispatch("session.stop", "operation_call", _operation_params(
            "session.stop", stop_args,
            {"project_id": project_id, "session_id": session_id,
             "idempotency_key": keys["session_stop"], "request_id": request_ids["session_stop"],
             "rpc_timeout_s": RPC_WAIT_S}))
        stopped_data = _assert_success(stopped, "session.stop")
        if (stopped_data.get("project_id") != project_id or stopped_data.get("session_id") != session_id
                or stopped_data.get("state") != "STOPPED" or stopped_data.get("server_stopped") is not True):
            raise RunnerError("session.stop did not prove owned Server stop")
        _mark_isolation_receipt_stopped(Path(plan["isolation_receipt"]))
        report["cleanup"] = {"status": "CLEANUP_COMPLETE", "worker_retired": True,
                             "owned_server_stopped": True, "server_stop_evidence": stopped_data.get("stop_evidence")}
        report_path = Path(plan["run_root"]) / "field_probe_receipt.json"
        # run_root is derived from the frozen evidence paths, not a caller path.
        write_json_atomic(report_path, report)
        state.value["status"] = report["status"]
        state.value["receipt_path"] = str(report_path)
        state.value["receipt_sha256"] = sha256_file(report_path)
        state.save()
        return report
    except RunnerError:
        if not any_unknown:
            state.value["status"] = "FAILED"
            state.value["failure"] = "deterministic refusal; no implicit replay"
            state.save()
            await cleanup_after_deterministic_failure()
        raise
    except Exception as exc:
        if not any_unknown:
            state.value["status"] = "FAILED"
            state.value["failure"] = {"kind": "UNEXPECTED_LOCAL_ERROR",
                                      "error_type": type(exc).__name__}
            state.save()
            await cleanup_after_deterministic_failure()
        raise RunnerError(f"runner local failure: {type(exc).__name__}; state receipt preserved") from None


def _write_isolation_receipt(path: Path, server_identity: Mapping[str, Any],
                             endpoint: Mapping[str, Any]) -> None:
    """Bind G2 trusted Java to the daemon-created exact live Windows Server."""
    from comsol_mcp._g2_isolation import _windows_process_snapshot, _windows_socket_rows
    from comsol_mcp._platform_process import process_identity

    pid = server_identity.get("pid")
    birth = server_identity.get("birth")
    port = endpoint.get("port")
    if (type(pid) is not int or pid <= 1 or not isinstance(birth, str)
            or not birth.startswith("start_epoch_ms:") or type(port) is not int):
        raise RunnerError("session.start identity cannot support the required G2 isolation proof")
    try:
        birth_ms = int(birth.split(":", 1)[1])
    except ValueError as exc:
        raise RunnerError("session.start process birth is malformed") from exc
    before = process_identity(pid, platform_name="nt")
    observed = _windows_process_snapshot(pid)
    after = process_identity(pid, platform_name="nt")
    if (not isinstance(before, Mapping) or before.get("alive") is not True
            or before.get("start_epoch_ms") != birth_ms
            or not isinstance(after, Mapping) or after.get("alive") is not True
            or after.get("start_epoch_ms") != birth_ms
            or not isinstance(observed, Mapping)
            or not isinstance(observed.get("command_sha256"), str)):
        raise RunnerError("live OS process identity differs from the exact session.start birth")
    command = observed.get("command", "")
    if not isinstance(command, str) or "comsol" not in command.casefold() or "mphserver" not in command.casefold():
        raise RunnerError("session.start PID is not verified as COMSOL Server")
    rows = _windows_socket_rows(port)
    listeners = [row for row in rows if row.get("state") == "LISTEN"]
    if (len(listeners) != 1 or listeners[0].get("pid") != pid
            or listeners[0].get("endpoint") != f"127.0.0.1:{port}"):
        raise RunnerError("live listener no longer matches the session.start loopback identity")
    write_json_atomic(path, {"status": "RUNNING", "process": {
        "pid": pid, "birth": observed.get("birth"),
        "command_sha256": observed["command_sha256"], "port": port,
    }, "proof_scope": "daemon session.start identity + live Windows PID/birth/loopback listener"})


def _mark_isolation_receipt_stopped(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise RunnerError("owned Server isolation receipt disappeared before cleanup completion")
    value = json.loads(path.read_text(encoding="utf-8"))
    value["status"] = "STOPPED"
    write_path = path.with_name(path.name + ".stopped")
    if write_path.exists() or write_path.is_symlink():
        raise RunnerError("stopped isolation receipt destination already exists")
    write_path.write_bytes(json.dumps(value, sort_keys=True, indent=2).encode("utf-8") + b"\n")
    os.replace(write_path, path)


def _load_plan(path: Path, expected: str) -> tuple[dict[str, Any], _RunState]:
    if path.is_symlink() or not path.is_file():
        raise RunnerError("freeze plan must be a regular file")
    plan = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(plan, dict) or plan.get("schema") != SCHEMA:
        raise RunnerError("unsupported freeze plan")
    verify_plan(plan, expected_sha256=expected, source_root=Path(plan["source_root"]))
    state_path = path.parent / "state.json"
    if state_path.is_symlink() or not state_path.is_file():
        raise RunnerError("prepared state receipt is missing or aliased")
    state_value = json.loads(state_path.read_text(encoding="utf-8"))
    if state_value.get("freeze_sha256") != expected:
        raise RunnerError("state receipt belongs to a different frozen plan")
    if state_value.get("status") != "PREPARED" or state_value.get("action_history"):
        raise RunnerError("state is not pristine PREPARED; replay is forbidden")
    plan["run_root"] = str(path.parent.resolve(strict=True))
    return plan, _RunState(state_path, state_value)


def _process_preflight() -> list[dict[str, Any]]:
    if platform.system() != "Windows":
        raise RunnerError("native execute is supported only on Windows")
    helper_spec_name = "w21_windows_preflight_helper"
    import importlib.util
    spec = importlib.util.spec_from_file_location(helper_spec_name, PROCESS_HELPER)
    if spec is None or spec.loader is None:
        raise RunnerError("fixed Windows process preflight helper could not be imported")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rows = module.assert_no_existing_comsol_processes()
    # Only safe, allowlisted process fields are kept in receipts.
    output = []
    for row in rows:
        if (not isinstance(row, Mapping) or type(row.get("process_id")) is not int
                or not isinstance(row.get("name"), str)
                or type(row.get("path_missing")) is not bool
                or type(row.get("command_line_missing")) is not bool):
            raise RunnerError("fixed process preflight returned an invalid safe projection")
        output.append({key: row[key] for key in (
            "process_id", "name", "path_missing", "command_line_missing")})
    return output


async def _execute_stdio(plan: dict[str, Any], state: _RunState) -> dict[str, Any]:
    if platform.system() != "Windows":
        raise RunnerError("execute refuses non-Windows hosts before any MCP process is started")
    if os.environ.get("COMSOL_MCP_HOST_CONTROL", "").casefold() not in {"1", "true", "yes"}:
        raise RunnerError("COMSOL_MCP_HOST_CONTROL grant must be explicitly present in the parent environment")
    if os.environ.get("COMSOL_MCP_TRUSTED_CODE", "").casefold() not in {"1", "true", "yes"}:
        raise RunnerError("COMSOL_MCP_TRUSTED_CODE grant must be explicitly present in the parent environment")

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    source_root = Path(plan["source_root"]).resolve(strict=True)
    run_root = Path(plan["run_root"])
    for dirname in ("stdio-home", "server-home", "scratch", "prefs"):
        (run_root / dirname).mkdir(mode=0o700, exist_ok=True)
    env_names = ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "USERPROFILE", "APPDATA", "LOCALAPPDATA")
    child_env = {name: os.environ[name] for name in env_names if name in os.environ}
    child_env.update({
        "COMSOL_ROOT": plan["selected_comsol"]["root"],
        "COMSOL_SERVER_VERSION": plan["selected_comsol"]["version"],
        "JAVA_HOME": plan["selected_jdk"]["home"],
        "COMSOL_JAVA_HOME": plan["selected_jdk"]["home"],
        "COMSOL_SERVER_MCP_HOME": str(run_root / "server-home"),
        "COMSOL_PROJECT_ROOT": str(Path(plan["project_workspace"]).parent),
        "COMSOL_PREFS_DIR": str(run_root / "prefs"),
        "COMSOL_MCP_ISOLATION_RECEIPT": plan["isolation_receipt"],
        "COMSOL_MCP_TOOL_PROFILE": "full",
        "COMSOL_MCP_HOST_CONTROL": os.environ["COMSOL_MCP_HOST_CONTROL"],
        "COMSOL_MCP_TRUSTED_CODE": os.environ["COMSOL_MCP_TRUSTED_CODE"],
        "PYTHONPATH": str(source_root), "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1",
    })
    # The subprocess rehashes the whole frozen source closure before and after
    # imports, then proves that the public stdio server came from this checkout.
    bootstrap = textwrap.dedent(f"""\
        import hashlib, json, sys
        from pathlib import Path
        root = Path({str(source_root)!r})
        plan_path = Path({str(Path(plan['run_root']) / 'freeze.json')!r})
        plan = json.loads(plan_path.read_text(encoding='utf-8'))
        def hash_file(relative):
            digest = hashlib.sha256()
            with (root / relative).open('rb') as stream:
                for block in iter(lambda: stream.read(1048576), b''):
                    digest.update(block)
            return digest.hexdigest()
        def check_manifest():
            observed = {{name: hash_file(name) for name in plan['source_manifest']}}
            encoded = json.dumps(observed, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
            return observed == plan['source_manifest'] and hashlib.sha256(encoded).hexdigest() == plan['source_manifest_sha256']
        if not check_manifest():
            raise SystemExit('frozen source closure changed before stdio import')
        sys.path.insert(0, str(root))
        import comsol_mcp
        if Path(comsol_mcp.__file__).resolve() != root / 'comsol_mcp/__init__.py':
            raise SystemExit('stdio child imported comsol_mcp from a different source')
        import comsol_mcp.mcp_server as server
        if not check_manifest():
            raise SystemExit('frozen source closure changed during stdio import')
        server.main()
    """)
    server = StdioServerParameters(
        command=sys.executable, args=["-B", "-c", bootstrap], env=child_env,
        cwd=str(source_root),
    )
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await run_metadata_protocol(_MCPCalls(session), plan, state,
                                               preflight=_process_preflight)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prep = commands.add_parser("prepare", help="freeze source/runtime identity; never launches MCP or COMSOL")
    prep.add_argument("--version", choices=("6.4", "6.3"), required=True)
    prep.add_argument("--comsol-root", type=Path, required=True)
    prep.add_argument("--jdk-home", type=Path, required=True)
    prep.add_argument("--evidence-root", type=Path, required=True)
    prep.add_argument("--source-root", type=Path, default=REPOSITORY)
    prep.add_argument("--prerequisite-64-receipt", type=Path)
    run = commands.add_parser("execute", help="one explicitly frozen metadata-only field probe")
    run.add_argument("--plan", type=Path, required=True)
    run.add_argument("--freeze-sha256", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "prepare":
            result = prepare(version=args.version, comsol_root=args.comsol_root,
                             jdk_home=args.jdk_home, evidence_root=args.evidence_root,
                             source_root=args.source_root,
                             prerequisite_64_receipt=args.prerequisite_64_receipt)
        else:
            plan, state = _load_plan(args.plan, args.freeze_sha256)
            result = asyncio.run(_execute_stdio(plan, state))
        print(json.dumps({key: value for key, value in result.items()
                          if key not in {"plan", "probe_readback", "fixture_readback"}},
                         ensure_ascii=False, sort_keys=True, allow_nan=False))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "FAILED" if not isinstance(exc, RunnerError) else "REFUSED_OR_FAILED",
                          "error_type": type(exc).__name__,
                          "message": (str(exc)[:500] if isinstance(exc, RunnerError)
                                      else "unexpected local failure; inspect the durable run state")},
                         ensure_ascii=False, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
