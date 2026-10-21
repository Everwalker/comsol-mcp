"""Production Worker compileSnapshot check for isolated W24 Java artifacts."""
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_COMSOL = Path("/Applications/COMSOL64/Multiphysics")
DEFAULT_JDK11 = Path("/Library/Java/JavaVirtualMachines/amazon-corretto-11.jdk/Contents/Home")


def _java_major(home):
    release = home / "release"
    if not release.is_file():
        return None
    match = re.search(r'^JAVA_VERSION="(\d+)', release.read_text(encoding="utf-8"), re.MULTILINE)
    return int(match.group(1)) if match else None


def test_production_compile_snapshot_compiles_and_tests_each_w24_artifact_alone(tmp_path):
    comsol_root = Path(os.environ.get("COMSOL_ROOT", str(DEFAULT_COMSOL))).expanduser()
    configured_jdk = os.environ.get("COMSOL_TEST_JDK11") or os.environ.get("COMSOL_JAVA_HOME") or os.environ.get("JAVA_HOME")
    jdk = Path(configured_jdk).expanduser() if configured_jdk else DEFAULT_JDK11
    if _java_major(jdk) != 11:
        pytest.skip("SKIP_ENV/NOT_RUN: exact Corretto/JDK 11 compiler unavailable")
    java = jdk / "bin/java"
    javac = jdk / "bin/javac"
    jars = sorted(path for path in (comsol_root / "apiplugins").glob("*.jar")
                  if not path.name.startswith("._"))
    if not java.is_file() or not javac.is_file() or not jars:
        pytest.skip("SKIP_ENV/NOT_RUN: COMSOL 6.4 API jars or JDK 11 executables unavailable")

    production_worker = ROOT / "comsol_mcp/worker_java/PersistentComsolWorker.java"
    harness = ROOT / "tests/java/PersistentComsolWorkerW24SingleSourceHarness.java"
    classpath = os.pathsep.join(map(str, jars))
    driver_classes = tmp_path / "driver-classes"
    driver_classes.mkdir()
    compile_driver = subprocess.run(
        [str(javac), "-encoding", "UTF-8", "-classpath", classpath, "-d", str(driver_classes),
         str(production_worker), str(harness)],
        capture_output=True, text=True, timeout=120,
    )
    (tmp_path / "javac-driver.log").write_text(compile_driver.stdout + compile_driver.stderr, encoding="utf-8")
    assert compile_driver.returncode == 0, compile_driver.stdout + compile_driver.stderr

    runtime_classpath = str(driver_classes) + os.pathsep + classpath
    result = subprocess.run(
        [str(java), "-cp", runtime_classpath,
         "comsol_mcp.worker_java.PersistentComsolWorkerW24SingleSourceHarness",
         str(ROOT), str(tmp_path / "individual-artifact-outputs")],
        capture_output=True, text=True, timeout=120,
    )
    (tmp_path / "single-source-harness.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS_W24_SINGLE_SOURCE_COMPILE_AND_HELPER_CONTROLS" in result.stdout
    assert "engine=NOT_RUN Worker_constructor=NOT_RUN model=NOT_RUN" in result.stdout
