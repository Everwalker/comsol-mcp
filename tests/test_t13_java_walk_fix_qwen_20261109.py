"""T13 walk getter-fix controls (qwen, 2026-11-09).

Drives ``tests/java/PersistentComsolWorkerWalkFixControlHarness.java`` against
the real ``PersistentComsolWorker.walk(Map)`` in an offline JVM with JDK-proxy
receivers over the installed COMSOL API interfaces only: no Worker
constructor, no service, no socket, no COMSOL process, no engine.  This is
software-harness evidence, NOT native COMSOL evidence, and it does not claim
the Python fallback/UNKNOWN or cursor-completeness gaps were touched.

Pinned controls (details in the Java file):
  A1 clean nested traversal + normal API absence (Model has no getType) stays
     errors=[] complete=true;
  B1 a declared getter whose body itself throws NoSuchMethodException must be
     recorded (exception NAME alone must not misclassify it as API absence);
  C1 a wrapped completed fault (undeclared checked exception surfaced as
     UndeclaredThrowableException) must be recorded without leaking the
     exception message (SENTINEL must never appear in the reply);
  D1 90 completed faults keep the reply error list bounded at the existing
     50-entry cap and complete=false;
  E1 a getter returning null stays clean (no blanket traversal errors).
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tests.test_t13_java_walk_getter_failure_20261109 import (
    REPO,
    WORKER_SOURCE,
    _java_toolchain,
    _sha256,
)

HARNESS = REPO / "tests/java/PersistentComsolWorkerWalkFixControlHarness.java"
HARNESS_CLASS = "comsol_mcp.worker_java.PersistentComsolWorkerWalkFixControlHarness"


def test_java_walk_fix_control_harness(tmp_path: Path):
    javac, java, classpath = _java_toolchain()
    worker_sha_before = _sha256(WORKER_SOURCE)
    harness_sha_before = _sha256(HARNESS)

    classes = tmp_path / "walk-fix-control-classes"
    classes.mkdir()
    compiled = subprocess.run(
        [javac, "-encoding", "UTF-8", "-classpath", classpath, "-d", str(classes),
         str(WORKER_SOURCE), str(HARNESS)],
        cwd=REPO, capture_output=True, text=True, timeout=240,
    )
    assert compiled.returncode == 0, (
        "COMPILE failure (harness/worker source or classpath problem, not the "
        "behaviour under test): " + compiled.stdout + compiled.stderr
    )

    execution = subprocess.run(
        [java, "-cp", str(classes) + os.pathsep + classpath, HARNESS_CLASS],
        cwd=REPO, capture_output=True, text=True, timeout=180,
    )
    worker_sha_after = _sha256(WORKER_SOURCE)
    assert worker_sha_after == worker_sha_before, (
        f"Worker source changed during the harness run: {worker_sha_before} -> {worker_sha_after}")
    assert _sha256(HARNESS) == harness_sha_before, "control harness changed during its own run"

    raw = f"\n--- harness exit={execution.returncode} ---\n{execution.stdout}\n--- stderr ---\n{execution.stderr}\n"

    for scenario in ("A1", "B1", "C1", "D1", "E1"):
        assert f"CTRL id={scenario} " in execution.stdout, (
            f"harness did not execute control {scenario}; every control must run even after earlier "
            f"failures{raw}")
    assert "sentinelInReply=false" in execution.stdout, (
        "every control line must report sentinelInReply=false; an exception-detail leak is visible "
        f"in the machine lines{raw}")
    assert "SENTINEL" not in execution.stdout, (
        f"getter exception detail leaked into harness stdout (reply or ASSERT-FAIL rendering){raw}")
    assert "WALKFIX_CTRL_PASS" in execution.stdout, (
        "fix-control contract NOT satisfied by this Worker build; see CTRL/ASSERT-FAIL lines" + raw)
    assert execution.returncode == 0, (
        f"control harness reported behavioural failures (exit {execution.returncode}){raw}")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
