"""Nested COMSOL API interface proxy test for the real Java walk; no engine."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from tests.test_t13_java_walk_getter_failure_20261109 import REPO, WORKER_SOURCE, _java_toolchain


def test_nested_walk_surfaces_completed_getter_failures(tmp_path: Path):
    javac, java, classpath = _java_toolchain()
    harness = REPO / "tests/java/PersistentComsolWorkerNestedWalkHarness.java"
    classes = tmp_path / "nested-walk-classes"
    classes.mkdir()
    compiled = subprocess.run(
        [javac, "-encoding", "UTF-8", "-classpath", classpath, "-d", str(classes), str(WORKER_SOURCE), str(harness)],
        cwd=REPO, capture_output=True, text=True, timeout=240,
    )
    assert compiled.returncode == 0, f"COMPILE failure, not behavioural RED:\n{compiled.stdout}\n{compiled.stderr}"
    result = subprocess.run(
        [java, "-cp", str(classes) + os.pathsep + classpath,
         "comsol_mcp.worker_java.PersistentComsolWorkerNestedWalkHarness"],
        cwd=REPO, capture_output=True, text=True, timeout=180,
    )
    for scenario in ("none", "getType", "label", "tag"):
        assert f"SCENARIO {scenario} reached=true" in result.stdout, result.stdout + result.stderr
    assert "SCENARIO none reached=true" in result.stdout and "NESTED_WALK_PASS" in result.stdout, result.stdout + result.stderr
    assert result.returncode == 0, result.stdout + result.stderr
