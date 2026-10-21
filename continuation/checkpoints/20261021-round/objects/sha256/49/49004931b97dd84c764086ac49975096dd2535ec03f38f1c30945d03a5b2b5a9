"""Real Python subprocess imports and audit logger; no SDK/native process."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import pytest

from tools import run_native_w23_saved_mph_diagnostic as runner


@pytest.mark.parametrize("mode", ["correct", "wrong_env", "late_import"])
@pytest.mark.skipif(sys.platform != "darwin", reason="fresh native namespace/APFS guard is macOS-only")
def test_actual_import_roots_and_model_load_logger_beforebirth(mode):
    repo = runner.REPO
    source = repo / "comsol_mcp/_server.py"
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    with tempfile.TemporaryDirectory(prefix="w23-import-home-", dir="/private/tmp") as directory:
        namespace = Path(directory)
        namespace.chmod(0o700)
        correct = namespace / "work/mcp-home"
        wrong = namespace / "wrong-home"
        env = dict(os.environ, PYTHONPATH=str(repo), PYTHONDONTWRITEBYTECODE="1",
                   COMSOL_SERVER_MCP_HOME=str(correct if mode == "correct" else wrong))
        program = '''
import json,logging,os,sys
from pathlib import Path
os.umask(0o077)
from tools import run_native_w23_saved_mph_diagnostic as runner
namespace=Path(sys.argv[1]); mode=sys.argv[2]
if mode == "late_import":
    from comsol_mcp import _server
    os.environ["COMSOL_SERVER_MCP_HOME"]=str(namespace/"work/mcp-home")
try:
    receipt=runner._validate_fresh_import_home({"namespace":str(namespace)},namespace/"work")
except RuntimeError as exc:
    print(json.dumps({"status":"REFUSED_BEFOREBIRTH","reason":str(exc)}))
else:
    from comsol_mcp import _server, _tools_model
    _server._setup_logging()
    logging.info("model_load pre-RPC audit initialization software probe")
    print(json.dumps({"status":"PASS_SOFTWARE_IMPORT_LOGGING","receipt":receipt,
                      "log_exists":(namespace/"work/mcp-home/logs/server.log").is_file()}))
'''
        result = subprocess.run([sys.executable, "-c", program, str(namespace), mode], env=env, cwd=repo,
                                text=True, capture_output=True, timeout=20)
        assert result.returncode == 0, result.stderr
        value = json.loads(result.stdout)
        if mode == "correct":
            assert value["status"] == "PASS_SOFTWARE_IMPORT_LOGGING"
            assert value["log_exists"]
            assert not wrong.exists()
        else:
            assert value["status"] == "REFUSED_BEFOREBIRTH"
            assert not correct.exists()
            assert not wrong.exists()
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before


def test_fresh_argv_environment_and_legacy_bytes_are_separate():
    paths = [Path("/private/tmp/test") / name for name in ("freeze", "approval", "model", "work", "evidence")]
    binding = {"namespace": "/private/tmp/test", "python": {"entry": "/private/tmp/python/bin/python"}}
    fresh = runner._native_argv(paths[0], "sha", paths[1], paths[2], paths[3], paths[4], binding)
    legacy = runner._native_argv(paths[0], "sha", paths[1], paths[2], paths[3], paths[4])
    assignment = "COMSOL_SERVER_MCP_HOME=/private/tmp/test/work/mcp-home"
    assert fresh.index(assignment) < fresh.index(binding["python"]["entry"])
    assert not any("COMSOL_SERVER_MCP_HOME" in token for token in legacy)
    assert runner._native_environment(binding)["COMSOL_SERVER_MCP_HOME"] == assignment.split("=", 1)[1]
    assert "COMSOL_SERVER_MCP_HOME" not in runner._native_environment()


def test_fresh_root_approval_requires_exact_import_home():
    binding = {"namespace": "/private/tmp/test", "activity_id": "86e0f892-46b4-40aa-8e78-b23a88f137db", "binding_sha256": "binding"}
    freeze = {"fresh_runtime_binding": binding, "source_manifest_sha256": "source", "read_only_call_plan": {}}
    argv = ["explicit-frozen-argv"]
    approval = {"status": "ROOT_APPROVED_FOR_EXACT_FROZEN_CANDIDATE", "activity_id": runner._activity(freeze),
        "candidate_id": runner.CANDIDATE_ID, "candidate_revision": runner.CANDIDATE_REVISION,
        "freeze_sha256": "freeze", "source_manifest_sha256": "source", "native_argv_sha256": runner.sha256_json(argv),
        "max_owned_server_processes": 1, "max_worker_sessions": 1, "max_gui_processes": 0,
        "max_study_run_calls": 0, "max_solver_run_calls": 0, "max_seconds_from_server_birth": int(runner.MAX_BIRTH_SECONDS),
        "cleanup_reserve_seconds": int(runner.CLEANUP_RESERVE_SECONDS), "retry_unknown_or_nonterminal_worker_request": False,
        "read_only_call_plan_sha256": runner.sha256_json({}), "runtime_binding_sha256": "binding",
        "exact_native_invocation": {"argv": argv, "cwd": str(runner.REPO), "environment": runner._native_environment(binding)}}
    runner._validate_approval(approval, freeze, "freeze", argv)
    approval["exact_native_invocation"]["environment"]["COMSOL_SERVER_MCP_HOME"] = str(runner.REPO / "comsol-server-home")
    with pytest.raises(RuntimeError, match="exact_native_invocation.environment"):
        runner._validate_approval(approval, freeze, "freeze", argv)
