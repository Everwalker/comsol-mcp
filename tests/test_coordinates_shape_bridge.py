"""Offline contract checks for the shape-only NumericalFeature bridge."""

from __future__ import annotations

import re
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
    shape_body = source[source.index("private Object getCoordinatesShape"):source.index("// ---- G3 R02", source.index("private Object getCoordinatesShape"))]
    assert ".getData(" not in shape_body


def test_shape_alias_does_not_replace_the_raw_coordinate_getter_contract() -> None:
    source = JAVA_SOURCE.read_text(encoding="utf-8")
    # The generic getCoordinates method remains a separately allowlisted API;
    # the alias is an additional shape-only path used before getData budgeting.
    assert '"getCoordinates", "getCoordinatesShape", "getNData"' in source
    assert '"source", "native NumericalFeature.getCoordinates()"' in source
