"""Tests for R04: Result Evaluate Artifact Budget and Chunk Hash Caching."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from comsol_mcp._artifact_store import ArtifactStore, _read_pinned_chunk, _WHOLE_FILE_HASH_CACHE
from comsol_mcp import _g3_results as results
from comsol_mcp._solution_binding import FieldArray


class _FakeWorker:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self.paths = self
        self.resolved_project_root = project_root


def test_artifact_read_reuses_cached_whole_hash(tmp_path: Path) -> None:
    test_file = tmp_path / "large_chunk_test.txt"
    test_file.write_bytes(b"A" * 10000)

    _WHOLE_FILE_HASH_CACHE.clear()
    chunk1 = _read_pinned_chunk(test_file, offset=0, length=100)
    assert len(_WHOLE_FILE_HASH_CACHE) == 1

    # Second read at different offset should hit cache for whole_file_sha256
    chunk2 = _read_pinned_chunk(test_file, offset=100, length=100)
    assert chunk2["whole_file_sha256"] == chunk1["whole_file_sha256"]
    assert chunk2["chunk_sha256"] == chunk1["chunk_sha256"]  # All 'A's
    assert chunk2["offset"] == 100


def test_field_array_summary_bounds_preview_and_omits_data_values() -> None:
    values = [[[[float(i) for i in range(100)]]]]
    fa = FieldArray(
        values,
        axes=("expression", "outer", "inner", "point"),
        coords={"expression": ["T"], "outer": [1], "inner": [1], "point": list(range(100))},
        units={"expression": {"T": "K"}},
        metadata={"model": "test"},
    )
    summary = results._field_array_summary(fa, max_preview_points=4)
    assert summary["storage"] == "artifact"
    assert "values" not in summary
    assert "data" not in summary
    assert summary["axes"] == ["expression", "outer", "inner", "point"]
    assert summary["shape"] == [1, 1, 1, 100]
    assert len(summary["coords"]["point"]) == 4  # Bounded to 4
    assert "... (96 more)" in summary["preview"][0][0][0]
