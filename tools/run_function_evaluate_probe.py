"""Bounded Windows COMSOL native probe for the managed function.evaluate route.

Run once per COMSOL version in a new task-owned work directory. The existing
W21 launcher provides the already-verified private tree and loopback server
pattern; this runner only exercises a fresh model and the function.evaluate
read path. It contains no Desktop/UI automation and no study solve/run call.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import hashlib
import importlib.util
import json
import math
import os
import platform
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


RUN_BUDGET_S = 15 * 60
RPC_TIMEOUT_S = 300


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_write(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str, allow_nan=False) + "\n", encoding="utf-8")


def load_server_class(w21_runner: Path):
    spec = importlib.util.spec_from_file_location("function_evaluate_w21_helpers", w21_runner)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import the verified W21 server helper: {w21_runner}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.LiveComsolServerInstance


def assert_no_existing_comsol_processes() -> list[str]:
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process | Where-Object {$_.Name -in @('comsolmphserver.exe','comsol.exe','comsolbatch.exe')} | ForEach-Object {$_.ProcessId.ToString() + '|' + $_.Name + '|' + $_.ExecutablePath}"],
        check=True, capture_output=True, text=True, timeout=15,
    )
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip() and not line.startswith("#< CLIXML")]
    if lines:
        raise RuntimeError("pre-existing COMSOL processes detected; left untouched: " + " ; ".join(lines))
    return lines


def cim_process(pid: int) -> dict[str, Any] | None:
    command = [
        "powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
        f"Get-CimInstance Win32_Process -Filter 'ProcessId = {pid}' | Select-Object ProcessId,ExecutablePath,CommandLine,CreationDate | ConvertTo-Json -Compress",
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=15, check=False)
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        value = json.loads(result.stdout.strip())
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        return None


def owned_loopback_listener(port: int, pid: int) -> dict[str, Any]:
    output = subprocess.run(["netstat.exe", "-ano", "-p", "tcp"], check=True, capture_output=True, text=True, timeout=15).stdout
    matching = []
    for line in output.splitlines():
        fields = line.split()
        if len(fields) >= 5 and fields[0].upper() == "TCP" and fields[1].endswith(f":{port}") and fields[-1] == str(pid) and fields[3].upper() == "LISTENING":
            matching.append(line.strip())
    if len(matching) != 1 or not matching[0].split()[1].startswith("127.0.0.1:"):
        raise RuntimeError(f"expected exactly one task-owned loopback listener for pid={pid}, port={port}; got {matching}")
    return {"netstat_row": matching[0], "address": "127.0.0.1", "port": port, "pid": pid}


def resolve_envelope(result: Any) -> dict[str, Any]:
    data = getattr(result, "structuredContent", None)
    if isinstance(data, dict):
        return data
    content = getattr(result, "content", None) or []
    if content and getattr(content[0], "text", None):
        return json.loads(content[0].text)
    raise RuntimeError("MCP response did not contain a structured result")


def function_path(tag: str, component: str | None = None) -> dict[str, Any]:
    segments = []
    if component is not None:
        segments.append({"collection": "component", "tag": component})
    segments.append({"collection": "func", "tag": tag})
    return {"segments": segments}


def within_frozen_tolerance(actual: float, expected: float) -> bool:
    return abs(actual - expected) <= 1.0e-10 + 1.0e-12 * abs(expected)


def check_complex(value: Any, expected: tuple[float, float]) -> dict[str, Any]:
    if isinstance(value, dict) and isinstance(value.get("real"), (int, float)) and isinstance(value.get("imag"), (int, float)):
        actual = (float(value["real"]), float(value["imag"]))
        representation = "versioned_real_imag_object"
    elif isinstance(value, list) and len(value) == 2 and all(isinstance(item, (int, float)) for item in value):
        actual = (float(value[0]), float(value[1]))
        representation = "raw_COMSOL_evaluateComplex_pair"
    else:
        return {"status": "FAIL", "reason": "value is not a supported finite complex pair"}
    if not all(math.isfinite(item) for item in actual):
        return {"status": "FAIL", "reason": "complex pair contains a non-finite scalar", "representation": representation}
    ok = all(within_frozen_tolerance(a, e) for a, e in zip(actual, expected))
    return {"status": "PASS" if ok else "FAIL", "actual": list(actual), "expected": list(expected),
            "representation": representation,
            "tolerance": "abs(error) <= 1e-10 + 1e-12*abs(expected)"}


async def run(args: argparse.Namespace) -> int:
    root = Path(args.work).resolve()
    root.mkdir(parents=True, exist_ok=False)
    for name in ("runtime", "control", "project", "logs"):
        (root / name).mkdir(parents=True, exist_ok=True)
    fixture = Path(args.fixture).resolve()
    runner_path = Path(args.w21_runner).resolve()
    helper_type = load_server_class(runner_path)
    summary: dict[str, Any] = {
        "schema_version": "function-evaluate-windows-native-run/1.0.0",
        "status": "PREPARED",
        "comsol_version_requested": args.version,
        "comsol_root": args.comsol,
        "jdk_home": args.jdk,
        "fixture_sha256": sha256(fixture),
        "w21_helper_sha256": sha256(runner_path),
        "python_executable": sys.executable,
        "work_root": str(root),
        "no_solve_policy": {"study_solve_calls": 0, "study_run_calls": 0, "gui": False},
    }
    summary["windows_os"] = {"platform": platform.platform(), "version": platform.version(), "release": platform.release()}
    jdk_home = Path(args.jdk)
    jdk_release = {}
    release_path = jdk_home / "release"
    if release_path.is_file():
        for line in release_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                jdk_release[key] = value.strip().strip('"')
    summary["jdk_release"] = jdk_release
    for name in ("java", "javac"):
        executable = jdk_home / "bin" / f"{name}.exe"
        result = subprocess.run([str(executable), "-version"], capture_output=True, text=True, timeout=10, check=False)
        summary[f"{name}_version_output"] = (result.stdout + result.stderr).strip()
        summary[f"{name}_sha256"] = sha256(executable) if executable.is_file() else None
    transcript = root / "transcript.jsonl"
    request_history: list[dict[str, Any]] = []

    def log(row: dict[str, Any]) -> None:
        with transcript.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, ensure_ascii=False, default=str, allow_nan=False) + "\n")

    existing = assert_no_existing_comsol_processes()
    summary["preexisting_comsol_processes"] = existing
    server = helper_type(args.version, Path(args.comsol), Path(args.jdk), root / "runtime")
    from comsol_mcp._java_worker import JavaWorkerPaths
    worker_paths = JavaWorkerPaths(Path(args.comsol), Path(args.jdk), private_prefs=server.prefs_dir, project_root=root)
    _, classpath_manifest_hash, classpath_entries, classpath_content_hash = worker_paths.classpath()
    summary["comsol_api_classpath"] = {
        "manifest_sha256": classpath_manifest_hash,
        "entry_count": classpath_entries,
        "content_binding_sha256": classpath_content_hash,
        "selected_root": "apiplugins-first complete official manifest set",
    }
    server_executable = Path(args.comsol) / "bin" / "win64" / "comsolmphserver.exe"
    file_info_command = [
        "powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
        f"(Get-Item -LiteralPath '{server_executable}').VersionInfo | Select-Object FileVersion,ProductVersion,CompanyName | ConvertTo-Json -Compress",
    ]
    file_info = subprocess.run(file_info_command, capture_output=True, text=True, timeout=15, check=False)
    if file_info.returncode == 0 and file_info.stdout.strip():
        try:
            summary["server_binary_version_info"] = json.loads(file_info.stdout.strip())
        except json.JSONDecodeError:
            summary["server_binary_version_info"] = {"raw": file_info.stdout.strip()}
    else:
        summary["server_binary_version_info"] = {"status": "UNAVAILABLE", "stderr": file_info.stderr.strip()}
    started = time.monotonic()
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    supplied_budget_origin = (
        datetime.datetime.fromisoformat(args.budget_origin_utc.replace("Z", "+00:00"))
        if args.budget_origin_utc else None
    )
    budget_origin = supplied_budget_origin
    already_used = max(0.0, (now_utc - budget_origin).total_seconds()) if budget_origin else 0.0
    budget_remaining = max(0.0, RUN_BUDGET_S - already_used)
    summary["native_budget_started_utc"] = budget_origin.isoformat().replace("+00:00", "Z") if budget_origin else None
    summary["native_budget_origin_source"] = "frozen_first_engine_birth" if budget_origin else "first_owned_engine_birth_pending"
    summary["attempt_started_utc"] = now_utc.isoformat().replace("+00:00", "Z")
    summary["native_budget_seconds"] = RUN_BUDGET_S
    summary["budget_already_used_seconds_at_attempt_start"] = round(already_used, 3)
    summary["budget_remaining_seconds_at_attempt_start"] = round(budget_remaining, 3)
    deadline = started + budget_remaining

    try:
        if supplied_budget_origin is not None and budget_remaining <= 0:
            raise TimeoutError("frozen 15 minute engine-birth budget is already exhausted; no retry was started")
        port = server.start()
        if supplied_budget_origin is not None and time.monotonic() > deadline:
            raise TimeoutError("retry attempt exceeded the remaining frozen 15 minute budget during startup")
        assert server.proc is not None and server.proc.pid is not None
        runtime_root = root / "runtime"
        process_image = str(server.proc.args[0])
        expected_launcher = runtime_root / "engine" / "bin" / "win64" / "comsolmphserver.exe"
        lexical_image = os.path.normcase(os.path.abspath(process_image))
        lexical_expected = os.path.normcase(os.path.abspath(str(expected_launcher)))
        summary["server_pid"] = server.proc.pid
        summary["server_launch_executable_argument"] = process_image
        summary["expected_private_launcher_path"] = str(expected_launcher)
        summary["launch_argument_matches_task_owned_private_path"] = lexical_image == lexical_expected
        summary["actual_process_cim"] = cim_process(server.proc.pid)
        from comsol_mcp._platform_process import process_identity
        summary["server_pid_birth_identity_before_sampling"] = process_identity(server.proc.pid, platform_name="nt")
        server_start_epoch_ms = summary["server_pid_birth_identity_before_sampling"].get("start_epoch_ms")
        if not isinstance(server_start_epoch_ms, int):
            raise RuntimeError("owned COMSOL engine process birth time could not be verified")
        server_start_utc = datetime.datetime.fromtimestamp(server_start_epoch_ms / 1000.0, tz=datetime.timezone.utc)
        summary["owned_server_process_start_utc"] = server_start_utc.isoformat().replace("+00:00", "Z")
        if budget_origin is None:
            budget_origin = server_start_utc
            summary["native_budget_started_utc"] = budget_origin.isoformat().replace("+00:00", "Z")
            summary["native_budget_origin_source"] = "owned_server_process_birth_identity"
            already_used = max(0.0, (datetime.datetime.now(datetime.timezone.utc) - budget_origin).total_seconds())
            budget_remaining = max(0.0, RUN_BUDGET_S - already_used)
            deadline = time.monotonic() + budget_remaining
            summary["budget_already_used_seconds_at_attempt_start"] = round(already_used, 3)
            summary["budget_remaining_seconds_at_attempt_start"] = round(budget_remaining, 3)
        elif time.monotonic() > deadline:
            raise TimeoutError("retry attempt reached the frozen 15 minute budget during startup")
        summary["owned_engine_budget_deadline_utc"] = (
            budget_origin + datetime.timedelta(seconds=RUN_BUDGET_S)
        ).isoformat().replace("+00:00", "Z")
        summary["runner_conservative_deadline_utc"] = (
            budget_origin + datetime.timedelta(seconds=RUN_BUDGET_S)
        ).isoformat().replace("+00:00", "Z")
        summary["engine_budget_remaining_seconds_at_identity"] = round(
            RUN_BUDGET_S - (datetime.datetime.now(datetime.timezone.utc) - budget_origin).total_seconds(), 3
        )
        worker_pid_before_disconnect = None
        if server.worker is not None:
            initial_worker_proc = getattr(server.worker, "proc", None)
            worker_pid_before_disconnect = getattr(initial_worker_proc, "pid", None)
        summary["initial_java_worker_pid"] = worker_pid_before_disconnect
        summary["initial_java_worker_identity_before_disconnect"] = (
            process_identity(worker_pid_before_disconnect, platform_name="nt")
            if isinstance(worker_pid_before_disconnect, int) else None
        )
        private_runtime_path = root / "runtime" / "private_runtime.json"
        summary["private_runtime"] = json.loads(private_runtime_path.read_text(encoding="utf-8")) if private_runtime_path.is_file() else None
        junction_path = root / "runtime" / "engine" / "bin" / "win64"
        junction = subprocess.run(["fsutil.exe", "reparsepoint", "query", str(junction_path)], capture_output=True,
                                  text=True, timeout=15, check=False)
        summary["private_win64_junction_evidence"] = {
            "path": str(junction_path), "returncode": junction.returncode,
            "stdout": junction.stdout.strip(), "stderr": junction.stderr.strip(),
        }
        if not summary["launch_argument_matches_task_owned_private_path"]:
            raise RuntimeError(f"server executable is outside the task-owned engine tree: {process_image}")
        summary["port"] = port
        summary["listener"] = owned_loopback_listener(port, server.proc.pid)
        summary["engine_version"] = server.worker.client().getComsolVersion()
        summary["isolation_receipt"] = json.loads(Path(server.receipt_file).read_text(encoding="utf-8")) if getattr(server, "receipt_file", None) else None
        server.stop_worker()
        summary["initial_java_worker_identity_after_disconnect"] = (
            process_identity(worker_pid_before_disconnect, platform_name="nt")
            if isinstance(worker_pid_before_disconnect, int) else None
        )

        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        env.update(
            COMSOL_SERVER_MCP_HOME=str(root / "control"),
            COMSOL_ROOT=args.comsol,
            COMSOL_PREFS_DIR=str(root / "runtime" / "prefs"),
            COMSOL_PROJECT_ROOT=str(root / "project"),
            COMSOL_MCP_ISOLATION_RECEIPT=str(getattr(server, "receipt_file", "")),
            COMSOL_MCP_TRUSTED_CODE="1",
            COMSOL_JAVA_HOME=args.jdk,
            JAVA_HOME=args.jdk,
            COMSOL_SERVER_VERSION=args.version,
        )
        local_source = Path(args.source_root).resolve()
        env["PYTHONPATH"] = str(local_source)
        fixture_copy = root / "project" / "FunctionEvaluateProbe.java"
        fixture_copy.write_bytes(fixture.read_bytes())
        app = Path(sys.executable).parent / "comsol-mcp.exe"
        if not app.is_file():
            raise RuntimeError(f"task-local MCP console script is absent: {app}")
        params = StdioServerParameters(command=str(app), args=[], env=env, cwd=str(root))

        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                binding: dict[str, Any] = {}
                last_revision: Any = None

                async def call(tool: str, body: dict[str, Any], *, bound: bool = True, allow_error: bool = False) -> dict[str, Any]:
                    nonlocal last_revision
                    if time.monotonic() >= deadline:
                        raise TimeoutError("native run reached the frozen 15 minute budget before dispatch")
                    execution = dict(binding) if bound else {}
                    execution.update(project_id="function-evaluate-native", rpc_timeout_s=RPC_TIMEOUT_S, execution_timeout_s=None)
                    request = dict(body, execution=execution)
                    revision_before = last_revision
                    log({"direction": "request", "tool": tool, "arguments": request})
                    request_history.append({"tool": tool, "arguments": request})
                    remaining = max(0.1, min(RPC_TIMEOUT_S, deadline - time.monotonic()))
                    try:
                        result = await asyncio.wait_for(session.call_tool(tool, arguments=request), timeout=remaining)
                    except asyncio.TimeoutError as exc:
                        raise TimeoutError(f"unobserved native MCP response for {tool}; worker state must remain UNKNOWN") from exc
                    envelope = resolve_envelope(result)
                    log({"direction": "response", "tool": tool, "result": envelope})
                    execution_result = envelope.get("execution", {}) if isinstance(envelope.get("execution"), dict) else {}
                    if execution_result.get("model_ref"):
                        binding.update(
                            model_ref=execution_result["model_ref"],
                            expected_revision=execution_result.get("revision"),
                            session_id=execution_result["model_ref"].get("session_id"),
                        )
                    revision = execution_result.get("revision")
                    if revision is not None:
                        last_revision = revision
                        envelope["_runner_recorded_revision"] = revision
                    failed = bool(getattr(result, "isError", False) or envelope.get("success") is False)
                    error = envelope.get("error", {}) if isinstance(envelope.get("error"), dict) else {}
                    data = envelope.get("data", {}) if isinstance(envelope.get("data"), dict) else {}
                    if (error.get("code") == "EXECUTION_STATE_UNKNOWN"
                            or data.get("execution_state_unknown") is True):
                        raise TimeoutError(
                            f"{tool} returned an explicit unknown execution state; stop issuing follow-up requests"
                        )
                    if failed:
                        is_function_evaluate = (
                            tool == "operation_call" and body.get("operation_id") == "function.evaluate"
                        )
                        witness = data.get("witness") if isinstance(data.get("witness"), dict) else {}
                        dispatches = witness.get("dispatches") if isinstance(witness.get("dispatches"), list) else []
                        safe_validation_refusal = (
                            is_function_evaluate
                            and error.get("code") == "API_UNSUPPORTED"
                            and error.get("stage") == "validation"
                            and data.get("status") == "REFUSED"
                            and data.get("partial_change") is False
                            and data.get("execution_state_unknown") is False
                            and isinstance(witness.get("engine_calls"), int)
                            and witness["engine_calls"] > 0
                            and witness.get("mutation_issued") is False
                            and witness.get("mutation_method") is None
                            and len(dispatches) == witness.get("engine_calls")
                            and all(isinstance(row, dict) and row.get("is_mutation") is False for row in dispatches)
                            and revision_before is not None
                            and revision == revision_before
                            and execution_result.get("dirty") is False
                        )
                        if safe_validation_refusal:
                            envelope["_runner_safe_terminal_api_unsupported"] = True
                        else:
                            safe_completed_sample_failure = (
                                allow_error
                                and is_function_evaluate
                                and data.get("status") == "FAILED"
                                and data.get("partial_change") is False
                                and data.get("execution_state_unknown") is False
                                and isinstance(data.get("results"), list)
                                and any(
                                    isinstance(row, dict)
                                    and row.get("value_status") == "EVALUATION_ERROR"
                                    for row in data["results"]
                                )
                                and revision_before is not None
                                and revision == revision_before
                                and execution_result.get("dirty") is False
                            )
                            if safe_completed_sample_failure:
                                envelope["_runner_completed_sample_failure"] = True
                            elif allow_error:
                                raise RuntimeError(
                                    f"{tool} failure was not a completed, revision-stable sample error or a proven "
                                    f"validation refusal: {json.dumps(envelope, ensure_ascii=False, default=str)[:3000]}"
                                )
                            else:
                                raise RuntimeError(
                                    f"{tool} returned failure: {json.dumps(envelope, ensure_ascii=False, default=str)[:3000]}"
                                )
                    return envelope

                summary["server_connect"] = await call("server_connect", {"host": "127.0.0.1", "port": port}, bound=False)
                summary["model_create"] = await call("model_create", {"name": "FunctionEvaluateNativeOwned"}, bound=False)
                fixture_revision_before = last_revision
                fixture_result = await call("operation_call", {
                    "operation_id": "code.execute_java",
                    "arguments": {"source_artifact": str(fixture_copy), "entrypoint": "FunctionEvaluateProbe", "mode": "trusted", "arguments": {"mode": "prepare"}},
                })
                summary["direct_api_probe"] = fixture_result
                summary["fixture_setup_and_temporary_derivative_revision_transition"] = {
                    "revision_before": fixture_revision_before,
                    "revision_after": fixture_result.get("_runner_recorded_revision"),
                    "revision_advanced": (
                        isinstance(fixture_revision_before, int)
                        and isinstance(fixture_result.get("_runner_recorded_revision"), int)
                        and fixture_result.get("_runner_recorded_revision") > fixture_revision_before
                    ),
                    "execution_state_unknown": fixture_result.get("data", {}).get("execution_state_unknown")
                    if isinstance(fixture_result.get("data"), dict) else None,
                    "cleanup_failed": fixture_result.get("data", {}).get("cleanup_failed")
                    if isinstance(fixture_result.get("data"), dict) else None,
                }
                fixture_data = fixture_result.get("data", {}) if isinstance(fixture_result.get("data"), dict) else {}
                # code.execute_java returns its trusted method value under
                # data.readback.readback. Keep compatibility with either a
                # direct payload or that managed envelope shape.
                fixture_wrapper = fixture_data.get("readback", {}) if isinstance(fixture_data.get("readback"), dict) else {}
                fixture_payload = fixture_wrapper.get("readback", fixture_wrapper)
                if not isinstance(fixture_payload, dict):
                    fixture_payload = {}
                direct_samples = {row.get("case"): row for row in fixture_payload.get("direct_samples", []) if isinstance(row, dict)}
                direct_expected = {
                    "global_shared_2_3": (32.0, 0.0),
                    "component_shadow_shared_2_3": (132.0, 0.0),
                    "complex_h_2": (4.0, 6.0),
                    "rate_m_s": (2.0 / 3.0, 0.0),
                    "rate_cm_ms": (2.0 / 3.0, 0.0),
                    "interp_none_interior": (5.0, 0.0),
                    "interp_none_left_endpoint": (0.0, 0.0),
                    "interp_none_right_endpoint": (10.0, 0.0),
                    "interp_linear_right_outside": (11.0, 0.0),
                }
                summary["direct_api_numeric_checks"] = {
                    label: check_complex(direct_samples.get(label, {}).get("evaluateComplex"), expected)
                    for label, expected in direct_expected.items()
                }
                summary["direct_api_non_extrapolating_range_error_checks"] = {}
                for label in ("interp_none_left_outside", "interp_none_right_outside"):
                    row = direct_samples.get(label, {})
                    observed = (
                        row.get("value_status") == "ERROR"
                        and row.get("value_error") == "Interpolation_function_is_out_of_range"
                        and row.get("unit_status") == "OBSERVED"
                    )
                    summary["direct_api_non_extrapolating_range_error_checks"][label] = {
                        "status": "PASS" if observed else "FAIL",
                        "value_status": row.get("value_status"),
                        "value_error": row.get("value_error"),
                        "unit_status": row.get("unit_status"),
                        "expected_error": "Interpolation_function_is_out_of_range",
                    }
                summary["direct_api_snapshot_unchanged"] = (
                    fixture_payload.get("snapshot_before_direct_sampling") == fixture_payload.get("snapshot_after_direct_sampling")
                    and fixture_payload.get("snapshot_before_direct_sampling") is not None
                )
                derivative_rows = [row for row in fixture_payload.get("derivative_candidates", []) if isinstance(row, dict)]
                derivative_expected = {"dx": (17.0, 0.0), "dy": (36.0, 0.0), "dxx": (4.0, 0.0), "dyy": (10.0, 0.0)}
                derivative_checks: dict[str, Any] = {}
                for candidate in derivative_rows:
                    label = candidate.get("case")
                    expected = derivative_expected.get(label)
                    check = check_complex(candidate.get("evaluateComplex"), expected) if expected is not None else {
                        "status": "FAIL", "reason": "unrecognized or missing derivative candidate"
                    }
                    check["probe_status"] = candidate.get("probe_status")
                    check["value_status"] = candidate.get("value_status")
                    check["cleanup_status"] = candidate.get("cleanup_status")
                    check["function_tag_absent_after_cleanup"] = candidate.get("function_tag_absent_after_cleanup")
                    if candidate.get("probe_status") != "EVALUATED" or candidate.get("value_status") != "OBSERVED":
                        check["status"] = "FAIL"
                    if candidate.get("function_tag_absent_after_cleanup") is not True:
                        check["status"] = "FAIL"
                    derivative_checks[str(label)] = check
                summary["temporary_derivative_numeric_checks"] = derivative_checks
                summary["temporary_derivative_snapshots_restored"] = (
                    fixture_payload.get("snapshot_before_derivative_candidates")
                    == fixture_payload.get("snapshot_after_derivative_candidates")
                    and fixture_payload.get("snapshot_before_derivative_candidates") is not None
                )
                summary["temporary_derivative_cleanup_verified"] = (
                    fixture_payload.get("all_derivative_temporaries_removed") is True
                    and len(derivative_rows) == 4
                    and all(row.get("function_tag_absent_after_cleanup") is True for row in derivative_rows
                            if row.get("probe_status") != "NOT_RUN")
                )
                derivative_api_error = any(
                    row.get("probe_status") == "PROBE_FAILED_API_UNSUPPORTED" or row.get("value_status") == "ERROR"
                    for row in derivative_rows
                )
                derivative_api_pass = (
                    len(derivative_checks) == 4
                    and all(row.get("status") == "PASS" for row in derivative_checks.values())
                    and summary["temporary_derivative_snapshots_restored"] is True
                    and summary["temporary_derivative_cleanup_verified"] is True
                )
                summary["derivative_candidate_probe_status"] = (
                    "PROBE_FAILED_API_UNSUPPORTED" if derivative_api_error
                    else "PROBED_NUMERIC_PASS_API_FEASIBILITY_ONLY" if derivative_api_pass
                    else "PROBE_INCOMPLETE_OR_NUMERIC_MISMATCH"
                )
                if summary["temporary_derivative_cleanup_verified"] is not True:
                    raise RuntimeError("temporary derivative feature cleanup was not verified; stop before production sampling")
                if summary["temporary_derivative_snapshots_restored"] is not True:
                    raise RuntimeError("temporary derivative candidates changed the frozen model snapshot; stop before production sampling")
                summary["mcp_revision_before_function_evaluate"] = last_revision
                calls = [
                    ("global_shared_2_3", function_path("fe_global"), [{"coordinate": [2, 3], "units": ["1", "1"]}], None),
                    ("component_shadow_shared_2_3", function_path("fe_local", "fe_component"), [{"coordinate": [2, 3], "units": ["1", "1"]}], None),
                    ("complex_h_2", function_path("fe_complex"), [{"value": 2, "unit": "1"}], None),
                    ("rate_m_s", function_path("fe_rate"), [{"coordinate": [2, 3], "units": ["m", "s"]}], None),
                    ("rate_cm_ms", function_path("fe_rate"), [{"coordinate": [200, 3000], "units": ["cm", "ms"]}], None),
                    ("interp_none_interior", function_path("fe_interp_none"), [{"value": 0.5, "unit": "1"}], None),
                    ("interp_none_left_endpoint", function_path("fe_interp_none"), [{"value": 0, "unit": "1"}], None),
                    ("interp_none_right_endpoint", function_path("fe_interp_none"), [{"value": 1, "unit": "1"}], None),
                    ("interp_linear_right_outside", function_path("fe_interp_linear"), [{"value": 1.1, "unit": "1"}], None),
                    # Known no-extrapolation error cases are deliberately last.
                    # If the Worker cannot classify a terminal COMSOL error,
                    # preserving UNKNOWN must not prevent collection of the
                    # independent successful values above.
                    ("interp_none_left_outside", function_path("fe_interp_none"), [{"value": -0.1, "unit": "1"}], True),
                    ("interp_none_right_outside", function_path("fe_interp_none"), [{"value": 1.1, "unit": "1"}], True),
                ]
                public_results: list[dict[str, Any]] = []
                for label, path, sample_arguments, allow in calls:
                    before = last_revision
                    result = await call("operation_call", {
                        "operation_id": "function.evaluate",
                        "arguments": {"path": path, "arguments": sample_arguments},
                    }, allow_error=bool(allow))
                    after = result.get("_runner_recorded_revision", last_revision)
                    data = result.get("data", {}) if isinstance(result.get("data"), dict) else {}
                    row = {"case": label, "response": result, "data": data, "revision_before": before, "revision_after": after}
                    public_results.append(row)
                summary["public_function_evaluate_results"] = public_results
                summary["mcp_revision_after_function_evaluate"] = last_revision
                summary["mcp_revision_stable_during_sampling"] = bool(public_results) and all(
                    row["revision_before"] is not None and row["revision_after"] is not None
                    and row["revision_before"] == row["revision_after"] for row in public_results
                )
                managed_checks: dict[str, Any] = {}
                expected_managed = {
                    "global_shared_2_3": (32.0, 0.0),
                    "component_shadow_shared_2_3": (132.0, 0.0),
                    "complex_h_2": (4.0, 6.0),
                    "rate_m_s": (2.0 / 3.0, 0.0),
                    "rate_cm_ms": (2.0 / 3.0, 0.0),
                    "interp_none_interior": (5.0, 0.0),
                    "interp_none_left_endpoint": (0.0, 0.0),
                    "interp_none_right_endpoint": (10.0, 0.0),
                    "interp_linear_right_outside": (11.0, 0.0),
                }
                for row in public_results:
                    label = row["case"]
                    envelope = row["response"]
                    data = row["data"]
                    if label in expected_managed:
                        result_rows = data.get("results", []) if isinstance(data, dict) else []
                        first = result_rows[0] if result_rows and isinstance(result_rows[0], dict) else {}
                        check = check_complex(first.get("value"), expected_managed[label])
                        check["envelope_success"] = envelope.get("success") is True
                        check["action_status"] = data.get("status") if isinstance(data, dict) else None
                        check["range_status"] = first.get("range_status")
                        check["partial_change"] = data.get("partial_change")
                        check["execution_state_unknown"] = data.get("execution_state_unknown")
                        if (envelope.get("success") is not True or data.get("status") != "OBSERVED"
                                or first.get("range_status") != "UNKNOWN" or data.get("partial_change") is not False
                                or data.get("execution_state_unknown") is not False):
                            check["status"] = "FAIL"
                            check["reason"] = "outer success, OBSERVED completion, or UNKNOWN range contract failed"
                        if envelope.get("_runner_safe_terminal_api_unsupported") is True:
                            check["status"] = "FAIL"
                            check["failure_class"] = "API_UNSUPPORTED"
                            check["reason"] = envelope.get("error", {}).get("message", "API unsupported")
                        managed_checks[label] = check
                    elif label.startswith("interp_none_") and label.endswith("_outside"):
                        result_rows = data.get("results", []) if isinstance(data, dict) else []
                        first = result_rows[0] if result_rows and isinstance(result_rows[0], dict) else {}
                        observed_completed_error = (
                            envelope.get("success") is False
                            and envelope.get("_runner_completed_sample_failure") is True
                            and envelope.get("error", {}).get("code") != "EXECUTION_STATE_UNKNOWN"
                            and data.get("status") == "FAILED"
                            and first.get("value_status") == "EVALUATION_ERROR"
                            and first.get("range_status") == "UNKNOWN"
                            and data.get("partial_change") is False
                            and data.get("execution_state_unknown") is False
                            and data.get("sample_completion") == "FAILED"
                            and data.get("sample_failure_count") == 1
                            and "Interpolation_function_is_out_of_range" in json.dumps(first.get("errors", []), ensure_ascii=False)
                        )
                        status = "PASS" if observed_completed_error else "FAIL"
                        failure_class = None
                        if envelope.get("_runner_safe_terminal_api_unsupported") is True:
                            status = "FAIL"
                            failure_class = "API_UNSUPPORTED"
                        managed_checks[label] = {"status": status, "outer_success": envelope.get("success"),
                                                 "range_status": first.get("range_status"), "error": envelope.get("error"),
                                                 "failure_class": failure_class}
                summary["managed_api_contract_checks"] = managed_checks

                summary["derivative_api_response"] = await call("operation_call", {
                    "operation_id": "function.evaluate",
                    "arguments": {"path": function_path("fe_q"), "arguments": [{"coordinate": [2, 3], "units": ["1", "1"]}],
                                  "derivative": {"order": 1, "argument_index": 0}},
                }, allow_error=True)
                snapshot = await call("operation_call", {
                    "operation_id": "code.execute_java",
                    "arguments": {"source_artifact": str(fixture_copy), "entrypoint": "FunctionEvaluateProbe", "mode": "trusted", "arguments": {"mode": "snapshot_only"}},
                })
                summary["readback_snapshot_after_public_sampling"] = snapshot
                final_fixture_data = snapshot.get("data", {}) if isinstance(snapshot.get("data"), dict) else {}
                snapshot_wrapper = final_fixture_data.get("readback", {}) if isinstance(final_fixture_data.get("readback"), dict) else {}
                snapshot_payload = snapshot_wrapper.get("readback", snapshot_wrapper)
                if not isinstance(snapshot_payload, dict):
                    snapshot_payload = {}
                summary["snapshot_unchanged_after_managed_sampling"] = (
                    snapshot_payload.get("snapshot") == fixture_payload.get("snapshot_after_direct_sampling")
                    and fixture_payload.get("snapshot_after_direct_sampling") is not None
                )
                derivative_error = summary["derivative_api_response"].get("error", {})
                summary["production_derivative_status"] = (
                    "NOT_IMPLEMENTED_API_UNSUPPORTED" if derivative_error.get("code") == "API_UNSUPPORTED" else "UNEXPECTED_RESPONSE_REQUIRES_REVIEW"
                )
                summary["requests_sent"] = request_history
                summary["elapsed_seconds"] = round(time.monotonic() - started, 3)
                summary["elapsed_from_original_budget_seconds"] = round(already_used + summary["elapsed_seconds"], 3)
                summary["status"] = "COMPLETED"
                summary["acceptance_status"] = "PASS" if (
                    all(row.get("status") == "PASS" for row in summary["direct_api_numeric_checks"].values())
                    and all(row.get("status") == "PASS" for row in summary["direct_api_non_extrapolating_range_error_checks"].values())
                    and summary["direct_api_snapshot_unchanged"] is True
                    and all(row.get("status") == "PASS" for row in managed_checks.values())
                    and summary["mcp_revision_stable_during_sampling"] is True
                    and summary["snapshot_unchanged_after_managed_sampling"] is True
                    and summary["production_derivative_status"] == "NOT_IMPLEMENTED_API_UNSUPPORTED"
                    and summary["fixture_setup_and_temporary_derivative_revision_transition"].get("revision_advanced") is True
                    and summary["derivative_candidate_probe_status"] == "PROBED_NUMERIC_PASS_API_FEASIBILITY_ONLY"
                ) else "FAIL"
                summary["native_scope"] = "COMSOL server on a task-owned in-memory model; zero solve/run calls"
    except TimeoutError as exc:
        summary["status"] = "EXECUTION_STATE_UNKNOWN"
        summary["error"] = {"type": type(exc).__name__, "message": str(exc)}
        summary["elapsed_seconds"] = round(time.monotonic() - started, 3)
        summary["elapsed_from_original_budget_seconds"] = round(already_used + summary["elapsed_seconds"], 3)
    except BaseException as exc:  # preserve the concrete native failure in the receipt
        summary["status"] = "FAILED"
        summary["error"] = {"type": type(exc).__name__, "message": str(exc)}
        summary["traceback"] = __import__("traceback").format_exc()
        summary["elapsed_seconds"] = round(time.monotonic() - started, 3)
    finally:
        summary["server_exit_requested"] = True
        owned_pid = server.proc.pid if server.proc is not None else summary.get("server_pid")
        server.stop()
        from comsol_mcp._platform_process import process_identity
        summary["server_owned_pid"] = owned_pid
        summary["server_pid_identity_after_stop"] = process_identity(owned_pid, platform_name="nt") if isinstance(owned_pid, int) else None
        initial_worker_pid = summary.get("initial_java_worker_pid")
        summary["initial_java_worker_identity_after_stop"] = process_identity(initial_worker_pid, platform_name="nt") if isinstance(initial_worker_pid, int) else None
        summary["elapsed_seconds"] = round(time.monotonic() - started, 3)
        summary["elapsed_from_original_budget_seconds"] = round(already_used + summary["elapsed_seconds"], 3)
        json_write(root / "summary.json", summary)
    print(json.dumps({"status": summary.get("status"), "work_root": str(root), "engine_version": summary.get("engine_version"),
                      "elapsed_seconds": summary.get("elapsed_seconds"), "error": summary.get("error")}, ensure_ascii=False), flush=True)
    return 0 if summary.get("status") == "COMPLETED" else 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--comsol", required=True)
    parser.add_argument("--jdk", required=True)
    parser.add_argument("--work", required=True)
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--w21-runner", required=True)
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--budget-origin-utc", default="", help="first engine birth for a retry within the same 15-minute version budget")
    args = parser.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
