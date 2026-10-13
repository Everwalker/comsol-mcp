"""Offline contract checks for the shape-only NumericalFeature bridge."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from comsol_mcp._domain_outcome import is_mutation_call


REPO = Path(__file__).resolve().parents[1]
JAVA_SOURCE = REPO / "comsol_mcp" / "worker_java" / "PersistentComsolWorker.java"


def _worker_methods() -> set[str]:
    source = JAVA_SOURCE.read_text(encoding="utf-8")
    block = re.search(r"METHODS = new HashSet<>\(Arrays\.asList\((.*?)\)\);", source, re.S)
    assert block is not None
    return set(re.findall(r'"([A-Za-z_][A-Za-z0-9_]*)"', block.group(1)))


def test_shape_alias_is_allowlisted_and_classified_as_read_only() -> None:
    assert "getCoordinatesShape" in _worker_methods()
    assert is_mutation_call("getCoordinatesShape") is False


def test_shape_alias_checks_handle_then_calls_native_getcoordinates_without_wire_values() -> None:
    source = JAVA_SOURCE.read_text(encoding="utf-8")
    assert 'if ("getCoordinatesShape".equals(method)) {' in source
    assert 'if (!args.isEmpty()) throw new IllegalArgumentException("getCoordinatesShape takes no arguments");' in source
    assert "return getCoordinatesShape(target);" in source
    assert "target instanceof NumericalFeature" in source
    assert "((NumericalFeature) target).getCoordinates()" in source
    assert '"values_transmitted", false' in source
    assert '"wire_payload", "shape-only"' in source
    assert '"COORDINATES_SHAPE_RAGGED"' in source
    assert '"COORDINATES_SHAPE_NULL_ROW"' in source
    assert '"COORDINATES_SHAPE_NONFINITE"' in source
    shape_start = source.index("private Object getCoordinatesShape")
    shape_body = source[shape_start:source.index("private Object getStrictFieldReadback", shape_start)]
    assert ".getData(" not in shape_body


def test_shape_alias_does_not_replace_the_raw_coordinate_getter_contract() -> None:
    source = JAVA_SOURCE.read_text(encoding="utf-8")
    # The generic getCoordinates method remains a separately allowlisted API;
    # the alias is an additional shape-only path used before getData budgeting.
    assert '"getCoordinates", "getCoordinatesShape", "getNData"' in source
    assert '"source", "native NumericalFeature.getCoordinates()"' in source


def test_strict_w21_field_alias_is_worker_bounded_and_not_a_generic_getter() -> None:
    source = JAVA_SOURCE.read_text(encoding="utf-8")
    assert "getStrictFieldReadback" in _worker_methods()
    assert 'if ("getStrictFieldReadback".equals(method)) {' in source
    assert "return getStrictFieldReadback(target);" in source
    assert "strictFieldPayload(real, imaginary, coordinates, complex" in source
    assert "W21_FIELD_MAX_NUMERIC_SCALARS = 65_536L" in source
    assert "W21_FIELD_MAX_JSON_BYTES = 8 * 1024 * 1024" in source
    assert "strict field JSON payload exceeds the byte limit" in source


def test_strict_w21_worker_payload_harness_executes_fixed_limits_and_negative_cases(tmp_path: Path) -> None:
    javac = shutil.which("javac")
    java = shutil.which("java")
    if javac is None or java is None:
        import pytest
        pytest.skip("NOT_RUN: Java compiler/runtime unavailable for strict Worker payload contract")
    comsol_jars = Path("/Applications/COMSOL64/Multiphysics/apiplugins")
    jars = sorted(comsol_jars.glob("*.jar")) if comsol_jars.is_dir() else []
    if not jars:
        import pytest
        pytest.skip("NOT_RUN: COMSOL Java API classpath unavailable for strict Worker payload contract")
    import os
    import pytest

    classpath = os.pathsep.join(str(path) for path in jars)
    classes = tmp_path / "java-classes"
    classes.mkdir()
    harness = REPO / "tests" / "java" / "W21StrictFieldPayloadHarness.java"
    compile_result = subprocess.run(
        [javac, "-encoding", "UTF-8", "-classpath", classpath, "-d", str(classes),
         str(JAVA_SOURCE), str(harness)],
        cwd=REPO, capture_output=True, text=True, timeout=90,
    )
    assert compile_result.returncode == 0, compile_result.stderr
    execution = subprocess.run(
        [java, "-cp", str(classes) + os.pathsep + classpath,
         "comsol_mcp.worker_java.W21StrictFieldPayloadHarness"],
        cwd=REPO, capture_output=True, text=True, timeout=30,
    )
    assert execution.returncode == 0, execution.stderr
    assert execution.stdout.strip() == "W21_STRICT_FIELD_PAYLOAD_PASS"
