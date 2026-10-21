#!/usr/bin/env python3
"""Run the baseline unit suite and retain a five-part no-engine evidence case."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN_ID = os.environ.get("W01_RUN_ID", "manual")
CASE = ROOT / "evidence" / "w01" / "runs" / RUN_ID / "cases" / "pytest_baseline"


def write(name: str, content: object) -> None:
    (CASE / name).write_text(json.dumps(content, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    CASE.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-m", "pytest", "-q"]
    write("environment.json", {"timestamp_utc": datetime.now(timezone.utc).isoformat(), "python": sys.version, "cwd": str(ROOT), "engine": "NOT_RUN: unit tests do not start COMSOL."})
    write("request.json", {"command": command})
    completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
    write("result.json", {"returncode": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr})
    write("assertions.json", {"pytest_exit_zero": completed.returncode == 0, "engine_action": "NOT_RUN"})
    (CASE / "engine.log").write_text("NOT_RUN: baseline unit tests do not start COMSOL.\n", encoding="utf-8")
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
