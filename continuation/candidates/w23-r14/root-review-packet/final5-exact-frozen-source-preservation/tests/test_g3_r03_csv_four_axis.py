"""Tests for R03: CSV 4-Axis Semantics, Units, Coordinates, and Roundtrip."""
from __future__ import annotations

import csv
from pathlib import Path

import pytest

from comsol_mcp._artifact_store import ArtifactStore, csv_to_field_array
from comsol_mcp._solution_binding import FieldArray


def test_csv_four_axis_and_units_mapping(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    # 2 expressions, outer [2, 5], inner [3, 7], point [10, 20]
    values = [
        [  # expr "T"
            [[10.0, 11.0], [12.0, 13.0]],  # outer 2: inner 3, 7
            [[20.0, 21.0], [22.0, 23.0]],  # outer 5: inner 3, 7
        ],
        [  # expr "p"
            [[100.0, 101.0], [102.0, 103.0]],  # outer 2
            [[200.0, 201.0], [202.0, 203.0]],  # outer 5
        ],
    ]
    field = {
        "values": values,
        "axes": ["expression", "outer", "inner", "point"],
        "shape": [2, 2, 2, 2],
        "coords": {
            "expression": ["T", "p"],
            "outer": [2, 5],
            "inner": [3, 7],
            "point": [10, 20],
            "spatial": [[0.1, 0.2, 0.3], [1.1, 1.2, 1.3]],
        },
        "units": {
            "expression": {"T": "degC", "p": "Pa"},
        },
    }
    eval_result = {
        "status": {"ok": True},
        "values": values,
        "expressions": ["T", "p"],
        "field_array": field,
    }

    target = tmp_path / "four_axis.csv"
    res = store.export_field_data(str(target), eval_result, fmt="csv")
    assert res["format"] == "csv"

    with target.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    assert len(rows) == 16  # 2 * 2 * 2 * 2
    first_row = rows[0]
    assert first_row["expr"] == "T"
    assert first_row["outer"] == "2"
    assert first_row["inner"] == "3"
    assert first_row["point"] == "10"
    assert first_row["real"] == "10.0"
    assert first_row["unit"] == "degC"
    assert first_row["coord_0"] == "0.1"
    assert first_row["coord_1"] == "0.2"
    assert first_row["coord_2"] == "0.3"

    # Check second point row
    second_row = rows[1]
    assert second_row["expr"] == "T"
    assert second_row["point"] == "20"
    assert second_row["real"] == "11.0"
    assert second_row["coord_0"] == "1.1"


def test_csv_to_field_array_roundtrip(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    values = [
        [
            [[1.5, 2.5], [3.5, 4.5]],
            [[5.5, 6.5], [7.5, 8.5]],
        ]
    ]
    field = {
        "values": values,
        "axes": ["expression", "outer", "inner", "point"],
        "shape": [1, 2, 2, 2],
        "coords": {
            "expression": ["V"],
            "outer": [10, 20],
            "inner": [100, 200],
            "point": [1, 2],
            "spatial": [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]],
        },
        "units": {
            "expression": {"V": "V"},
        },
    }
    eval_result = {
        "status": {"ok": True},
        "values": values,
        "expressions": ["V"],
        "field_array": field,
    }

    target = tmp_path / "roundtrip.csv"
    store.export_field_data(str(target), eval_result, fmt="csv")

    reconstructed: FieldArray = csv_to_field_array(target)
    assert reconstructed.axes == ["expression", "outer", "inner", "point"]
    assert reconstructed.shape == (1, 2, 2, 2)
    assert reconstructed.coords["expression"] == ["V"]
    assert reconstructed.coords["outer"] == [10, 20]
    assert reconstructed.coords["inner"] == [100, 200]
    assert reconstructed.coords["point"] == [1, 2]
    assert reconstructed.coords["spatial"] == [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]]
    assert reconstructed.units["expression"] == {"V": "V"}
    assert reconstructed.values == values


def test_csv_complex_split(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    values = [[[[{"real": 3.0, "imag": 4.0}]]]]
    field = {
        "values": values,
        "axes": ["expression", "outer", "inner", "point"],
        "shape": [1, 1, 1, 1],
        "coords": {"expression": ["Z"], "outer": [1], "inner": [1], "point": [1]},
        "units": {"expression": {"Z": "Ohm"}},
    }
    target = tmp_path / "complex.csv"
    store.export_field_data(str(target), {"status": {"ok": True}, "values": values, "field_array": field}, fmt="csv")

    with target.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["real"] == "3.0"
    assert rows[0]["imag"] == "4.0"
    assert rows[0]["unit"] == "Ohm"

    reconstructed = csv_to_field_array(target)
    assert reconstructed.is_complex is True
    assert reconstructed.values[0][0][0][0] == complex(3.0, 4.0)
