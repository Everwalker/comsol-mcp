#!/usr/bin/env python3
"""Build, save, reopen and read back the W24 cure coupon on one COMSOL server.

This setup-only preflight performs native geometry/physics/mesh/solver creation,
all required pre-solve readbacks, a complete Solid Mechanics Equation View
inventory, and same-Worker immutable MPH save/reopen verification. Its engine
budget is at most 15 minutes from process birth and it makes zero study.run
submissions. Cleanup uses the W23-reviewed exact-identity TERM-only path. A
later solver campaign needs its own complete freeze and birth.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.metadata
import json
import os
import re
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo


REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
INSTALL_ROOT = Path("/Applications/COMSOL64/Multiphysics")
JAVA_HOME = Path("/Library/Java/JavaVirtualMachines/amazon-corretto-11.jdk/Contents/Home")
JAVAC = JAVA_HOME / "bin/javac"
FIXTURE = REPO / "tools/java/W24CureCouponFixture.java"
PLAN = REPO / "docs/full_project_execution/w24/W24_CURE_FIXTURE_PROPOSAL.md"
MAX_BIRTH_BUDGET_S = 900.0
CLEANUP_RESERVE_S = 45.0
DEFAULT_PIP_CHECK_PYTHON = Path("/opt/homebrew/opt/python@3.12/bin/python3.12")
PROJECT_WORKSPACE_NAME = "science"
TRUSTED_CODE_STARTUP_ENV = "COMSOL_MCP_TRUSTED_CODE"
ISOLATION_RECEIPT_ENV = "COMSOL_MCP_ISOLATION_RECEIPT"
TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED", "EXPIRED", "LOST"}
WORKER_CLEANUP_TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED", "EXPIRED"}
ACTIVE_JOB_STATES = {"QUEUED", "RUNNING", "PENDING", "ACTIVE", "IN_PROGRESS", "SUBMITTED"}
EXPRESSION_CANDIDATE_RULE = (
    "candidate rows have case-insensitive stress/cauchy/shear text or a "
    "solid.s[a-z0-9_]* identifier; optional component cues are "
    "hoop/radial/circumferential/azimuthal; full raw rows are preserved; discovery only"
)
SOURCE_PATHS = {
    "runner": Path(__file__).resolve(),
    "java_fixture": FIXTURE,
    "proposal": PLAN,
    "server_helper": REPO / "tools/run_native_resume_smoke.py",
    "geometry_inventory_helper": REPO / "tools/run_native_artifact_geometry.py",
    "control_daemon": REPO / "comsol_mcp/_control_daemon.py",
    "execution_service": REPO / "comsol_mcp/_execution_service.py",
    "managed_backend": REPO / "comsol_mcp/_managed_backend.py",
    "java_worker": REPO / "comsol_mcp/_java_worker.py",
    "java_worker_source": REPO / "comsol_mcp/worker_java/PersistentComsolWorker.java",
    "study_dispatch": REPO / "comsol_mcp/_g3_w16.py",
    "geometry_dispatch": REPO / "comsol_mcp/_g3_w14.py",
    "operation_store": REPO / "comsol_mcp/_operation_store.py",
    "isolation": REPO / "comsol_mcp/_g2_isolation.py",
    "exact_cleanup_helper": REPO / "tools/run_native_w23_te_managed_preflight.py",
    "science_runner": REPO / "tools/run_native_w24_cure_science.py",
    "science_java_fixture": REPO / "tools/java/W24CureScienceFixture.java",
    "science_runner_tests": REPO / "tests/test_w24_cure_science_runner.py",
    "fixture_acceptance": REPO / "tests/test_w24_cure_coupon_fixture.py",
    "worker_transition_tests": REPO / "tests/test_w24_worker_transition.py",
    "preflight_runner_tests": REPO / "tests/test_w24_cure_preflight_runner.py",
    "science_acceptance": REPO / "tools/w24_science_acceptance.py",
    "science_acceptance_tests": REPO / "tests/test_w24_science_acceptance.py",
    # Bind every direct software gate exercised before the setup-only native
    # candidate to the freeze, including the F03/F04 project and Worker
    # lifecycle contracts that are now part of this runner's production path.
    "runtime_control_tests": REPO / "tests/test_runtime_control.py",
    "g3_runtime_tests": REPO / "tests/test_g3_runtime.py",
    "runtime_installation_tests": REPO / "tests/test_runtime_installation.py",
    "control_daemon_tests": REPO / "tests/test_control_daemon.py",
    "control_daemon_projects_tests": REPO / "tests/test_control_daemon_projects.py",
    "managed_backend_tests": REPO / "tests/test_managed_backend.py",
    "execution_service_tests": REPO / "tests/test_execution_service.py",
    "session_context_tests": REPO / "tests/test_session_context.py",
    "session_lifecycle_tests": REPO / "tests/test_session_lifecycle.py",
    "operation_store_migration_tests": REPO / "tests/test_operation_store_migrations.py",
    "project_authority_tests": REPO / "tests/test_project_authority.py",
    "model_catalog_adapter_tests": REPO / "tests/test_model_catalog_adapters.py",
    "workspace_action_catalog": REPO / "docs/comsol_mcp_design_v1/02_ACTION_CATALOG.json",
}


def utc_now() -> str:
    return datetime.now().astimezone().isoformat()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _runtime_distributions() -> list[dict[str, str]]:
    """Return a deterministic inventory of every distribution visible to this Python."""
    rows_by_identity: dict[tuple[str, str], dict[str, str]] = {}
    site_packages = Path(sysconfig.get_paths()["purelib"]).resolve()
    for distribution in importlib.metadata.distributions():
        location = Path(getattr(distribution, "_path", ""))
        if location.name.startswith("._"):
            continue
        try:
            resolved_location = location.resolve()
            if not resolved_location.is_relative_to(site_packages):
                continue
            name = distribution.metadata.get("Name")
            version = distribution.version
        except (OSError, UnicodeError, ValueError):
            continue
        if not name or not version:
            continue
        normalized_name = re.sub(r"[-_.]+", "-", name).lower()
        rows_by_identity[(normalized_name, version)] = {
            "name": normalized_name, "version": version,
        }
    return sorted(rows_by_identity.values(), key=lambda row: (row["name"], row["version"]))


def _runtime_appledouble_metadata() -> list[str]:
    """Expose ignored AppleDouble metadata names without depending on sys.path spelling."""
    ignored = []
    for distribution in importlib.metadata.distributions():
        location = Path(getattr(distribution, "_path", ""))
        if location.name.startswith("._"):
            ignored.append(location.name)
    return sorted(set(ignored))


def _json_sha256(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _runtime_environment_candidate(pip_check_python: Path = DEFAULT_PIP_CHECK_PYTHON) -> dict[str, Any]:
    """Capture the exact Python environment that must be repeated at native launch."""
    checker = pip_check_python.expanduser().absolute()
    distributions = _runtime_distributions()
    checker_identity = subprocess.run(
        [str(checker), "-c",
         "import sys, pip; print(sys.executable); print(sys.version_info.major, sys.version_info.minor, sys.version_info.micro); print(pip.__version__)"],
        capture_output=True, text=True, check=False, timeout=15,
        env={**os.environ, "PIP_NO_CACHE_DIR": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1",
             "PIP_CONFIG_FILE": os.devnull},
    )
    if checker_identity.returncode != 0:
        raise RuntimeError("pip-check interpreter identity could not be captured: " + checker_identity.stderr)
    lines = checker_identity.stdout.strip().splitlines()
    if len(lines) != 3:
        raise RuntimeError("pip-check interpreter identity output was malformed")
    return {
        "python_executable": str(Path(sys.executable).absolute()),
        "python_resolved_executable": str(Path(sys.executable).resolve()),
        "python_version": ".".join(map(str, sys.version_info[:3])),
        "distributions": distributions,
        "distributions_sha256": _json_sha256(distributions),
        "ignored_appledouble_metadata": _runtime_appledouble_metadata(),
        "pip_check_python": str(checker),
        "pip_check_python_resolved": str(Path(lines[0]).resolve()),
        "pip_check_python_version": lines[1],
        "pip_check_version": lines[2],
    }


def _validate_python_runtime_inventory(expected: dict[str, Any], actual: dict[str, Any]) -> dict[str, str]:
    """Compare the exact interpreter and real installed distributions, excluding sidecars."""
    for key in ("python_executable", "python_resolved_executable", "python_version"):
        if actual.get(key) != expected.get(key):
            raise RuntimeError(f"running Python {key} differs from the frozen runtime interpreter")
    distributions = actual.get("distributions")
    if distributions != expected.get("distributions"):
        raise RuntimeError("installed Python distribution inventory differs from the frozen environment")
    if _json_sha256(distributions) != expected.get("distributions_sha256"):
        raise RuntimeError("installed Python distribution inventory hash differs from the frozen environment")
    by_name = {row["name"]: row["version"] for row in distributions}
    required = {"mcp": "1.30.0", "mph": "1.4.0", "jpype1": "1.7.1"}
    if any(by_name.get(name) != version for name, version in required.items()):
        raise RuntimeError("required runtime dependency versions differ from the reviewed Python environment")
    return {name: by_name[name] for name in sorted(required)}


def _verify_registered_workspace(project: Any, authorized_root: Path) -> tuple[str, Path]:
    if not isinstance(project, dict):
        raise RuntimeError("project.create response omitted the project record")
    project_id = project.get("project_id")
    workspace_text = project.get("workspace")
    if not isinstance(project_id, str) or not project_id:
        raise RuntimeError("project.create response omitted its authoritative project_id")
    if not isinstance(workspace_text, str) or not workspace_text:
        raise RuntimeError("project.create response omitted its authoritative workspace path")
    root = authorized_root.resolve(strict=True)
    workspace = Path(workspace_text)
    if not workspace.is_absolute():
        raise RuntimeError("project.create must return an absolute workspace path")
    try:
        metadata = workspace.lstat()
        resolved = workspace.resolve(strict=True)
    except OSError as exc:
        raise RuntimeError("project.create workspace cannot be inspected") from exc
    expected = root / PROJECT_WORKSPACE_NAME
    if (workspace != expected or resolved != expected or workspace.is_symlink() or
            not workspace.is_dir() or not resolved.is_relative_to(root) or resolved == root):
        raise RuntimeError("project.create workspace differs from the exact authorized science child")
    if not metadata or not workspace.exists():
        raise RuntimeError("project.create workspace is not a real directory")
    return project_id, resolved


def _configure_trusted_code_startup_opt_in() -> dict[str, Any]:
    """Set the explicit host startup capability before any daemon is built.

    This records only the deployment switch, not an isolation claim. The
    actual server-pair isolation receipt is installed after the owned listener
    has been verified and remains a separate gate.
    """
    inherited_receipt_was_present = ISOLATION_RECEIPT_ENV in os.environ
    os.environ.pop(ISOLATION_RECEIPT_ENV, None)
    os.environ[TRUSTED_CODE_STARTUP_ENV] = "1"
    return {
        "status": "EXPLICIT_TASK_DEPLOYMENT_OPT_IN_SET_BEFORE_DAEMON_START",
        "environment_variable": TRUSTED_CODE_STARTUP_ENV,
        "value": os.environ[TRUSTED_CODE_STARTUP_ENV],
        "trusted_code_isolation_claim": False,
        "inherited_isolation_receipt_cleared_before_native_identity": inherited_receipt_was_present,
        "isolation_receipt_installed_after_owned_server_verification": False,
    }


def _require_trusted_code_host_grant(daemon: Any) -> dict[str, list[str]]:
    """Verify the pre-connection deployment ceiling and current host grants."""
    ceiling = getattr(getattr(daemon, "backend", None), "host_permission_ceiling", None)
    if not isinstance(ceiling, (set, frozenset)):
        raise RuntimeError("ControlDaemon has no inspectable deployment permission ceiling")
    effective_reader = getattr(daemon, "_project_host_permissions", None)
    if not callable(effective_reader):
        raise RuntimeError("ControlDaemon has no effective host-permission readback")
    effective = effective_reader()
    if not isinstance(effective, (set, frozenset)):
        raise RuntimeError("ControlDaemon effective host-permission readback is malformed")
    if "trusted_code" not in ceiling or "trusted_code" not in effective:
        raise RuntimeError("trusted_code is not enabled in the prebirth host grant ceiling")
    return {"startup_ceiling": sorted(ceiling), "effective_host_permissions": sorted(effective)}


def _create_registered_project_workspace(daemon: Any, authorized_root: Path) -> dict[str, Any]:
    """Create W24's real project workspace through the production daemon before engine birth."""
    host_grants = _require_trusted_code_host_grant(daemon)
    key = f"w24-project-create-{uuid4()}"
    request_id = f"w24-project-create-request-{uuid4()}"
    arguments = {
        "label": "W24 cure coupon setup and science",
        "workspace": PROJECT_WORKSPACE_NAME,
        "policy": {"permissions": ["inspect", "project_write", "compute", "trusted_code"]},
        "idempotency_key": key,
        "request_id": request_id,
    }
    response = daemon.dispatch({
        "operation": "project.create",
        "arguments": arguments,
        "execution": {"idempotency_key": key, "request_id": request_id,
                      "rpc_timeout_s": 30.0, "queue_timeout_s": 30.0,
                      "execution_timeout_s": None},
    })
    if not isinstance(response, dict) or response.get("success") is not True:
        raise RuntimeError("canonical project.create did not complete successfully: " +
                           json.dumps(response, ensure_ascii=False, default=str)[:3000])
    data = response.get("data")
    project = data.get("project") if isinstance(data, dict) else None
    project_id, workspace = _verify_registered_workspace(project, authorized_root)
    recorded_policy = project.get("policy") if isinstance(project, dict) else None
    recorded_permissions = recorded_policy.get("permissions") if isinstance(recorded_policy, dict) else None
    expected_permissions = set(arguments["policy"]["permissions"])
    if not isinstance(recorded_permissions, list) or set(recorded_permissions) != expected_permissions:
        raise RuntimeError("project.create did not persist the exact requested project permission policy")
    inspect_request_id = f"w24-project-inspect-request-{uuid4()}"
    inspection = daemon.dispatch({
        "operation": "project.inspect",
        "arguments": {"project_id": project_id, "request_id": inspect_request_id},
        "execution": {"project_id": project_id, "request_id": inspect_request_id,
                      "rpc_timeout_s": 30.0,
                      "queue_timeout_s": 30.0, "execution_timeout_s": None},
    })
    inspected_data = inspection.get("data") if isinstance(inspection, dict) else None
    inspected_project = inspected_data.get("project") if isinstance(inspected_data, dict) else None
    inspected_permissions = (inspected_project.get("policy", {}).get("permissions")
                             if isinstance(inspected_project, dict) and
                             isinstance(inspected_project.get("policy"), dict) else None)
    effective_permissions = inspected_data.get("effective_permissions") if isinstance(inspected_data, dict) else None
    if (not isinstance(inspection, dict) or inspection.get("success") is not True or
            not isinstance(inspected_project, dict) or
            inspected_project.get("project_id") != project_id or
            inspected_project.get("workspace") != str(workspace) or
            not isinstance(inspected_permissions, list) or set(inspected_permissions) != expected_permissions or
            not isinstance(effective_permissions, list) or
            "trusted_code" not in effective_permissions):
        raise RuntimeError("production project.inspect did not read back the persisted project policy/workspace")
    return {"status": "PROJECT_CREATED_BEFORE_ENGINE_BIRTH", "project_id": project_id,
            "workspace": str(workspace), "response": response,
            "inspection_response": inspection,
            "idempotency_key": key, "request_id": request_id,
            "policy": arguments["policy"], "host_grants": host_grants}


def _adopt_registered_model(daemon: Any, *, project_id: str, session_id: str,
                            model_tag: str, idempotency_key: str,
                            request_id: str) -> dict[str, Any]:
    """Persist a loaded native tag through the canonical project-scoped route."""
    response = daemon.dispatch({
        "operation": "model.adopt",
        "arguments": {"server_model_tag": model_tag},
        "execution": {
            "project_id": project_id,
            "session_id": session_id,
            "idempotency_key": idempotency_key,
            "request_id": request_id,
            "rpc_timeout_s": 30.0,
            "queue_timeout_s": 30.0,
            "execution_timeout_s": None,
        },
    })
    if not isinstance(response, dict) or response.get("success") is not True:
        raise RuntimeError("canonical project-scoped model.adopt failed: " +
                           json.dumps(response, ensure_ascii=False, default=str)[:3000])
    execution = response.get("execution")
    model_ref = execution.get("model_ref") if isinstance(execution, dict) else None
    revision = execution.get("revision") if isinstance(execution, dict) else None
    if (not isinstance(execution, dict) or execution.get("project_id") != project_id or
            execution.get("session_id") != session_id or not isinstance(model_ref, dict) or
            model_ref.get("model_tag") != model_tag or model_ref.get("session_id") != session_id or
            isinstance(revision, bool) or not isinstance(revision, int) or revision < 0):
        raise RuntimeError("model.adopt response did not persist the exact project/session/native-tag identity")
    _require_project_bound_model(daemon, model_ref, project_id)
    return response


def _require_project_bound_model(daemon: Any, model_ref: dict[str, Any],
                                 project_id: str) -> dict[str, Any]:
    """Read the exact persisted ModelRef association after production create/adopt."""
    backend = getattr(daemon, "backend", None)
    reader = getattr(backend, "model_project_binding", None)
    if not callable(reader):
        raise RuntimeError("ControlDaemon cannot read back the persisted model/project association")
    binding = reader(model_ref)
    if (not isinstance(binding, dict) or binding.get("attribution") != "PROJECT_BOUND" or
            binding.get("project_id") != project_id):
        raise RuntimeError("managed ModelRef is not persisted under the exact registered project")
    return binding


def _resolve_project_file(workspace: Path, path: Path | str) -> Path:
    """Resolve an existing non-symlink file strictly inside a registered workspace."""
    root = workspace.resolve(strict=True)
    if not root.is_dir():
        raise RuntimeError("registered project workspace is not a directory")
    supplied = Path(path)
    if ".." in supplied.parts:
        raise RuntimeError("project-scoped file path cannot contain parent traversal")
    candidate = supplied if supplied.is_absolute() else root / supplied
    candidate = Path(os.path.abspath(candidate))
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise RuntimeError("project-scoped file path is outside the registered workspace") from exc
    if not relative.parts:
        raise RuntimeError("project-scoped file path must name a file below the registered workspace")
    cursor = root
    try:
        for part in relative.parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise RuntimeError("project-scoped file path cannot traverse a symlink")
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise RuntimeError("project-scoped file cannot be inspected") from exc
    if resolved != candidate or not resolved.is_relative_to(root) or not resolved.is_file():
        raise RuntimeError("project-scoped file is missing, aliased, or outside the registered workspace")
    return resolved


def _load_saved_template_managed(daemon: Any, *, project_id: str,
                                 project_workspace: Path, path: Path,
                                 idempotency_key: str, request_id: str,
                                 timeout_s: float = 120.0) -> dict[str, Any]:
    """Reopen an MPH through the path-authorized managed model_load route."""
    from tools.run_native_resume_smoke import _dispatch

    scoped_path = _resolve_project_file(project_workspace, path)
    response = _dispatch(daemon, "model_load", {"path": str(scoped_path)},
                         project_id=project_id, rpc_timeout_s=timeout_s,
                         idempotency_key=idempotency_key, request_id=request_id)
    if not isinstance(response, dict) or response.get("success") is not True:
        raise RuntimeError("project-scoped managed model_load failed: " +
                           json.dumps(response, ensure_ascii=False, default=str)[:3000])
    execution = response.get("execution")
    data = response.get("data")
    ref = execution.get("model_ref") if isinstance(execution, dict) else None
    revision = execution.get("revision") if isinstance(execution, dict) else None
    echoed_project_id = execution.get("project_id") if isinstance(execution, dict) else None
    if (not isinstance(execution, dict) or
            (echoed_project_id is not None and echoed_project_id != project_id) or
            not isinstance(ref, dict) or isinstance(revision, bool) or
            not isinstance(revision, int) or revision < 0):
        raise RuntimeError("managed model_load omitted the exact ModelRef/revision or echoed a foreign project")
    model_tag = (data.get("model_tag") if isinstance(data, dict) else None) or ref.get("model_tag")
    if not isinstance(model_tag, str) or not model_tag or ref.get("model_tag") != model_tag:
        raise RuntimeError("managed model_load did not return the exact loaded native model tag")
    if execution.get("session_id") != ref.get("session_id") or not ref.get("session_id"):
        raise RuntimeError("managed model_load returned inconsistent session identity")
    binding = _require_project_bound_model(daemon, ref, project_id)
    return {
        "status": "PROJECT_SCOPED_MANAGED_MODEL_LOAD_RETURNED",
        "project_id": project_id,
        "project_workspace": str(project_workspace.resolve(strict=True)),
        "path": str(scoped_path),
        "sha256": sha256(scoped_path),
        "model_tag": model_tag,
        "model_ref": dict(ref),
        "revision": revision,
        "persisted_project_binding": binding,
        "load_response": response,
    }


def _runtime_environment_preflight(expected: dict[str, Any], work: Path,
                                   evidence: Path) -> dict[str, Any]:
    """Prove Python dependencies and production no-engine construction before birth."""
    receipt: dict[str, Any] = {
        "status": "FAIL",
        "scope": "Python runtime/dependency validation plus production registry/ControlDaemon construction with no Worker or COMSOL connection",
        "engine_births": 0,
        "study_or_solver_submissions": 0,
        # Preserve the exact offline runtime target that this no-engine gate
        # checked, so a later science candidate can bind itself to the same
        # interpreter and installed distributions rather than guessing from
        # the current shell.
        "frozen_runtime_environment": dict(expected) if isinstance(expected, dict) else None,
    }
    daemon = None
    scratch_path: Path | None = None

    try:
        if os.environ.get(TRUSTED_CODE_STARTUP_ENV, "").strip().lower() not in {"1", "true", "yes"}:
            raise RuntimeError("explicit trusted_code host opt-in must be set before the production prebirth gate")
        if os.environ.get(ISOLATION_RECEIPT_ENV, "").strip():
            raise RuntimeError("an isolation receipt cannot be claimed before the owned server identity exists")
        if not isinstance(expected, dict):
            raise RuntimeError("frozen runtime_environment must be an object")
        current_executable = str(Path(sys.executable).absolute())
        current_resolved = str(Path(sys.executable).resolve())
        current_version = ".".join(map(str, sys.version_info[:3]))
        receipt.update({"python_executable": current_executable,
                        "python_resolved_executable": current_resolved,
                        "python_version": current_version})
        distributions = _runtime_distributions()
        distributions_sha = _json_sha256(distributions)
        receipt.update({"installed_distributions": distributions,
                        "installed_distributions_sha256": distributions_sha,
                        "ignored_appledouble_metadata": _runtime_appledouble_metadata()})
        receipt["required_runtime_distributions"] = _validate_python_runtime_inventory(
            expected,
            {"python_executable": current_executable,
             "python_resolved_executable": current_resolved,
             "python_version": current_version,
             "distributions": distributions,
             "distributions_sha256": distributions_sha,
             "ignored_appledouble_metadata": receipt["ignored_appledouble_metadata"]},
        )

        checker = Path(str(expected.get("pip_check_python", ""))).expanduser().absolute()
        if not checker.is_file() or str(checker) != expected.get("pip_check_python"):
            raise RuntimeError("frozen pip-check interpreter is missing or is not an absolute path")
        checker_identity = subprocess.run(
            [str(checker), "-c",
             "import sys, pip; print(sys.executable); print(sys.version_info.major, sys.version_info.minor, sys.version_info.micro); print(pip.__version__)"],
            capture_output=True, text=True, check=False, timeout=15,
            env={**os.environ, "PIP_NO_CACHE_DIR": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1",
                 "PIP_CONFIG_FILE": os.devnull},
        )
        checker_lines = checker_identity.stdout.strip().splitlines()
        if (checker_identity.returncode != 0 or len(checker_lines) != 3 or
                str(Path(checker_lines[0]).resolve()) != expected.get("pip_check_python_resolved") or
                checker_lines[1] != expected.get("pip_check_python_version") or
                checker_lines[2] != expected.get("pip_check_version")):
            raise RuntimeError("pip-check interpreter identity differs from its frozen receipt")
        pip_check_command = [str(checker), "-m", "pip", "--python", current_executable, "check"]
        pip_check = subprocess.run(
            pip_check_command, capture_output=True, text=True, check=False, timeout=30,
            env={**os.environ, "PIP_NO_CACHE_DIR": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1",
                 "PIP_CONFIG_FILE": os.devnull},
        )
        receipt["pip_check"] = {"command": pip_check_command,
                                "exit_code": pip_check.returncode,
                                "stdout": pip_check.stdout, "stderr": pip_check.stderr,
                                "pip_check_python_identity": {
                                    "executable": checker_lines[0],
                                    "version": checker_lines[1],
                                    "pip_version": checker_lines[2]}}
        if pip_check.returncode != 0:
            raise RuntimeError("read-only pip check found a broken or inconsistent runtime dependency")

        import_origins: dict[str, str] = {}
        for module_name in ("mcp", "mcp.server.fastmcp", "mph", "jpype", "comsol_mcp._server"):
            module = importlib.import_module(module_name)
            origin = getattr(module, "__file__", None)
            import_origins[module_name] = str(Path(origin).resolve()) if origin else "namespace-package"
        receipt["production_imports"] = import_origins

        from comsol_mcp._managed_backend import collect_legacy_registry
        registry = collect_legacy_registry()
        registry_names = sorted(registry)
        receipt["production_registry"] = {
            "status": "COLLECTED",
            "entry_count": len(registry_names),
            "entry_names": registry_names,
            "entry_names_sha256": _json_sha256(registry_names),
        }

        from comsol_mcp._control_daemon import ControlDaemon
        with tempfile.TemporaryDirectory(prefix="runtime-preflight-", dir=str(work)) as scratch:
            scratch_path = Path(scratch)
            daemon = ControlDaemon(scratch_path / "control-home", project_root=REPO)
            if daemon.worker is not None or daemon.service is not None:
                raise RuntimeError("no-engine ControlDaemon preflight unexpectedly acquired a Worker or service")
            trusted_grants = _require_trusted_code_host_grant(daemon)
            daemon_registry_names = sorted(daemon.backend.registry)
            if daemon_registry_names != registry_names:
                raise RuntimeError("ControlDaemon production registry differs from direct collect_legacy_registry result")
            receipt["production_control_daemon"] = {
                "status": "CONSTRUCTED_NO_ENGINE",
                "worker": None,
                "service": None,
                "registry_entry_count": len(daemon_registry_names),
                "registry_entry_names_sha256": _json_sha256(daemon_registry_names),
                "trusted_code_host_grants": trusted_grants,
            }
            daemon.close()
            daemon = None
            receipt["production_control_daemon"]["close"] = "RETURNED"
        receipt["scratch_removed"] = not scratch_path.exists()
        if receipt["scratch_removed"] is not True:
            raise RuntimeError("production no-engine preflight scratch directory was not removed")
        receipt.update({"status": "PASS", "engine_births": 0,
                        "study_or_solver_submissions": 0,
                        "no_engine_assertion": "ControlDaemon.worker is None and service is None; no connect or model operation was invoked"})
        write_json(evidence / "python_runtime_preflight.json", receipt)
        return receipt
    except Exception as exc:
        if daemon is not None:
            try:
                daemon.close()
                receipt["production_control_daemon_close_after_error"] = "RETURNED"
            except Exception as close_exc:
                receipt["production_control_daemon_close_after_error"] = f"{type(close_exc).__name__}: {close_exc}"
        receipt.update({"status": "FAIL", "error": f"{type(exc).__name__}: {exc}",
                        "traceback": traceback.format_exc()})
        write_json(evidence / "python_runtime_preflight.json", receipt)
        raise


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str,
                                    allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _guarded_direct_native_call(guard: dict[str, Any], evidence: Path,
                                label: str, call: Any) -> Any:
    """Record synchronous Worker/native calls that bypass managed dispatch."""
    record = {"label": label, "status": "IN_PROGRESS", "started_at_utc": utc_now()}
    guard.setdefault("calls", []).append(record)
    guard["all_returned"] = False
    guard["status"] = "DIRECT_NATIVE_CALL_IN_PROGRESS"
    write_json(evidence / "direct_native_call_guard.json", guard)
    try:
        result = call()
    except BaseException as exc:
        record.update({"status": "RAISED_OR_UNOBSERVED", "finished_at_utc": utc_now(),
                       "error": f"{type(exc).__name__}: {exc}"})
        guard["all_returned"] = False
        guard["status"] = "UNRESOLVED_DIRECT_NATIVE_CALL"
        write_json(evidence / "direct_native_call_guard.json", guard)
        raise
    record.update({"status": "RETURNED", "finished_at_utc": utc_now()})
    guard["all_returned"] = all(item.get("status") == "RETURNED" for item in guard["calls"])
    guard["status"] = ("ALL_DIRECT_NATIVE_CALLS_RETURNED" if guard["all_returned"]
                       else "UNRESOLVED_DIRECT_NATIVE_CALL")
    write_json(evidence / "direct_native_call_guard.json", guard)
    return result


def append_event(path: Path, event: str, **fields: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"at_utc": utc_now(), "event": event, **fields},
                                ensure_ascii=False, default=str, allow_nan=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _mac_birth_epoch(raw: str) -> float:
    parsed = datetime.strptime(raw, "%a %b %d %H:%M:%S %Y")
    return parsed.replace(tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()


def _probe_output_summary(value: str) -> dict[str, Any]:
    data = value.encode("utf-8", errors="surrogateescape")
    return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def _process_inventory() -> dict[str, Any]:
    """Classify native processes transiently and return only privacy-safe evidence."""
    ps_command = ["/bin/ps", "-axo", "pid=,ppid=,comm=,args="]
    lsof_command = ["/usr/sbin/lsof", "-nP", "-iTCP", "-sTCP:LISTEN"]
    ps = subprocess.run(ps_command, capture_output=True, text=True, check=False, timeout=15)
    rows: list[dict[str, Any]] = []
    worker_rows: list[dict[str, Any]] = []
    ps_failures: list[dict[str, str]] = []
    parsed_rows = 0
    malformed_rows = 0
    ambiguous_rows = 0
    observer_pid = os.getpid()
    observer_present = False
    for raw in ps.stdout.splitlines():
        if not raw.strip():
            continue
        fields = raw.strip().split(None, 3)
        if len(fields) < 3 or not fields[0].isdigit() or not fields[1].isdigit():
            malformed_rows += 1
            continue
        pid, ppid, comm = int(fields[0]), int(fields[1]), fields[2]
        argv = fields[3] if len(fields) == 4 else ""
        parsed_rows += 1
        observer_present = observer_present or pid == observer_pid
        lowered = (comm + " " + argv).lower()
        if not argv and ("java" in comm.lower() or "comsol" in comm.lower()):
            ambiguous_rows += 1
        is_engine = ("mphserver" in lowered or
                     ("/applications/comsol64/multiphysics/" in lowered and
                      ("java" in comm.lower() or "comsol" in lowered)))
        is_worker = "persistentcomsolworker" in lowered or "comsolworker" in lowered
        if is_engine:
            kind = "comsol_mphserver" if "mphserver" in lowered else "comsol_engine"
            rows.append({"pid": pid, "ppid": ppid, "kind": kind})
        if is_worker:
            worker_rows.append({"pid": pid, "ppid": ppid, "kind": "persistent_comsol_worker"})
    if ps.returncode != 0:
        ps_failures.append({"code": "nonzero_exit"})
    if ps.stderr.strip():
        ps_failures.append({"code": "stderr_nonempty"})
    if parsed_rows == 0:
        ps_failures.append({"code": "inventory_empty"})
    if malformed_rows:
        ps_failures.append({"code": "malformed_process_rows"})
    if ambiguous_rows:
        ps_failures.append({"code": "ambiguous_native_row_without_argv"})
    if not observer_present:
        ps_failures.append({"code": "observer_pid_missing"})

    lsof = subprocess.run(lsof_command, capture_output=True, text=True, check=False, timeout=15)
    listener_rows: list[dict[str, Any]] = []
    lsof_failures: list[dict[str, str]] = []
    parsed_listener_rows = 0
    malformed_listener_rows = 0
    lsof_lines = [line.strip() for line in lsof.stdout.splitlines() if line.strip()]
    header_fields = lsof_lines[0].split() if lsof_lines else []
    expected_header = [
        "COMMAND", "PID", "USER", "FD", "TYPE", "DEVICE", "SIZE/OFF", "NODE", "NAME",
    ]
    if [field.upper() for field in header_fields] != expected_header:
        lsof_failures.append({"code": "listener_header_missing_or_malformed"})
    else:
        for line in lsof_lines[1:]:
            fields = line.split(None, 8)
            if len(fields) != 9 or not fields[1].isdigit():
                malformed_listener_rows += 1
                continue
            name = fields[8]
            endpoint = name[:-9] if name.endswith(" (LISTEN)") else ""
            address, separator, port = endpoint.rpartition(":")
            if (fields[4] not in {"IPv4", "IPv6"} or fields[7].upper() != "TCP" or
                    not separator or not address or not port.isdigit()):
                malformed_listener_rows += 1
                continue
            parsed_listener_rows += 1
            lowered = line.lower()
            if "mphserver" in lowered or "comsol" in lowered:
                kind = "mphserver_listener" if "mphserver" in lowered else "comsol_listener"
                listener_rows.append({"pid": int(fields[1]), "kind": kind})
        if malformed_listener_rows:
            lsof_failures.append({"code": "malformed_listener_rows"})
    if lsof.returncode != 0:
        lsof_failures.append({"code": "nonzero_exit"})
    if lsof.stderr.strip():
        lsof_failures.append({"code": "stderr_nonempty"})

    ps_ok = not ps_failures
    lsof_ok = not lsof_failures
    probes_ok = ps_ok and lsof_ok
    return {
        "at_utc": utc_now(),
        "ps": {
            "command": ps_command,
            "exit_code": ps.returncode,
            "stdout": _probe_output_summary(ps.stdout),
            "stderr": _probe_output_summary(ps.stderr),
            "parsed_rows": parsed_rows,
            "malformed_rows": malformed_rows,
            "ambiguous_rows": ambiguous_rows,
            "observer_pid": observer_pid,
            "observer_present": observer_present,
            "failure_causes": ps_failures,
        },
        "lsof": {
            "command": lsof_command,
            "exit_code": lsof.returncode,
            "stdout": _probe_output_summary(lsof.stdout),
            "stderr": _probe_output_summary(lsof.stderr),
            "parsed_rows": parsed_listener_rows,
            "malformed_rows": malformed_listener_rows,
            "failure_causes": lsof_failures,
        },
        "comsol_engine_processes": rows,
        "persistent_worker_processes": worker_rows,
        "comsol_listener_rows": listener_rows,
        "failure_causes": ([{"probe": "ps", **cause} for cause in ps_failures] +
                            [{"probe": "lsof", **cause} for cause in lsof_failures]),
        "probes_ok": probes_ok,
        "quiescent": probes_ok and not rows and not worker_rows and not listener_rows,
    }


def _compile_offline(output_dir: Path, *, fixture_source: Path = FIXTURE) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=False)
    command = [str(JAVAC), "-cp",
               f"{INSTALL_ROOT}/plugins/*:{INSTALL_ROOT}/apiplugins/*",
               "-encoding", "UTF-8", "-d", str(output_dir), str(fixture_source)]
    result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=60)
    return {
        "command": command,
        "javac_version": subprocess.run([str(JAVAC), "-version"], capture_output=True,
                                         text=True, check=False, timeout=10).stderr.strip(),
        "exit_code": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "fixture_source": str(fixture_source.resolve()),
        "fixture_sha256": sha256(fixture_source),
        "classpath": f"{INSTALL_ROOT}/plugins/*:{INSTALL_ROOT}/apiplugins/*",
        "output_classes": sorted(str(path.relative_to(output_dir))
                                  for path in output_dir.rglob("*.class")),
    }


def _frozen_hashes() -> dict[str, dict[str, str]]:
    paths = _all_source_paths()
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError("W24 frozen sources are missing: " + ", ".join(missing))
    return {name: {"path": str(path), "sha256": sha256(path)}
            for name, path in paths.items()}


def _all_source_paths() -> dict[str, Path]:
    """Freeze the explicit runner closure plus runtime code and data resources."""
    paths = dict(SOURCE_PATHS)
    explicit = {path.resolve() for path in paths.values()}
    runtime_roots = (REPO / "comsol_mcp",)
    for root in runtime_roots:
        for path in sorted(path for path in root.rglob("*")
                            if path.is_file() and path.suffix in {".py", ".json"}):
            if path.name.startswith("._") or path.resolve() in explicit:
                continue
            kind = "runtime" if path.suffix == ".py" else "runtime_data"
            paths[f"{kind}:{path.relative_to(REPO).as_posix()}"] = path
    worker_root = REPO / "comsol_mcp/worker_java"
    for path in sorted(worker_root.rglob("*.java")):
        if path.name.startswith("._") or path.resolve() in explicit:
            continue
        paths[f"worker_java:{path.relative_to(REPO).as_posix()}"] = path
    return paths


def _verify_offline_candidate_freeze(path: Path, expected_sha256: str) -> dict[str, Any]:
    """Bind native execution to the reviewed, offline-compiled source candidate."""
    path = path.expanduser().absolute()
    root = (REPO / "docs/full_project_execution/w24/evidence/setup_candidate_").resolve()
    try:
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise RuntimeError(f"offline W24 candidate freeze is unavailable: {exc}") from exc
    if os.path.islink(path) or not path.is_file():
        raise RuntimeError("offline W24 candidate freeze must be a regular non-symlink file")
    if not str(resolved).startswith(str(root)):
        raise RuntimeError("offline W24 candidate freeze is outside the W24 setup-candidate evidence roots")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256) or sha256(resolved) != expected_sha256:
        raise RuntimeError("offline W24 candidate freeze SHA-256 differs from the reviewed value")
    try:
        candidate = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"offline W24 candidate freeze is malformed: {exc}") from exc
    if (not isinstance(candidate, dict) or
            candidate.get("schema") != "W24_SETUP_CANDIDATE_OFFLINE_FREEZE_V1" or
            candidate.get("status") != "FROZEN_OFFLINE_CANDIDATE_AWAITING_ROOT_NATIVE_SLOT" or
            candidate.get("candidate_id") != "W24-CURE-HIST-AXISYM-01"):
        raise RuntimeError("offline W24 candidate freeze has an unknown schema/status/candidate")
    native = candidate.get("native_execution")
    if (not isinstance(native, dict) or native.get("status") != "NOT_RUN" or
            native.get("engine_births_this_candidate") != 0 or
            native.get("study_run_submissions") != 0 or
            native.get("macos_6_3_arm64") != "USER_REQUESTED_SKIP" or
            native.get("macos_6_3_x86_64") != "USER_REQUESTED_SKIP"):
        raise RuntimeError("offline W24 candidate freeze contains invalid native/OS acceptance claims")
    limits = candidate.get("independent_native_budget")
    required_limits = {
        "owned_server_processes": 1,
        "max_seconds_from_exact_birth_including_setup_reopen_and_cleanup": MAX_BIRTH_BUDGET_S,
        "cleanup_reserve_seconds": CLEANUP_RESERVE_S,
        "worker_sessions_sequential": 1,
        "study_or_solver_submissions": 0,
    }
    if not isinstance(limits, dict) or any(limits.get(key) != value for key, value in required_limits.items()):
        raise RuntimeError("offline W24 candidate freeze changed the reviewed setup-only native budget")
    runtime_environment = candidate.get("runtime_environment")
    if not isinstance(runtime_environment, dict):
        raise RuntimeError("offline W24 candidate freeze has no exact Python runtime environment")
    for key in ("python_executable", "python_resolved_executable", "pip_check_python",
                "pip_check_python_resolved", "python_version", "pip_check_python_version",
                "pip_check_version", "distributions_sha256"):
        if not isinstance(runtime_environment.get(key), str) or not runtime_environment[key]:
            raise RuntimeError(f"offline W24 runtime environment lacks {key}")
    if (not Path(runtime_environment["python_executable"]).is_absolute() or
            not Path(runtime_environment["python_resolved_executable"]).is_absolute() or
            not Path(runtime_environment["pip_check_python"]).is_absolute() or
            not Path(runtime_environment["pip_check_python_resolved"]).is_absolute()):
        raise RuntimeError("offline W24 Python/pip-checker paths must be absolute")
    distributions = runtime_environment.get("distributions")
    if not isinstance(distributions, list) or not distributions:
        raise RuntimeError("offline W24 runtime environment has no installed distribution inventory")
    if not isinstance(runtime_environment.get("ignored_appledouble_metadata"), list):
        raise RuntimeError("offline W24 runtime environment must record ignored AppleDouble metadata")
    if any(not isinstance(row, dict) or not isinstance(row.get("name"), str) or
           not isinstance(row.get("version"), str) for row in distributions):
        raise RuntimeError("offline W24 installed distribution inventory is malformed")
    if _json_sha256(distributions) != runtime_environment.get("distributions_sha256"):
        raise RuntimeError("offline W24 installed distribution inventory hash is inconsistent")
    runtime_by_name = {row["name"]: row["version"] for row in distributions}
    if any(runtime_by_name.get(name) != version for name, version in
           {"mcp": "1.30.0", "mph": "1.4.0", "jpype1": "1.7.1"}.items()):
        raise RuntimeError("offline W24 candidate is missing the reviewed MCP/MPh/JPype runtime versions")
    frozen_sources = candidate.get("source_closure")
    current_sources = _frozen_hashes()
    if not isinstance(frozen_sources, dict):
        raise RuntimeError("offline W24 candidate freeze has no complete source closure")
    normalized = {name: {"path": row.get("path"), "sha256": row.get("sha256")}
                  for name, row in frozen_sources.items() if isinstance(row, dict)}
    if normalized != current_sources or set(normalized) != set(current_sources):
        raise RuntimeError("W24 setup source closure changed after the offline candidate freeze")
    plan = candidate.get("plan")
    if not isinstance(plan, dict) or plan.get("path") != str(PLAN) or plan.get("sha256") != sha256(PLAN):
        raise RuntimeError("W24 setup proposal changed after the offline candidate freeze")
    compile_receipt = candidate.get("offline_java_compile")
    py_receipt = candidate.get("python_compile")
    tests = candidate.get("focused_software_tests")
    if (not isinstance(compile_receipt, dict) or compile_receipt.get("exit_code") != 0 or
            not compile_receipt.get("classes") or not isinstance(py_receipt, dict) or
            py_receipt.get("exit_code") != 0 or not isinstance(tests, dict) or
            tests.get("exit_code") != 0 or
            re.search(r"\b\d+ passed\b", str(tests.get("stdout", ""))) is None):
        raise RuntimeError("offline W24 candidate lacks successful Java/Python/focused test receipts")
    return {"path": str(resolved), "sha256": expected_sha256,
            "candidate_id": candidate["candidate_id"],
            "source_count": len(current_sources), "status": candidate["status"],
            "runtime_environment": runtime_environment}


def _java_action_readback(response: dict[str, Any], label: str) -> dict[str, Any]:
    if not isinstance(response, dict) or response.get("success") is not True:
        raise RuntimeError(f"{label} operation failed: {json.dumps(response, ensure_ascii=False, default=str)[:5000]}")
    data = response.get("data")
    if not isinstance(data, dict):
        raise AssertionError(f"{label} is missing operation data")
    worker = data.get("worker")
    wrapped = data.get("readback")
    if (not isinstance(worker, dict) or worker.get("ok") is not True or
            worker.get("status") != "SUCCEEDED" or not isinstance(wrapped, dict) or
            wrapped.get("executed") is not True or not isinstance(wrapped.get("readback"), dict)):
        raise AssertionError(f"{label} lacks an observed terminal successful Worker Java receipt")
    nested = wrapped["readback"]
    if worker.get("result", {}).get("readback") != nested:
        raise AssertionError(f"{label} Worker result and operation readback differ")
    return nested


def _stress_candidate_cues(row: list[Any]) -> list[str]:
    joined = "\x1f".join(cell for cell in row if isinstance(cell, str))
    lowered = joined.lower()
    cues = [cue for cue in ("stress", "cauchy", "shear", "hoop", "radial",
                            "circumferential", "azimuthal") if cue in lowered]
    solid_stress_identifier = bool(
        re.search(r"(?<![a-z0-9_])solid\.s[a-z0-9_]*(?![a-z0-9_])", lowered))
    if not any(cue in cues for cue in ("stress", "cauchy", "shear")) and not solid_stress_identifier:
        return []
    if solid_stress_identifier:
        cues.append("solid.s-prefixed-identifier")
    return cues


def _verify_expression_inventory(receipt: dict[str, Any], output_path: Path) -> dict[str, Any]:
    """Verify the complete standalone native Expression-table artifact."""
    output_path = output_path.expanduser().absolute()
    try:
        metadata = output_path.lstat()
    except OSError as exc:
        raise RuntimeError(f"native expression inventory artifact is unavailable: {exc}") from exc
    if os.path.islink(output_path) or not output_path.is_file():
        raise RuntimeError("native expression inventory must be a regular non-symlink file")
    output_path = output_path.resolve(strict=True)
    if not output_path.as_posix().startswith("/private/tmp/comsol-mcp-w24-cure-"):
        raise RuntimeError("native expression inventory artifact escaped its private W24 work tree")
    if receipt.get("status") != "NATIVE_EXPRESSION_INVENTORY_SAVED_NOT_EVALUATED":
        raise RuntimeError(f"native Equation View inventory is incomplete: {receipt.get('status')}")
    if (receipt.get("complete") is not True or receipt.get("read_only") is not True or
            receipt.get("model_mutations") != 0 or receipt.get("study_run_calls") != 0):
        raise RuntimeError("native Equation View receipt does not prove complete read-only zero-solve capture")
    if receipt.get("path") != str(output_path):
        raise RuntimeError("native Equation View receipt path differs from the requested artifact")
    data = output_path.read_bytes()
    if (isinstance(receipt.get("size_bytes"), bool) or
            receipt.get("size_bytes") != len(data) or
            receipt.get("sha256") != hashlib.sha256(data).hexdigest()):
        raise RuntimeError("native Equation View artifact byte count or SHA-256 differs from its receipt")
    try:
        artifact = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"native Equation View artifact is not complete UTF-8 JSON: {exc}") from exc
    if (not isinstance(artifact, dict) or
            artifact.get("schema") != "W24_COMSOL_EQUATION_VIEW_EXPRESSION_INVENTORY_V1" or
            artifact.get("status") != "COMPLETE_NOT_EVALUATED" or
            artifact.get("complete") is not True or artifact.get("read_only") is not True or
            artifact.get("model_mutations") != 0 or artifact.get("study_run_calls") != 0 or
            artifact.get("table_request") != ["Expression", "recursive", "all"] or
            artifact.get("candidate_rule") != EXPRESSION_CANDIDATE_RULE):
        raise RuntimeError("native Equation View JSON lacks its complete read-only schema/API contract")
    if artifact.get("solid_physics_tags") != ["solid"]:
        raise RuntimeError("native Equation View JSON did not identify exactly one configured Solid Mechanics interface")
    tables = artifact.get("feature_tables")
    if not isinstance(tables, list) or not tables:
        raise RuntimeError("native Equation View JSON has no observed Solid Mechanics feature tables")
    rows = 0
    candidates = 0
    for item in tables:
        if not isinstance(item, dict) or item.get("status") != "READ":
            raise RuntimeError("native Equation View JSON contains an unreadable feature table")
        raw_rows = item.get("raw_rows")
        if not isinstance(raw_rows, list) or item.get("row_count") != len(raw_rows):
            raise RuntimeError("native Equation View JSON feature table row count is incomplete")
        for row in raw_rows:
            if not isinstance(row, list) or not all(cell is None or isinstance(cell, str) for cell in row):
                raise RuntimeError("native Equation View JSON has a malformed/truncated native row")
        native_candidates = item.get("stress_candidate_rows")
        if not isinstance(native_candidates, list):
            raise RuntimeError("native Equation View JSON omitted its transparent stress-candidate view")
        for candidate in native_candidates:
            if (not isinstance(candidate, dict) or
                    isinstance(candidate.get("row_index"), bool) or
                    not isinstance(candidate.get("row_index"), int) or
                    not 0 <= candidate["row_index"] < len(raw_rows) or
                    candidate.get("raw_row") != raw_rows[candidate["row_index"]] or
                    not isinstance(candidate.get("cues"), list) or not candidate["cues"]):
                raise RuntimeError("native Equation View candidate row is detached from its full raw table")
            if candidate["cues"] != _stress_candidate_cues(raw_rows[candidate["row_index"]]):
                raise RuntimeError("native Equation View candidate cues do not match the frozen filter rule")
        expected_candidates = [
            {"row_index": row_index, "cues": _stress_candidate_cues(row), "raw_row": row}
            for row_index, row in enumerate(raw_rows)
            if _stress_candidate_cues(row)
        ]
        if native_candidates != expected_candidates:
            raise RuntimeError("native Equation View candidate list is incomplete or has rows out of order")
        rows += len(raw_rows)
        candidates += len(native_candidates)
    if (artifact.get("feature_count") != len(tables) or
            artifact.get("expression_row_count") != rows or
            artifact.get("stress_candidate_row_count") != candidates or
            artifact.get("errors") != []):
        raise RuntimeError("native Equation View JSON summary disagrees with the full stored rows")
    return {
        "path": str(output_path),
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "feature_count": len(tables),
        "expression_row_count": rows,
        "stress_candidate_row_count": candidates,
        "status": artifact["status"],
        "scope": "complete raw Solid Mechanics Equation View table inventory; candidates are not evaluated variables",
    }


def _verify_registered_template_files(receipt: dict[str, Any], template_path: Path,
                                      equation_view_inventory: dict[str, Any]) -> dict[str, Any]:
    """Verify template, fixture, classes, and raw Equation View live in its project."""
    project_id = receipt.get("project_id")
    workspace_text = receipt.get("project_workspace")
    if not isinstance(project_id, str) or not project_id:
        raise RuntimeError("configured template receipt has no canonical project id")
    if not isinstance(workspace_text, str) or not workspace_text:
        raise RuntimeError("configured template receipt has no registered project workspace")
    workspace = Path(workspace_text)
    try:
        workspace_meta = workspace.lstat()
        resolved_workspace = workspace.resolve(strict=True)
    except OSError as exc:
        raise RuntimeError("registered project workspace is unavailable") from exc
    if (os.path.islink(workspace) or not workspace_meta or not workspace.is_dir() or
            resolved_workspace != workspace or workspace.name != PROJECT_WORKSPACE_NAME or
            workspace.parent.name != "project"):
        raise RuntimeError("registered project workspace has an unexpected identity or path")
    if not template_path.is_relative_to(workspace):
        raise RuntimeError("saved MPH is outside the registered project workspace")

    creation_text = receipt.get("project_creation_receipt")
    creation_sha = receipt.get("project_creation_receipt_sha256")
    if not isinstance(creation_text, str) or not isinstance(creation_sha, str):
        raise RuntimeError("configured template receipt lacks its production project.create receipt")
    creation_path = Path(creation_text)
    try:
        creation_meta = creation_path.lstat()
        resolved_creation = creation_path.resolve(strict=True)
    except OSError as exc:
        raise RuntimeError("project.create receipt is unavailable") from exc
    if (os.path.islink(creation_path) or not creation_meta or not creation_path.is_file() or
            sha256(resolved_creation) != creation_sha):
        raise RuntimeError("project.create receipt bytes or file identity changed")
    try:
        creation = json.loads(resolved_creation.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("project.create receipt is not readable JSON") from exc
    if not isinstance(creation, dict):
        raise RuntimeError("project.create receipt is not a JSON object")
    response = creation.get("response")
    if not isinstance(response, dict):
        raise RuntimeError("project.create receipt omitted its production response")
    if response.get("success") is not True:
        raise RuntimeError("project.create receipt does not contain a successful production response")
    data = response.get("data")
    project = data.get("project") if isinstance(data, dict) else None
    policy = project.get("policy") if isinstance(project, dict) else None
    returned_permissions = policy.get("permissions") if isinstance(policy, dict) else None
    inspection = creation.get("inspection_response")
    inspection_data = inspection.get("data") if isinstance(inspection, dict) else None
    inspected_project = inspection_data.get("project") if isinstance(inspection_data, dict) else None
    inspected_policy = inspected_project.get("policy") if isinstance(inspected_project, dict) else None
    inspected_permissions = inspected_policy.get("permissions") if isinstance(inspected_policy, dict) else None
    effective_permissions = inspection_data.get("effective_permissions") if isinstance(inspection_data, dict) else None
    host_grants = creation.get("host_grants")
    if not isinstance(host_grants, dict):
        host_grants = {}
    if (not isinstance(project, dict) or
            creation.get("status") != "PROJECT_CREATED_BEFORE_ENGINE_BIRTH" or
            creation.get("project_id") != project_id or creation.get("workspace") != str(workspace) or
            project.get("project_id") != project_id or project.get("workspace") != str(workspace) or
            not isinstance(inspection, dict) or inspection.get("success") is not True or
            not isinstance(inspected_project, dict) or inspected_project.get("project_id") != project_id or
            inspected_project.get("workspace") != str(workspace) or
            not isinstance(inspected_permissions, list) or
            set(inspected_permissions) != {"inspect", "project_write", "compute", "trusted_code"} or
            not isinstance(effective_permissions, list) or "trusted_code" not in effective_permissions or
            "trusted_code" not in host_grants.get("startup_ceiling", []) or
            "trusted_code" not in host_grants.get("effective_host_permissions", []) or
            not isinstance(returned_permissions, list) or "trusted_code" not in returned_permissions):
        raise RuntimeError("project.create receipt does not bind the exact project/workspace/trusted_code policy")

    fixture_text = receipt.get("fixture_source_path")
    fixture_sha = receipt.get("fixture_source_sha256")
    if not isinstance(fixture_text, str) or not isinstance(fixture_sha, str):
        raise RuntimeError("configured template receipt lacks its project-local Java fixture source")
    fixture = Path(fixture_text)
    try:
        fixture_meta = fixture.lstat()
        fixture_resolved = fixture.resolve(strict=True)
    except OSError as exc:
        raise RuntimeError("registered project fixture source is unavailable") from exc
    if (os.path.islink(fixture) or not fixture_meta or not fixture.is_file() or
            not fixture_resolved.is_relative_to(workspace) or sha256(fixture_resolved) != fixture_sha):
        raise RuntimeError("project-local Java fixture source is not intact or is outside its registered workspace")

    classes_dir_text = receipt.get("fixture_compile_output_dir")
    class_names = receipt.get("fixture_compile_classes")
    if not isinstance(classes_dir_text, str) or not isinstance(class_names, list) or not class_names:
        raise RuntimeError("configured template receipt lacks project-local Java compile outputs")
    classes_dir = Path(classes_dir_text)
    if classes_dir != workspace / "offline-classes" or not classes_dir.is_dir() or classes_dir.is_symlink():
        raise RuntimeError("COMSOL fixture classes are outside the registered project workspace")
    verified_classes = []
    for name in class_names:
        if not isinstance(name, str) or not name.endswith(".class") or Path(name).is_absolute() or ".." in Path(name).parts:
            raise RuntimeError("COMSOL fixture compile receipt contains an invalid class path")
        class_path = classes_dir / name
        if class_path.is_symlink() or not class_path.is_file() or class_path.stat().st_size <= 0:
            raise RuntimeError("registered project Java class output is missing or empty")
        verified_classes.append(str(class_path.resolve(strict=True)))

    inventory_path = Path(str(equation_view_inventory.get("path", "")))
    if (not inventory_path.is_relative_to(workspace) or
            equation_view_inventory.get("registered_project_path") != str(inventory_path) or
            not inventory_path.is_file() or inventory_path.is_symlink() or
            sha256(inventory_path) != equation_view_inventory.get("sha256")):
        raise RuntimeError("raw native Equation View inventory is not preserved in the registered project")
    return {"project_id": project_id, "workspace": str(workspace),
            "project_create_receipt": str(resolved_creation),
            "project_create_receipt_sha256": creation_sha,
            "fixture_source": str(fixture_resolved), "fixture_source_sha256": fixture_sha,
            "compiled_classes": verified_classes,
            "equation_view_inventory": str(inventory_path)}


def _worker_request_terminal(response: Any) -> bool:
    """Whether the most recent Worker call returned a terminal status.

    A terminal FAILED call may still leave model state UNKNOWN; that state is
    recorded separately and does not mean the Worker request is still running.
    """
    if not isinstance(response, dict):
        return False
    data = response.get("data") if isinstance(response.get("data"), dict) else {}
    worker = data.get("worker")
    return isinstance(worker, dict) and worker.get("status") in TERMINAL


def _bounded_rpc_timeout(deadline_epoch: float, preferred_s: float, *, now: float | None = None) -> float:
    """Return an RPC timeout that leaves the full cleanup reserve, or fail closed."""
    current = time.time() if now is None else now
    usable = deadline_epoch - current - CLEANUP_RESERVE_S
    if usable <= 1.0:
        raise TimeoutError("W24 setup-only birth budget is inside its cleanup reserve; no Worker request dispatched")
    return min(preferred_s, usable)


def _all_project_jobs(store: Any, project_id: str, *, page_size: int = 250) -> list[dict[str, Any]]:
    if not isinstance(project_id, str) or not project_id.strip():
        raise ValueError("project-scoped job inventory requires the authoritative project id")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    offset = 0
    total: int | None = None
    while total is None or offset < total:
        page = store.list_jobs(offset=offset, limit=page_size, project_id=project_id)
        items = list(page)
        page_total = getattr(page, "total", None)
        if page_total is None:
            if len(items) == page_size:
                raise RuntimeError("paged job listing omitted total count; refusing incomplete job inventory")
            total = offset + len(items)
        else:
            total = int(page_total)
        if not items and offset < total:
            raise RuntimeError("paged job listing returned an empty page before its reported total")
        for job in items:
            job_id = job.get("job_id")
            if not isinstance(job_id, str) or job_id in seen:
                raise RuntimeError("paged job listing returned an invalid or duplicate job id")
            seen.add(job_id)
            rows.append(job)
        offset += len(items)
        if offset >= total:
            break
        if not items:
            break
    return rows


def _all_job_events(store: Any, job_id: str, *, page_size: int = 250) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = store.events(job_id, offset=offset, limit=page_size)
        items = list(page)
        rows.extend(items)
        if len(items) < page_size:
            break
        offset += len(items)
    return rows


def _project_job_inventory(daemon: Any, project_id: str) -> dict[str, Any]:
    rows = _all_project_jobs(daemon.store, project_id)
    active = [job for job in rows if str(job.get("status", "")).upper() in ACTIVE_JOB_STATES]
    unknown = [job for job in rows if str(job.get("status", "")).upper() not in TERMINAL | ACTIVE_JOB_STATES]
    return {"jobs": rows, "active": active, "unknown": unknown,
            "status": "ACTIVE" if active else "UNKNOWN" if unknown else "TERMINAL"}


def _worker_request_activity_inventory(store: Any, project_id: str) -> dict[str, Any]:
    """Reconcile every submitted/observed Worker request across every job page."""
    submitted: dict[str, list[dict[str, Any]]] = {}
    observed: dict[str, list[dict[str, Any]]] = {}
    ambiguous_events: list[dict[str, Any]] = []
    for job in _all_project_jobs(store, project_id):
        for event in _all_job_events(store, job["job_id"]):
            if event.get("event") != "worker_request":
                continue
            metadata = event.get("metadata")
            if not isinstance(metadata, dict):
                ambiguous_events.append({"job_id": job["job_id"], "reason": "metadata_not_object"})
                continue
            phase = metadata.get("phase")
            request_id = metadata.get("request_id")
            if not isinstance(request_id, str) or phase not in {"submitted", "observed"}:
                ambiguous_events.append({"job_id": job["job_id"], "reason": "missing_request_id_or_phase",
                                         "phase": phase})
                continue
            row = {"job_id": job["job_id"], "request_id": request_id,
                   "phase": phase, "kind": metadata.get("kind")}
            if phase == "submitted":
                submitted.setdefault(request_id, []).append(row)
            else:
                reply = metadata.get("reply") if isinstance(metadata.get("reply"), dict) else {}
                row["status"] = reply.get("status", metadata.get("status"))
                failure = reply.get("failure") if isinstance(reply.get("failure"), dict) else {}
                row["execution_state_unknown"] = failure.get("execution_state_unknown") is True
                row["failure_code"] = failure.get("code")
                observed.setdefault(request_id, []).append(row)
    pending = sorted(set(submitted) - set(observed))
    orphan_observed = sorted(set(observed) - set(submitted))
    nonterminal = sorted({request_id for request_id, rows in observed.items()
                          if any(row.get("status") not in WORKER_CLEANUP_TERMINAL for row in rows)})
    unknown_effect = sorted(request_id for request_id, rows in observed.items()
                            if any(row.get("execution_state_unknown") for row in rows))
    duplicate_submitted = sorted(request_id for request_id, rows in submitted.items() if len(rows) != 1)
    duplicate_observed = sorted(request_id for request_id, rows in observed.items() if len(rows) != 1)
    safe = not (pending or orphan_observed or nonterminal or ambiguous_events or
                duplicate_submitted or duplicate_observed)
    return {
        "status": "ALL_WORKER_REQUESTS_TERMINAL" if safe else "ACTIVE_OR_UNKNOWN_WORKER_REQUESTS",
        "submitted_request_count": sum(map(len, submitted.values())),
        "observed_request_count": sum(map(len, observed.values())),
        "unique_submitted_request_ids": len(submitted),
        "unique_observed_request_ids": len(observed),
        "pending_request_ids": pending,
        "orphan_observed_request_ids": orphan_observed,
        "nonterminal_observed_request_ids": nonterminal,
        "duplicate_submitted_request_ids": duplicate_submitted,
        "duplicate_observed_request_ids": duplicate_observed,
        "execution_state_unknown_request_ids": unknown_effect,
        "ambiguous_events": ambiguous_events,
        "requests": {request_id: {"submitted": submitted.get(request_id, []),
                                   "observed": observed.get(request_id, [])}
                     for request_id in sorted(set(submitted) | set(observed))},
        "safe_to_cleanup": safe,
    }


def _cleanup_reconciliation(job_inventory: dict[str, Any],
                            worker_inventory: dict[str, Any], *,
                            direct_native_calls_safe: bool) -> dict[str, Any]:
    """Adapt W24's fully paged ledger to the independently tested W23 TERM gate."""
    requests = worker_inventory.get("requests", {})
    request_ids = set(requests) if isinstance(requests, dict) else set()
    checks = {
        "all_submissions_observed_once": (
            worker_inventory.get("pending_request_ids") == [] and
            worker_inventory.get("orphan_observed_request_ids") == [] and
            worker_inventory.get("duplicate_submitted_request_ids") == [] and
            worker_inventory.get("duplicate_observed_request_ids") == [] and
            len(request_ids) == worker_inventory.get("unique_submitted_request_ids") ==
            worker_inventory.get("unique_observed_request_ids")),
        "all_observed_workers_terminal": (
            worker_inventory.get("nonterminal_observed_request_ids") == [] and
            worker_inventory.get("safe_to_cleanup") is True),
        "no_orphan_observations": worker_inventory.get("orphan_observed_request_ids") == [],
        "no_ambiguous_worker_ids": (worker_inventory.get("ambiguous_events") == [] and
                                     worker_inventory.get("duplicate_submitted_request_ids") == [] and
                                     worker_inventory.get("duplicate_observed_request_ids") == []),
        "no_queued_or_running_jobs": job_inventory.get("active") == [],
        "all_direct_native_calls_returned": direct_native_calls_safe is True,
    }
    safe = all(checks.values())
    return {
        "status": "ALL_WORKER_REQUESTS_TERMINAL" if safe else "ACTIVE_OR_UNKNOWN_WORKER_REQUESTS",
        "safe_for_owned_cleanup": safe,
        "durable_result": {
            "project_jobs_unknown_preserved": job_inventory.get("unknown", []),
            "execution_state_unknown_request_ids": worker_inventory.get(
                "execution_state_unknown_request_ids", []),
        },
        "checks": checks,
        "worker_inventory": worker_inventory,
        "active_jobs": job_inventory.get("active", []),
    }


def _compare_reopened_readback(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Require every configured geometry/physics/solver readback to survive MPH reload."""
    ignored = {"status", "native_study_run_calls"}
    expected = {key: value for key, value in before.items() if key not in ignored}
    missing = sorted(set(expected) - set(after))
    added = sorted(set(after) - set(expected))
    changed = sorted(key for key in set(expected) & set(after) if expected[key] != after[key])
    return {"matches": not (missing or added or changed),
            "missing_keys": missing, "added_keys": added, "changed_keys": changed,
            "compared_keys": sorted(expected)}


def _actual_study_run_submissions(daemon: Any, project_id: str) -> list[dict[str, Any]]:
    from tools.run_native_resume_smoke import _is_native_study_run_submission
    rows: list[dict[str, Any]] = []
    for job in _all_project_jobs(daemon.store, project_id):
        for event in _all_job_events(daemon.store, job["job_id"]):
            metadata = event.get("metadata")
            if event.get("event") == "worker_request" and _is_native_study_run_submission(metadata):
                rows.append({"job_id": job["job_id"], "status": job.get("status"),
                             "worker_event": metadata})
    return rows


def _verify_cleanup(server: Any) -> dict[str, Any]:
    if server.proc is None or server.proc.poll() is None:
        return {"status": "STOP_UNVERIFIED", "reason": "owned process remains alive"}
    pid = server.proc.pid
    port = server.port
    ps = subprocess.run(["/bin/ps", "-p", str(pid), "-o", "pid=,ppid=,lstart=,command="],
                        capture_output=True, text=True, check=False, timeout=10)
    lsof = subprocess.run(["/usr/sbin/lsof", "-nP", "-a", "-p", str(pid),
                           f"-iTCP:{port}", "-sTCP:LISTEN"], capture_output=True,
                          text=True, check=False, timeout=10)
    return {
        "status": ("STOPPED_AND_LISTENER_ABSENT" if ps.returncode == 1 and not ps.stdout.strip()
                   and not ps.stderr.strip() and lsof.returncode == 1
                   and not lsof.stdout.strip() and not lsof.stderr.strip() else "CLEANUP_UNVERIFIED"),
        "owned_pid": pid,
        "owned_birth": (server.process_identity or {}).get("birth"),
        "owned_command": (server.process_identity or {}).get("command"),
        "port": port,
        "ps_command": ["/bin/ps", "-p", str(pid), "-o", "pid=,ppid=,lstart=,command="],
        "ps_exit_code": ps.returncode, "ps_stdout": ps.stdout, "ps_stderr": ps.stderr,
        "lsof_command": ["/usr/sbin/lsof", "-nP", "-a", "-p", str(pid),
                         f"-iTCP:{port}", "-sTCP:LISTEN"],
        "lsof_exit_code": lsof.returncode, "lsof_stdout": lsof.stdout, "lsof_stderr": lsof.stderr,
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    from tools.run_native_resume_smoke import NativeLoopbackServer, _dispatch, _engine_build_identity
    from comsol_mcp._g2_isolation import _process_snapshot

    work = Path(args.work).resolve()
    evidence = Path(args.evidence).resolve()
    if not work.as_posix().startswith("/private/tmp/comsol-mcp-w24-cure-"):
        raise ValueError("--work must be a unique task-owned /private/tmp/comsol-mcp-w24-cure-* path")
    if work.exists() or evidence.exists():
        raise FileExistsError("W24 work and evidence paths must be new; existing data will not be overwritten")
    if not args.candidate_freeze or not args.expected_candidate_sha256:
        raise ValueError("native setup requires --candidate-freeze and --expected-candidate-sha256")
    offline_candidate = _verify_offline_candidate_freeze(
        Path(args.candidate_freeze), args.expected_candidate_sha256)
    work.mkdir(parents=True, exist_ok=False)
    evidence.mkdir(parents=True, exist_ok=False)
    events = evidence / "events.jsonl"
    status = "PREPARED"
    server = None
    daemon = None
    worker_connected = False
    request_terminal = True
    project_id: str | None = None
    project_workspace: Path | None = None
    project_creation: dict[str, Any] | None = None
    fixture_copy: Path | None = None
    direct_native_call_guard: dict[str, Any] = {
        "status": "NO_DIRECT_NATIVE_CALLS_YET", "all_returned": True, "calls": [],
    }
    engine_birth_epoch: float | None = None
    original_process_identity: dict[str, Any] | None = None
    worker_process_identity: dict[str, Any] | None = None
    worker_port: int | None = None
    cleanup: dict[str, Any] | None = None
    summary: dict[str, Any] = {
        "status": "FAIL_OR_INCOMPLETE",
        "scope": "W24 setup-only native 6.4 model build/readback/Equation View/template save-reopen; zero study/solver submissions",
        "python_executable": str(Path(sys.executable).absolute()),
        "python_version": ".".join(map(str, sys.version_info[:3])),
        "engine_births": 0,
        "native_solve_submissions": 0,
    }
    try:
        if sys.platform != "darwin":
            raise RuntimeError(f"W24 native preflight requires macOS, observed {sys.platform}")
        trusted_code_startup = _configure_trusted_code_startup_opt_in()
        summary["trusted_code_startup"] = trusted_code_startup
        runtime_receipt = _runtime_environment_preflight(
            offline_candidate["runtime_environment"], work, evidence)
        summary["python_runtime_preflight"] = runtime_receipt["status"]
        append_event(events, "production_python_runtime_preflight_passed",
                     python_executable=runtime_receipt["python_executable"],
                     python_version=runtime_receipt["python_version"],
                     distributions_sha256=runtime_receipt["installed_distributions_sha256"],
                     registry_entry_count=runtime_receipt["production_registry"]["entry_count"],
                     no_engine=True)
        if not INSTALL_ROOT.is_dir() or not JAVA_HOME.is_dir() or not JAVAC.is_file():
            raise FileNotFoundError("the pre-inventoried COMSOL 6.4 or Corretto 11 installation is missing")
        preflight = _process_inventory()
        write_json(evidence / "prelaunch_inventory.json", preflight)
        if not preflight["quiescent"]:
            raise RuntimeError("fresh process/listener inventory is unavailable or not quiescent; server was not started")

        server = NativeLoopbackServer(work, evidence, event_log=events)
        shadow = server.prepare_shadow()
        write_json(evidence / "private_shadow_receipt.json", shadow)
        from comsol_mcp._control_daemon import ControlDaemon

        project_daemon = ControlDaemon(work / "control", project_root=server.project)
        try:
            prebirth_host_grants = _require_trusted_code_host_grant(project_daemon)
            project_creation = _create_registered_project_workspace(project_daemon, server.project)
        finally:
            project_daemon.close()
        project_id = project_creation["project_id"]
        project_workspace = Path(project_creation["workspace"])
        write_json(evidence / "project_workspace_created_before_birth.json", project_creation)
        fixture_copy = project_workspace / FIXTURE.name
        if fixture_copy.exists() or fixture_copy.is_symlink():
            raise FileExistsError("registered project workspace already contains the W24 fixture source")
        shutil.copy2(FIXTURE, fixture_copy)

        offline_compile = _compile_offline(project_workspace / "offline-classes",
                                           fixture_source=fixture_copy)
        write_json(evidence / "offline_javac.json", offline_compile)
        if offline_compile["exit_code"] != 0 or not offline_compile["output_classes"]:
            raise RuntimeError("offline COMSOL 6.4 Java fixture compilation did not pass")
        frozen = _frozen_hashes()
        freeze = {
            "status": "FROZEN_BEFORE_NATIVE_BIRTH",
            "frozen_at_utc": utc_now(),
            "candidate_id": "W24-CURE-HIST-AXISYM-01",
            "scope": "setup-only native model/physics/mesh/solver construction, complete Equation View capture, MPH save/reopen readback; no study.run",
            "limits": {"max_owned_engine_processes": 1,
                       "max_wall_seconds_from_process_birth": MAX_BIRTH_BUDGET_S,
                       "cleanup_reserve_seconds": CLEANUP_RESERVE_S,
                       "solver_submissions_this_preflight": 0,
                       "max_worker_sessions_this_preflight": 1,
                       "cleanup_policy": "W23 exact PID/birth/command/port reconciliation; SIGTERM only; no SIGKILL fallback",
                       "following_solver_campaign": {
                           "status": "NOT_FROZEN_BY_THIS_PREFLIGHT",
                           "separate_birth_budget_seconds": 3600,
                           "max_submissions": 10,
                           "max_sequential_workers": 2,
                           "first_two_slots": ["mechanics_free_expansion", "mechanics_fully_fixed"],
                           "start_condition": "complete solver/control/readback implementation and exact inputs frozen after this native preflight"}},
            "plan": {"path": str(PLAN), "sha256": sha256(PLAN)},
            "registered_project": {
                "project_id": project_id,
                "workspace": str(project_workspace),
                "workspace_role": "actual W24 fixture source/classes/template/save/reopen and science artifacts",
                "authorized_root": str(server.project.resolve(strict=True)),
                "policy_permissions": project_creation["policy"]["permissions"],
                "prebirth_host_grants": prebirth_host_grants,
                "creation_receipt": str(evidence / "project_workspace_created_before_birth.json"),
                "creation_receipt_sha256": sha256(evidence / "project_workspace_created_before_birth.json"),
                "fixture_source": str(fixture_copy),
                "fixture_source_sha256": sha256(fixture_copy),
                "compiled_classes": offline_compile["output_classes"],
            },
            "offline_candidate_freeze": offline_candidate,
            "runtime_environment": runtime_receipt,
            "offline_compile": {"javac": offline_compile["javac_version"],
                                "fixture_sha256": offline_compile["fixture_sha256"],
                                "exit_code": offline_compile["exit_code"]},
            "sources": frozen,
        }
        write_json(evidence / "freeze.json", freeze)
        freeze_sha256 = sha256(evidence / "freeze.json")
        if _frozen_hashes() != frozen:
            raise RuntimeError("a frozen source changed before the native server start")
        verified_project_id, verified_workspace = _verify_registered_workspace(
            project_creation["response"]["data"]["project"], server.project)
        if (verified_project_id != project_id or verified_workspace != project_workspace or
                sha256(fixture_copy) != sha256(FIXTURE)):
            raise RuntimeError("registered project identity/workspace or copied fixture source changed before server birth")
        append_event(events, "candidate_frozen_before_native_birth", freeze_sha256=freeze_sha256,
                     source_names=sorted(frozen))

        # Repeat the mandatory process/listener gate directly before launch.
        birth_inventory = _process_inventory()
        write_json(evidence / "prebirth_inventory.json", birth_inventory)
        if not birth_inventory["quiescent"]:
            raise RuntimeError("fresh pre-birth process/listener inventory is unavailable or not quiescent")
        listener = server.start_and_verify_listener()
        process = server.process_identity or {}
        original_process_identity = process
        birth_value = process.get("birth")
        if not isinstance(birth_value, str):
            raise RuntimeError("owned COMSOL process birth identity is missing")
        engine_birth_epoch = _mac_birth_epoch(birth_value)
        deadline_epoch = engine_birth_epoch + MAX_BIRTH_BUDGET_S
        summary.update({"server_pid": server.proc.pid if server.proc else None,
                        "server_birth": birth_value, "server_command": process.get("command"),
                        "port": server.port, "engine_birth_epoch": engine_birth_epoch,
                        "budget_deadline_epoch": deadline_epoch,
                        "elapsed_from_birth_at_listener_s": round(time.time() - engine_birth_epoch, 3),
                        "freeze_sha256": freeze_sha256,
                        "offline_candidate_freeze_sha256": offline_candidate["sha256"],
                        "native_solve_submissions": 0})
        summary["engine_births"] = 1
        write_json(evidence / "server_birth.json", {
            "status": "OWNED_SERVER_BIRTH_VERIFIED",
            "process_identity": process,
            "listener": listener,
            "birth_epoch": engine_birth_epoch,
            "budget_deadline_epoch": deadline_epoch,
            "budget_s": MAX_BIRTH_BUDGET_S,
        })
        append_event(events, "owned_server_birth_and_loopback_verified", process=process,
                     port=server.port, deadline_epoch=deadline_epoch)
        if time.time() >= deadline_epoch - CLEANUP_RESERVE_S:
            raise TimeoutError("setup-only 15-minute birth budget entered its reserved cleanup window before Worker startup")
        worker_info = _guarded_direct_native_call(
            direct_native_call_guard, evidence, "start_worker/connect_and_version_readback",
            server.start_worker)
        worker_connected = True
        worker_runtime = worker_info.get("worker_runtime", {})
        worker_pid = worker_runtime.get("pid") if isinstance(worker_runtime, dict) else None
        worker_port_value = worker_runtime.get("port") if isinstance(worker_runtime, dict) else None
        if type(worker_pid) is not int or type(worker_port_value) is not int:
            raise RuntimeError("Worker PID/port identity is missing from the owned runtime receipt")
        worker_process_identity = _process_snapshot(worker_pid)
        worker_port = worker_port_value
        if not isinstance(worker_process_identity, dict):
            raise RuntimeError("Worker PID/birth/command identity could not be captured")
        worker_info["worker_process_identity"] = worker_process_identity
        engine_identity = _engine_build_identity(worker_info.get("engine_version"))
        summary["engine_identity"] = engine_identity
        write_json(evidence / "worker_connection.json", worker_info)
        if not engine_identity.get("matches_frozen_target"):
            raise RuntimeError("connected native COMSOL engine does not match frozen 6.4.0.293")

        os.environ["COMSOL_ROOT"] = str(server.shadow_root)
        os.environ["COMSOL_JAVA_HOME"] = str(JAVA_HOME)
        os.environ["COMSOL_PREFS_DIR"] = str(server.prefs)
        os.environ["COMSOL_PROJECT_ROOT"] = str(server.project)
        if os.environ.get(TRUSTED_CODE_STARTUP_ENV, "").strip().lower() not in {"1", "true", "yes"}:
            raise RuntimeError("trusted_code startup grant changed before managed Worker connection")
        os.environ["COMSOL_MCP_ISOLATION_RECEIPT"] = str(server.receipt_path)
        daemon = ControlDaemon(work / "control", worker=server.worker, project_root=server.project)
        actual_host_grants = _require_trusted_code_host_grant(daemon)
        if actual_host_grants != prebirth_host_grants:
            raise RuntimeError("post-Worker trusted_code host grant differs from the prebirth ceiling")
        request_terminal = False
        connection = _dispatch(daemon, "server_connect", {"host": "127.0.0.1", "port": server.port},
                              project_id=project_id, idempotency_key=f"w24-connect-{uuid4()}")
        if connection.get("success") is not True or connection.get("data", {}).get("endpoint") != f"127.0.0.1:{server.port}":
            raise RuntimeError("managed COMSOL server connection did not verify the owned loopback endpoint")
        request_terminal = True
        write_json(evidence / "managed_server_connection.json", connection)

        request_terminal = False
        binding = _dispatch(daemon, "model_create", {"name": "w24_cure_template"},
                            project_id=project_id,
                            idempotency_key=f"w24-model-create-{uuid4()}",
                            request_id=f"w24-model-create-request-{uuid4()}")
        request_terminal = _worker_request_terminal(binding)
        if binding.get("success") is not True:
            raise RuntimeError("project-scoped managed model_create failed: " +
                               json.dumps(binding, ensure_ascii=False, default=str)[:3000])
        bound_model_ref = binding.get("execution", {}).get("model_ref")
        bound_model_tag = bound_model_ref.get("model_tag") if isinstance(bound_model_ref, dict) else None
        if not isinstance(bound_model_tag, str) or not bound_model_tag:
            raise RuntimeError("project-scoped model_create omitted its managed ModelRef")
        initial_project_binding = _require_project_bound_model(daemon, bound_model_ref, project_id)
        request_terminal = True
        model_ref = binding["execution"]["model_ref"]
        revision = int(binding["execution"]["revision"])
        write_json(evidence / "model_created.json", {
            "binding": binding, "persisted_project_binding": initial_project_binding,
            "prebuild_readback": "NOT_REQUIRED_EMPTY_MANAGED_MODEL; fixture build/readback is the native configuration gate",
        })
        if time.time() >= deadline_epoch - CLEANUP_RESERVE_S:
            raise TimeoutError("setup-only birth budget entered cleanup reserve before fixture build")

        request_terminal = False
        build_response = _dispatch(daemon, "operation_call", {
            "operation_id": "code.execute_java",
            "arguments": {"source_artifact": FIXTURE.name,
                          "entrypoint": "W24CureCouponFixture#run",
                          "arguments": {"phase": "build"}, "mode": "trusted"},
        }, project_id=project_id, ref=model_ref, revision=revision,
           idempotency_key=f"w24-build-{uuid4()}", request_id=f"w24-build-request-{uuid4()}",
           rpc_timeout_s=_bounded_rpc_timeout(deadline_epoch, 600.0))
        request_terminal = _worker_request_terminal(build_response)
        build_readback = _java_action_readback(build_response, "native W24 fixture build")
        if build_readback.get("status") != "BUILT_NOT_SOLVED" or build_readback.get("solver_submissions") != 0:
            raise AssertionError("native W24 fixture did not return its explicit no-solve build status")
        if not (build_readback.get("geometry_axisymmetric") is True and
                build_readback.get("geometry_dimension") == 2 and
                build_readback.get("geometry_domain_count") == 4 and
                len(build_readback.get("domain_readbacks", {})) == 4 and
                len(build_readback.get("solver_readbacks", {})) == 3):
            raise AssertionError("native W24 geometry/domain/solver readback gates are incomplete")
        model_ref = build_response["execution"]["model_ref"]
        revision = int(build_response["execution"]["revision"])
        write_json(evidence / "native_build_readback.json", {
            "build_response": build_response,
            "fixture_readback": build_readback,
            "model_ref": model_ref,
            "model_revision": revision,
            "study_run_submissions_so_far": _actual_study_run_submissions(daemon, project_id),
        })
        if time.time() >= deadline_epoch - CLEANUP_RESERVE_S:
            raise TimeoutError("setup-only birth budget entered cleanup reserve before immutable template save")

        expression_inventory_path = project_workspace / "solid_equation_view_expression_inventory.json"
        request_terminal = False
        expression_response = _dispatch(daemon, "operation_call", {
            "operation_id": "code.execute_java",
            "arguments": {"source_artifact": FIXTURE.name,
                          "entrypoint": "W24CureCouponFixture#run",
                          "arguments": {"phase": "expression_inventory",
                                        "output_path": str(expression_inventory_path)},
                          "mode": "trusted"},
        }, project_id=project_id, ref=model_ref, revision=revision,
           idempotency_key=f"w24-expression-inventory-{uuid4()}",
           request_id=f"w24-expression-inventory-request-{uuid4()}",
           rpc_timeout_s=_bounded_rpc_timeout(deadline_epoch, 240.0))
        request_terminal = _worker_request_terminal(expression_response)
        expression_receipt = _java_action_readback(
            expression_response, "native Solid Mechanics Equation View inventory")
        expression_inventory = _verify_expression_inventory(
            expression_receipt, expression_inventory_path)
        durable_inventory_path = evidence / "solid_equation_view_expression_inventory.json"
        inventory_bytes = expression_inventory_path.read_bytes()
        with durable_inventory_path.open("xb") as stream:
            stream.write(inventory_bytes)
            stream.flush()
            os.fsync(stream.fileno())
        expression_inventory["registered_project_path"] = str(expression_inventory_path.resolve(strict=True))
        expression_inventory["evidence_copy_path"] = str(durable_inventory_path.resolve(strict=True))
        expression_inventory["path"] = str(expression_inventory_path.resolve(strict=True))
        if (sha256(durable_inventory_path) != expression_inventory["sha256"] or
                sha256(expression_inventory_path) != expression_inventory["sha256"]):
            raise RuntimeError("registered project and evidence copies of Equation View bytes differ from the native artifact SHA-256")
        model_ref = expression_response["execution"]["model_ref"]
        revision = int(expression_response["execution"]["revision"])
        write_json(evidence / "native_expression_inventory_receipt.json", {
            "worker_response": expression_response,
            "java_receipt": expression_receipt,
            "verified_artifact": expression_inventory,
            "native_study_run_submissions_so_far": _actual_study_run_submissions(daemon, project_id),
        })
        append_event(events, "native_solid_expression_inventory_saved",
                     sha256=expression_inventory["sha256"],
                     expression_rows=expression_inventory["expression_row_count"],
                     candidate_rows=expression_inventory["stress_candidate_row_count"],
                     solver_submissions=0)
        if time.time() >= deadline_epoch - CLEANUP_RESERVE_S:
            raise TimeoutError("setup-only birth budget entered cleanup reserve before immutable template save")

        template_path = project_workspace / "cure_template.mph"
        request_terminal = False
        save_response = _dispatch(daemon, "operation_call", {
            "operation_id": "code.execute_java",
            "arguments": {"source_artifact": FIXTURE.name,
                          "entrypoint": "W24CureCouponFixture#run",
                          "arguments": {"phase": "save", "path": str(template_path)}, "mode": "trusted"},
        }, project_id=project_id, ref=model_ref, revision=revision,
           idempotency_key=f"w24-template-save-{uuid4()}", request_id=f"w24-template-save-request-{uuid4()}",
           rpc_timeout_s=_bounded_rpc_timeout(deadline_epoch, 300.0))
        request_terminal = _worker_request_terminal(save_response)
        save_readback = _java_action_readback(save_response, "native W24 template save")
        if save_readback.get("status") != "SAVED" or not template_path.is_file() or template_path.stat().st_size <= 0:
            raise AssertionError("native W24 configured template MPH was not saved as a non-empty file")
        model_ref = save_response["execution"]["model_ref"]
        revision = int(save_response["execution"]["revision"])
        if not isinstance(model_ref.get("model_tag") if isinstance(model_ref, dict) else None, str):
            raise AssertionError("managed model reference omitted its model tag before save/reopen")
        pre_save_project_binding = _require_project_bound_model(daemon, model_ref, project_id)
        template_receipt = {
            "status": "NATIVE_CONFIGURED_TEMPLATE_SAVED_NOT_SOLVED",
            "path": str(template_path), "size_bytes": template_path.stat().st_size,
            "sha256": sha256(template_path), "save_response": save_response,
            "save_readback": save_readback, "model_ref": model_ref, "model_revision": revision,
            "project_id": project_id,
            "project_workspace": str(project_workspace),
            "project_creation_receipt": str(evidence / "project_workspace_created_before_birth.json"),
            "project_creation_receipt_sha256": sha256(evidence / "project_workspace_created_before_birth.json"),
            "fixture_source_path": str(fixture_copy),
            "fixture_source_sha256": sha256(fixture_copy),
            "fixture_compile_classes": offline_compile["output_classes"],
            "fixture_compile_output_dir": str(project_workspace / "offline-classes"),
            "pre_save_project_binding": pre_save_project_binding,
            "engine_identity": engine_identity, "freeze_sha256": freeze_sha256,
            "runtime_environment": runtime_receipt.get("frozen_runtime_environment"),
            "equation_view_inventory": expression_inventory,
            "native_solver_submissions": _actual_study_run_submissions(daemon, project_id),
        }
        if template_receipt["native_solver_submissions"]:
            raise AssertionError("unexpected study.run Worker submission occurred during preflight")
        write_json(evidence / "configured_template_receipt.json", template_receipt)

        # Reload the immutable MPH in the same sequential Worker session: the
        # setup preflight is frozen to one Worker, while the later science
        # campaign separately allows its staged-baseline reopen in Worker 2.
        if time.time() >= deadline_epoch - CLEANUP_RESERVE_S:
            raise TimeoutError("setup-only birth budget entered cleanup reserve before template reopen")
        reopen_binding = _load_saved_template_managed(
            daemon, project_id=project_id, project_workspace=project_workspace,
            path=template_path,
            idempotency_key=f"w24-template-managed-load-{uuid4()}",
            request_id=f"w24-template-managed-load-request-{uuid4()}",
            timeout_s=_bounded_rpc_timeout(deadline_epoch, 120.0))
        request_terminal = _worker_request_terminal(reopen_binding["load_response"])
        loaded_tag = reopen_binding["model_tag"]
        reopen_ref = reopen_binding["model_ref"]
        reopen_revision = int(reopen_binding["revision"])
        write_json(evidence / "template_reopen_binding.json", reopen_binding)

        if time.time() >= deadline_epoch - CLEANUP_RESERVE_S:
            raise TimeoutError("setup-only birth budget entered cleanup reserve before reopened readback")
        request_terminal = False
        reopen_response = _dispatch(daemon, "operation_call", {
            "operation_id": "code.execute_java",
            "arguments": {"source_artifact": FIXTURE.name,
                          "entrypoint": "W24CureCouponFixture#run",
                          "arguments": {"phase": "readback"}, "mode": "trusted"},
        }, project_id=project_id, ref=reopen_ref, revision=reopen_revision,
           idempotency_key=f"w24-template-reopen-readback-{uuid4()}",
           request_id=f"w24-template-reopen-readback-request-{uuid4()}",
           rpc_timeout_s=_bounded_rpc_timeout(deadline_epoch, 300.0))
        request_terminal = _worker_request_terminal(reopen_response)
        reopened_readback = _java_action_readback(reopen_response, "native W24 reloaded template readback")
        reopen_comparison = _compare_reopened_readback(build_readback, reopened_readback)
        if not reopen_comparison["matches"]:
            raise AssertionError("saved/reopened MPH configuration readbacks differ: " +
                                 json.dumps(reopen_comparison, ensure_ascii=False))
        model_ref = reopen_response["execution"]["model_ref"]
        revision = int(reopen_response["execution"]["revision"])

        reopened_inventory_path = project_workspace / "solid_equation_view_expression_inventory_reopened.json"
        request_terminal = False
        reopened_expression_response = _dispatch(daemon, "operation_call", {
            "operation_id": "code.execute_java",
            "arguments": {"source_artifact": FIXTURE.name,
                          "entrypoint": "W24CureCouponFixture#run",
                          "arguments": {"phase": "expression_inventory",
                                        "output_path": str(reopened_inventory_path)},
                          "mode": "trusted"},
        }, project_id=project_id, ref=model_ref, revision=revision,
           idempotency_key=f"w24-template-reopen-expression-inventory-{uuid4()}",
           request_id=f"w24-template-reopen-expression-inventory-request-{uuid4()}",
           rpc_timeout_s=_bounded_rpc_timeout(deadline_epoch, 240.0))
        request_terminal = _worker_request_terminal(reopened_expression_response)
        reopened_expression_java_receipt = _java_action_readback(
            reopened_expression_response, "reopened W24 Solid Mechanics Equation View inventory")
        reopened_expression_inventory = _verify_expression_inventory(
            reopened_expression_java_receipt, reopened_inventory_path)
        original_equation_view = json.loads(
            durable_inventory_path.read_text(encoding="utf-8"))
        reopened_equation_view = json.loads(
            reopened_inventory_path.read_text(encoding="utf-8"))
        equation_tables_match = (
            original_equation_view.get("feature_tables") ==
            reopened_equation_view.get("feature_tables"))
        if not equation_tables_match:
            raise AssertionError("Solid Mechanics Equation View rows changed across MPH save/reopen")
        durable_reopened_inventory = evidence / "solid_equation_view_expression_inventory_reopened.json"
        with durable_reopened_inventory.open("xb") as stream:
            stream.write(reopened_inventory_path.read_bytes())
            stream.flush()
            os.fsync(stream.fileno())
        if sha256(durable_reopened_inventory) != reopened_expression_inventory["sha256"]:
            raise RuntimeError("durable reopened Equation View artifact differs from native table bytes")
        reopened_expression_inventory.update({
            "registered_project_path": str(reopened_inventory_path.resolve(strict=True)),
            "evidence_copy_path": str(durable_reopened_inventory.resolve(strict=True)),
            "path": str(reopened_inventory_path.resolve(strict=True)),
            "matches_pre_save_feature_tables": True,
        })
        write_json(evidence / "native_reopened_expression_inventory_receipt.json", {
            "worker_response": reopened_expression_response,
            "java_receipt": reopened_expression_java_receipt,
            "verified_artifact": reopened_expression_inventory,
            "pre_save_artifact_sha256": expression_inventory["sha256"],
            "feature_tables_match": equation_tables_match,
            "native_study_run_submissions_so_far": _actual_study_run_submissions(daemon, project_id),
        })
        model_ref = reopened_expression_response["execution"]["model_ref"]
        revision = int(reopened_expression_response["execution"]["revision"])

        write_json(evidence / "native_template_reopen_readback.json", {
            "status": "NATIVE_TEMPLATE_REOPEN_READBACK_PASS_NOT_SOLVED",
            "saved_template_path": str(template_path),
            "saved_template_sha256": template_receipt["sha256"],
            "loaded_tag": loaded_tag,
            "managed_model_load": reopen_binding,
            "reopen_response": reopen_response,
            "fixture_readback": reopened_readback,
            "configuration_comparison": reopen_comparison,
            "equation_view_feature_tables_match": equation_tables_match,
            "reopened_equation_view_inventory": reopened_expression_inventory,
            "study_run_submissions_so_far": _actual_study_run_submissions(daemon, project_id),
        })
        if _actual_study_run_submissions(daemon, project_id):
            raise AssertionError("unexpected study.run Worker submission occurred during template reopen")
        template_receipt["reopen_status"] = "NATIVE_TEMPLATE_REOPEN_READBACK_PASS_NOT_SOLVED"
        template_receipt["reopened_model_tag"] = loaded_tag
        template_receipt["reopened_model_ref"] = reopen_ref
        template_receipt["reopened_model_revision"] = reopen_revision
        template_receipt["reopened_model_binding"] = reopen_binding["persisted_project_binding"]
        template_receipt["reopen_readback"] = str(evidence / "native_template_reopen_readback.json")
        template_receipt["reopened_equation_view_inventory"] = reopened_expression_inventory
        write_json(evidence / "configured_template_receipt.json", template_receipt)

        summary.update({"status": "NATIVE_SETUP_PREFLIGHT_PASS_NOT_SOLVED",
                        "template_path": str(template_path),
                        "template_size_bytes": template_path.stat().st_size,
                        "template_sha256": template_receipt["sha256"],
                        "template_reopen_status": template_receipt["reopen_status"],
                        "reopened_model_tag": loaded_tag,
                        "fixture_readback": build_readback,
                        "equation_view_inventory": expression_inventory,
                        "reopened_fixture_readback": reopened_readback,
                        "template_reopen_comparison": reopen_comparison,
                        "reopened_equation_view_inventory": reopened_expression_inventory,
                        "model_ref": model_ref, "model_revision": revision,
                        "native_solve_submissions": 0,
                        "acceptance_scope": "geometry/physics/mesh/solver configuration and axisymmetric volume pre-solve gates only"})
        status = "PRE_SOLVE_GATES_PASSED_NOT_SOLVED"
        append_event(events, "native_build_readback_and_template_saved", template_sha256=template_receipt["sha256"],
                     solver_submissions=0)
    except Exception as exc:
        status = "FAIL_OR_INCOMPLETE"
        summary.update({"status": status, "error": f"{type(exc).__name__}: {exc}",
                        "traceback": traceback.format_exc(),
                        "engine_births": int(server is not None and server.proc is not None),
                        "native_solve_submissions": (len(_actual_study_run_submissions(daemon, project_id))
                                                     if daemon and project_id else 0)})
        runtime_receipt_path = evidence / "python_runtime_preflight.json"
        if runtime_receipt_path.is_file():
            try:
                summary["python_runtime_preflight"] = json.loads(
                    runtime_receipt_path.read_text(encoding="utf-8")).get("status")
            except (OSError, json.JSONDecodeError):
                summary["python_runtime_preflight"] = "RECEIPT_UNREADABLE"
        append_event(events, "preflight_failed_or_incomplete", error=summary["error"],
                     solver_submissions=summary["native_solve_submissions"])
    finally:
        summary["at_utc_end"] = utc_now()
        cleanup_reconciliation: dict[str, Any] | None = None
        # Full paged ledger reconciliation supersedes a stale local RPC flag.
        # A durable UNKNOWN job/result remains in the receipt, while cleanup
        # depends on every submitted Worker request being observed terminal and
        # there being no queued/running project job.
        if daemon is not None:
            try:
                job_inventory = _project_job_inventory(daemon, project_id)
                worker_inventory = _worker_request_activity_inventory(daemon.store, project_id)
                write_json(evidence / "precleanup_job_inventory.json", job_inventory)
                write_json(evidence / "precleanup_worker_request_inventory.json", worker_inventory)
                summary["project_jobs"] = job_inventory["jobs"]
                summary["active_project_jobs"] = job_inventory["active"]
                summary["unknown_project_jobs"] = job_inventory["unknown"]
                summary["worker_request_inventory"] = worker_inventory
                cleanup_reconciliation = _cleanup_reconciliation(
                    job_inventory, worker_inventory,
                    direct_native_calls_safe=(direct_native_call_guard.get("all_returned") is True and
                                              all(row.get("status") == "RETURNED"
                                                  for row in direct_native_call_guard.get("calls", []))))
                cleanup_reconciliation["durable_result"]["prior_local_terminal_flag"] = request_terminal
                cleanup_reconciliation["durable_result"]["direct_native_call_guard_status"] = direct_native_call_guard.get("status")
                write_json(evidence / "worker_ledger_reconciliation.json", cleanup_reconciliation)
                request_terminal = cleanup_reconciliation["safe_for_owned_cleanup"] is True
            except Exception as exc:
                summary["precleanup_inventory_error"] = f"{type(exc).__name__}: {exc}"
                request_terminal = False
        else:
            # Before ControlDaemon creation, no managed Worker request can have
            # been dispatched. The synchronous owned Worker startup must still
            # have returned successfully before its process may be stopped.
            checks = {
                "all_submissions_observed_once": True,
                "all_observed_workers_terminal": True,
                "no_orphan_observations": True,
                "no_ambiguous_worker_ids": True,
                "no_queued_or_running_jobs": True,
                "all_direct_native_calls_returned": (
                    direct_native_call_guard.get("all_returned") is True and
                    all(row.get("status") == "RETURNED"
                        for row in direct_native_call_guard.get("calls", []))),
            }
            cleanup_reconciliation = {
                "status": "NO_MANAGED_REQUESTS_DISPATCHED",
                "safe_for_owned_cleanup": all(checks.values()),
                "durable_result": {"project_jobs_unknown_preserved": [],
                                    "execution_state_unknown_request_ids": []},
                "checks": checks,
            }
            write_json(evidence / "worker_ledger_reconciliation.json", cleanup_reconciliation)
            request_terminal = cleanup_reconciliation["safe_for_owned_cleanup"] is True
        if server is not None:
            if server.proc is None:
                cleanup = {"status": "NOT_STARTED", "reason": "owned COMSOL process was never created"}
            elif request_terminal and isinstance(original_process_identity, dict):
                try:
                    from tools.run_native_w23_te_managed_preflight import _exact_owned_cleanup
                    cleanup = _exact_owned_cleanup(
                        server, server_identity=original_process_identity,
                        worker_identity=worker_process_identity, worker_port=worker_port,
                        process_snapshot=_process_snapshot,
                        reconciliation=cleanup_reconciliation or {}, evidence=evidence,
                        require_fixture_terminal=False)
                except Exception as exc:
                    cleanup = {"status": "CLEANUP_UNVERIFIED", "error": f"{type(exc).__name__}: {exc}"}
            else:
                cleanup = {"status": "LEFT_RUNNING_ACTIVE_OR_UNKNOWN",
                           "pid": server.proc.pid if server.proc else None,
                           "port": server.port,
                           "action": "preserved because full terminal ledger or exact process identity proof is incomplete",
                           "worker_ledger_reconciliation": cleanup_reconciliation,
                           "server_identity": original_process_identity,
                           "worker_identity": worker_process_identity}
        summary["cleanup"] = cleanup
        if cleanup and cleanup.get("status") not in {
                "CLEANUP_VERIFIED_EXACT_OWNED_PIDS_AND_PORTS_ABSENT", "NOT_STARTED"}:
            summary["status"] = "FAIL_OR_INCOMPLETE"
        try:
            summary["native_solve_submissions"] = (len(_actual_study_run_submissions(daemon, project_id))
                                                    if daemon and project_id else 0)
        except Exception as exc:
            summary["native_solve_submissions"] = None
            summary["study_submission_inventory_error"] = f"{type(exc).__name__}: {exc}"
            summary["status"] = "LEFT_RUNNING_ACTIVE_OR_UNKNOWN"
        if summary.get("active_project_jobs") or summary.get("precleanup_inventory_error"):
            summary["status"] = "LEFT_RUNNING_ACTIVE_OR_UNKNOWN"
        if summary.get("unknown_project_jobs"):
            summary["durable_unknown_jobs_preserved"] = True
            if summary.get("status") not in {"LEFT_RUNNING_ACTIVE_OR_UNKNOWN"}:
                summary["status"] = "FAIL_OR_INCOMPLETE"
        if cleanup and cleanup.get("status") == "CLEANUP_VERIFIED_EXACT_OWNED_PIDS_AND_PORTS_ABSENT":
            try:
                post_inventory = _process_inventory()
                write_json(evidence / "postcleanup_inventory.json", post_inventory)
                summary["postcleanup_inventory_quiescent"] = post_inventory["quiescent"]
                if not post_inventory["quiescent"]:
                    summary["status"] = "FAIL_OR_INCOMPLETE"
            except Exception as exc:
                summary["postcleanup_inventory_error"] = f"{type(exc).__name__}: {exc}"
                summary["status"] = "FAIL_OR_INCOMPLETE"
            if daemon is not None:
                try:
                    daemon.close()
                except Exception as exc:
                    summary["daemon_close_error"] = f"{type(exc).__name__}: {exc}"
                    summary["status"] = "FAIL_OR_INCOMPLETE"
        write_json(evidence / "summary.json", summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", required=True)
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--candidate-freeze", help="path to the reviewed offline setup-candidate freeze JSON")
    parser.add_argument("--expected-candidate-sha256", help="exact SHA-256 of --candidate-freeze")
    args = parser.parse_args()
    result = run(args)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str, allow_nan=False))
    return 0 if result.get("status") == "NATIVE_SETUP_PREFLIGHT_PASS_NOT_SOLVED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
