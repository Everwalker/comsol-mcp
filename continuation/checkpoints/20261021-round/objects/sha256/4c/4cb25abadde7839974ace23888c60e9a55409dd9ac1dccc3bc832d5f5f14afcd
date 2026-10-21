from __future__ import annotations

import csv
import hashlib
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace

import pytest

import comsol_mcp._artifact_store as artifact_store_module
from comsol_mcp import _g3_results as results
from comsol_mcp._artifact_store import ArtifactStore, artifact_read
from comsol_mcp._execution_contract import ExecutionContractError


class _Worker:
    def __init__(self, project_root: Path) -> None:
        self.paths = SimpleNamespace(project_root=project_root)


def _ok(values):
    return {"status": {"ok": True}, "values": values, "expressions": ["T"],
            "dataset": "dset", "solution": "sol", "total_elements": 1}


def test_artifact_store_requires_a_trusted_project_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ExecutionContractError) as excinfo:
        ArtifactStore()
    assert excinfo.value.code == "RUNTIME_CONFIGURATION_REQUIRED"


def test_nested_symlink_is_refused_even_when_target_stays_inside(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "link").symlink_to(real, target_is_directory=True)
    with pytest.raises(ExecutionContractError) as excinfo:
        ArtifactStore(tmp_path).resolve_safe_path("link/out.json")
    assert excinfo.value.code == "ACCESS_VIOLATION"


def test_export_does_not_overwrite_without_explicit_authorization(tmp_path: Path) -> None:
    target = tmp_path / "out.json"
    target.write_text("original\n", encoding="utf-8")
    store = ArtifactStore(tmp_path)
    with pytest.raises(ExecutionContractError) as excinfo:
        store.export_field_data(str(target), _ok([1.0]))
    assert excinfo.value.code == "DESTINATION_EXISTS"
    assert target.read_text(encoding="utf-8") == "original\n"
    result = store.export_field_data(str(target), _ok([2.0]), allow_overwrite=True)
    assert result["sha256"] == hashlib.sha256(target.read_bytes()).hexdigest()


def test_failed_overwrite_preserves_original_hash(tmp_path: Path) -> None:
    target = tmp_path / "stable.json"
    target.write_text("keep this exact content\n", encoding="utf-8")
    before = hashlib.sha256(target.read_bytes()).hexdigest()
    with pytest.raises(ExecutionContractError) as excinfo:
        ArtifactStore(tmp_path).export_field_data(
            str(target), _ok([float("nan")]), allow_overwrite=True
        )
    assert excinfo.value.code == "SERIALIZATION_ERROR"
    assert hashlib.sha256(target.read_bytes()).hexdigest() == before
    assert target.read_text(encoding="utf-8") == "keep this exact content\n"


def test_field_export_checks_destination_before_evaluation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    called = False

    def fail_if_called(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("evaluation must not run for a refused destination")

    monkeypatch.setattr(results, "result_evaluate", fail_if_called)
    worker = _Worker(tmp_path)
    outside = tmp_path.parent / "outside-result.json"
    with pytest.raises(ExecutionContractError) as excinfo:
        results.result_field_export(worker, "model", {
            "spec": {"expressions": ["T"]}, "format": "json", "destination": str(outside),
        })
    assert excinfo.value.code == "ACCESS_VIOLATION"
    assert not called
    assert not outside.exists()


def test_field_export_checks_format_before_evaluation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    called = False

    def fail_if_called(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("evaluation must not run for an unsupported format")

    monkeypatch.setattr(results, "result_evaluate", fail_if_called)
    with pytest.raises(ExecutionContractError) as excinfo:
        results.result_field_export(_Worker(tmp_path), "model", {
            "spec": {"expressions": ["T"]}, "format": "yaml", "destination": "result.yaml",
        })
    assert excinfo.value.code == "API_UNSUPPORTED"
    assert not called


def test_field_export_uses_worker_project_root_and_rejects_unknown_outcome(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    worker = _Worker(tmp_path)
    monkeypatch.setattr(results, "result_evaluate", lambda *_args, **_kwargs: {
        "status": {"ok": False, "execution_state_unknown": True}, "values": [1.0],
    })
    target = tmp_path / "result.json"
    with pytest.raises(ExecutionContractError) as excinfo:
        results.result_field_export(worker, "model", {
            "spec": {"expressions": ["T"]}, "format": "json", "destination": str(target),
        })
    assert excinfo.value.code == "EXPORT_FAILED"
    assert not target.exists()


def test_field_export_overwrite_is_explicit_and_schema_visible(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    worker = _Worker(tmp_path)
    target = tmp_path / "result.json"
    target.write_text("before\n", encoding="utf-8")
    called = 0

    def evaluate(*_args, **_kwargs):
        nonlocal called
        called += 1
        return _ok([3.0])

    monkeypatch.setattr(results, "result_evaluate", evaluate)
    request = {"spec": {"expressions": ["T"]}, "format": "json", "destination": str(target)}
    with pytest.raises(ExecutionContractError) as excinfo:
        results.result_field_export(worker, "model", request)
    assert excinfo.value.code == "DESTINATION_EXISTS"
    assert called == 0
    out = results.result_field_export(worker, "model", {**request, "overwrite": True})
    assert called == 1
    assert out["sha256"] == hashlib.sha256(target.read_bytes()).hexdigest()


def test_empty_and_nonfinite_values_are_refused_before_publish(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    for values in ([], {}, [float("nan")], [{"real": 1.0, "imag": float("inf")}]):
        target = tmp_path / f"{len(list(tmp_path.iterdir()))}.json"
        with pytest.raises(ExecutionContractError) as excinfo:
            store.export_field_data(str(target), _ok(values))
        assert excinfo.value.code in {"EXPORT_FAILED", "SERIALIZATION_ERROR"}
        assert not target.exists()


def test_csv_has_explicit_axes_and_complex_columns(tmp_path: Path) -> None:
    target = tmp_path / "values.csv"
    result = ArtifactStore(tmp_path).export_field_data(
        str(target),
        {"status": {"ok": True}, "values": [[{"real": 3.0, "imag": 4.0}]],
         "expressions": ["E"], "expression_units": ["V/m"], "dataset": "d", "solution": "s"},
        fmt="csv",
    )
    assert result["format"] == "csv"
    header = target.read_text(encoding="utf-8").splitlines()[0].split(",")
    for name in ("axis", "expr", "outer", "inner", "point", "real", "imag", "unit", "coord_0"):
        assert name in header
    assert "3.0" in target.read_text(encoding="utf-8")
    assert "4.0" in target.read_text(encoding="utf-8")


def test_csv_flattens_complex_four_axis_field_array_with_units_and_coords(tmp_path: Path) -> None:
    target = tmp_path / "field.csv"
    result = ArtifactStore(tmp_path).export_field_data(
        str(target),
        {
            "status": {"ok": True},
            "values": [
                {
                    "axis": "solution",
                    "expr": "Ex",
                    "outer": 4,
                    "inner": 2,
                    "point": 7,
                    "real": 1.25,
                    "imag": -0.5,
                    "unit": "V/m",
                    "coords": [0.1, 0.2, 0.3],
                }
            ],
            "field_array": {
                "axes": ["outer", "inner", "point", "component"],
                "units": ["V/m"],
                "coordinates": [[0.1, 0.2, 0.3]],
            },
        },
        fmt="csv",
    )
    assert result["format"] == "csv"
    with target.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows == [{
        "axis": "solution",
        "expr": "Ex",
        "outer": "4",
        "inner": "2",
        "point": "7",
        "real": "1.25",
        "imag": "-0.5",
        "unit": "V/m",
        "coord_0": "0.1",
        "coord_1": "0.2",
        "coord_2": "0.3",
    }]


def test_json_retains_complete_field_array_metadata(tmp_path: Path) -> None:
    target = tmp_path / "field.json"
    field_array = {
        "axes": ["outer", "inner", "point", "component"],
        "units": ["V/m"],
        "coordinates": [[0.1, 0.2, 0.3]],
        "shape": [1, 1, 1, 1],
    }
    ArtifactStore(tmp_path).export_field_data(
        str(target), {**_ok([{"real": 1.0, "imag": 2.0}]), "field_array": field_array}
    )
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["metadata"]["field_array"] == field_array
    assert payload["metadata"]["status"] == {"ok": True}


def test_artifact_read_binds_worker_root_and_returns_whole_hash(tmp_path: Path) -> None:
    target = tmp_path / "payload.json"
    target.write_text(json.dumps({"x": list(range(20))}), encoding="utf-8")
    ArtifactStore.register_artifact(target)
    worker = _Worker(tmp_path)
    out = artifact_read(worker, None, {"path": str(target), "offset": 0, "length": 8})
    assert out["whole_file_sha256"] == hashlib.sha256(target.read_bytes()).hexdigest()
    with pytest.raises(ExecutionContractError) as excinfo:
        artifact_read(worker, None, {"path": str(target), "offset": 0, "length": 16 * 1024 * 1024 + 1})
    assert excinfo.value.code == "INVALID_REQUEST"


def test_artifact_read_rejects_same_size_path_replacement_during_pinned_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "payload.bin"
    replacement = tmp_path / "replacement.bin"
    old_bytes = b"old-content-0000"
    new_bytes = b"new-content-0000"
    assert len(old_bytes) == len(new_bytes)
    target.write_bytes(old_bytes)
    ArtifactStore.register_artifact(target)
    old_hash = hashlib.sha256(old_bytes).hexdigest()
    original_fstat = artifact_store_module.os.fstat
    calls = 0

    def replace_after_fd_pin(fd: int):
        nonlocal calls
        result = original_fstat(fd)
        if calls == 0:
            replacement.write_bytes(new_bytes)
            try:
                replacement.replace(target)
            except PermissionError:
                # On Windows NTFS, an open file descriptor locks the target against replacement
                pytest.skip("Windows NTFS file locking prevents replacing an open file")
        calls += 1
        return result

    monkeypatch.setattr(artifact_store_module.os, "fstat", replace_after_fd_pin)
    with pytest.raises(ExecutionContractError) as excinfo:
        artifact_read(
            _Worker(tmp_path),
            None,
            {"path": str(target), "offset": 0, "length": len(new_bytes), "expected_sha256": old_hash},
        )
    assert excinfo.value.code == "ARTIFACT_CHANGED"
    assert target.read_bytes() == new_bytes
    assert calls >= 2


def test_artifact_read_rejects_same_size_in_place_mutation_during_pinned_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "payload.bin"
    old_bytes = b"old-content-0000"
    new_bytes = b"new-content-0000"
    assert len(old_bytes) == len(new_bytes)
    target.write_bytes(old_bytes)
    ArtifactStore.register_artifact(target)
    original_fstat = artifact_store_module.os.fstat
    calls = 0

    def mutate_after_fd_pin(fd: int):
        nonlocal calls
        result = original_fstat(fd)
        if calls == 0:
            time.sleep(0.02)
            target.write_bytes(new_bytes)
        calls += 1
        return result

    monkeypatch.setattr(artifact_store_module.os, "fstat", mutate_after_fd_pin)
    with pytest.raises(ExecutionContractError) as excinfo:
        artifact_read(_Worker(tmp_path), None, {"path": str(target), "offset": 0, "length": len(new_bytes)})
    assert excinfo.value.code == "ARTIFACT_CHANGED"
    assert target.read_bytes() == new_bytes
    assert calls >= 2


def test_export_no_clobber_publish_preserves_target_created_after_preflight(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "race.json"
    original_link = artifact_store_module.os.link
    calls = 0

    def create_target_then_fail(src: str | bytes | os.PathLike[str], dst: str | bytes | os.PathLike[str], *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            target.write_text("racer-owned\n", encoding="utf-8")
            raise FileExistsError("simulated destination race")
        return original_link(src, dst, *args, **kwargs)

    monkeypatch.setattr(artifact_store_module.os, "link", create_target_then_fail)
    with pytest.raises(ExecutionContractError) as excinfo:
        ArtifactStore(tmp_path).export_field_data(str(target), _ok([3.0]))
    assert excinfo.value.code == "DESTINATION_EXISTS"
    assert target.read_text(encoding="utf-8") == "racer-owned\n"
    assert calls == 1
    assert not list(tmp_path.glob(f".{target.name}.tmp.*.partial"))
