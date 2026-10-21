from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import windows_current_installed_profile_runtime_binding_r2 as binding
from tests.test_windows_current_installed_profile_runtime_binding_r2 import _matrix


@pytest.mark.parametrize("target", sorted(binding.TARGET_BUILDS))
def test_exact_frozen_matrix_passes_read_only_for_each_target(tmp_path, target):
    path = _matrix(tmp_path / binding.ORIGINAL_MATRIX_NAME)

    result = binding._validate_original_matrix(path, target)

    assert result["sha256"] == binding.ORIGINAL_MATRIX_SHA256
    assert result["required_rows"] == 60
    assert result["target_status"] == "NOT_RUN"


def test_frozen_matrix_rejects_same_bytes_under_a_different_name(tmp_path):
    canonical = _matrix(tmp_path / binding.ORIGINAL_MATRIX_NAME)
    renamed = tmp_path / "copied-matrix.json"
    renamed.write_bytes(canonical.read_bytes())

    with pytest.raises(RuntimeError, match="filename"):
        binding._validate_original_matrix(renamed, "windows-x64/comsol6.3")


def test_frozen_matrix_rejects_symlink_alias_even_when_target_bytes_match(tmp_path):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    canonical = _matrix(source_dir / binding.ORIGINAL_MATRIX_NAME)
    alias_dir = tmp_path / "alias"
    alias_dir.mkdir()
    alias = alias_dir / binding.ORIGINAL_MATRIX_NAME
    alias.symlink_to(canonical)

    with pytest.raises(RuntimeError, match="reparse-point alias"):
        binding._validate_original_matrix(alias, "windows-x64/comsol6.3")


def test_frozen_matrix_rejects_changed_content_before_json_semantics(tmp_path):
    path = _matrix(tmp_path / binding.ORIGINAL_MATRIX_NAME)
    value = json.loads(path.read_text(encoding="utf-8"))
    value["tests"][0]["targets"]["windows-x64/comsol6.4"]["status"] = "PASS"
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")

    with pytest.raises(RuntimeError, match="frozen 60-case source checkpoint"):
        binding._validate_original_matrix(path, "windows-x64/comsol6.4")
