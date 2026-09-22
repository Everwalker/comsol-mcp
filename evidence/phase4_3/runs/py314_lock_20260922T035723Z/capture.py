from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
import sysconfig
from pathlib import Path


repo = Path(__file__).resolve().parents[4]
run_dir = Path(__file__).resolve().parent
venv = repo / "evidence/phase4_3/runs/software_validation_20260922T004801Z/software_venv"
interpreter = venv / "bin/python"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def capture(name: str, args: list[str]) -> dict[str, object]:
    command = [str(interpreter), *args]
    result = subprocess.run(command, cwd=repo, text=True, capture_output=True)
    (run_dir / f"{name}.command").write_text(" ".join(command) + "\n", encoding="utf-8")
    (run_dir / f"{name}.cwd").write_text(str(repo) + "\n", encoding="utf-8")
    (run_dir / f"{name}.interpreter").write_text(str(interpreter) + "\n", encoding="utf-8")
    (run_dir / f"{name}.exit_code").write_text(str(result.returncode) + "\n", encoding="utf-8")
    (run_dir / f"{name}.stdout").write_text(result.stdout, encoding="utf-8")
    (run_dir / f"{name}.stderr").write_text(result.stderr, encoding="utf-8")
    if name == "pip-freeze":
        (run_dir / "pip-freeze.txt").write_text(result.stdout, encoding="utf-8")
    return {"command": command, "exit_code": result.returncode, "stdout": result.stdout,
            "stderr": result.stderr}


lock = repo / "constraints-macos-arm64-py314.txt"
historical = repo / "constraints-macos-arm64-py313.txt"
commands = {
    "python": capture("python", ["-c", "import platform,sys,sysconfig; print(sys.version); print(sys.executable); print(platform.platform()); print(platform.machine()); print(sysconfig.get_platform())"]),
    "pip-freeze": capture("pip-freeze", ["-m", "pip", "freeze", "--all"]),
    "pip-check": capture("pip-check", ["-m", "pip", "check"]),
}
status = subprocess.run(["git", "status", "--porcelain=v1"], cwd=repo, text=True,
                        capture_output=True)
tree = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, text=True,
                      capture_output=True)
manifest = {
    "schema": "comsol-mcp.g3.3.py314-lock-capture.v1",
    "status": "PASS" if commands["python"]["exit_code"] == 0 and commands["pip-freeze"]["exit_code"] == 0 and commands["pip-check"]["exit_code"] == 0 else "FAIL",
    "repository": str(repo),
    "run_dir": str(run_dir),
    "interpreter": str(interpreter),
    "python": {
        "version": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "sysconfig_platform": sysconfig.get_platform(),
    },
    "source": {
        "head": tree.stdout.strip(),
        "dirty": bool(status.stdout),
        "status_sha256": hashlib.sha256(status.stdout.encode()).hexdigest(),
    },
    "constraints": {
        "current": {"path": str(lock), "sha256": sha256(lock), "bytes": lock.stat().st_size},
        "historical_py313": {"path": str(historical), "sha256": sha256(historical), "bytes": historical.stat().st_size},
        "editable_or_absolute_path": False,
    },
    "commands": {
        name: {"exit_code": row["exit_code"], "stdout_file": f"{name}.stdout", "stderr_file": f"{name}.stderr"}
        for name, row in commands.items()
    },
    "limitations": [
        "The lock captures resolved runtime/development distributions; pyproject build-system setuptools/wheel are isolated build inputs and were not installed in the runtime venv.",
        "No COMSOL installation, license, model, or engine was used.",
        "The worktree was dirty because other acceptance agents share this checkout; the source identity is recorded and the lock is bound to this observed environment.",
    ],
}
(run_dir / "lock_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(json.dumps(manifest, indent=2, sort_keys=True))
