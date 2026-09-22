"""Artifact export containment/atomicity and the host-requestable chunk read.

These cover the two defects the acceptance recheck recorded for the export side of
the artifact service:

* ``_export_to_artifact`` wrote straight into ``Path.cwd()/g2_artifacts/results`` with
  ``write_text`` -- no containment gate and no atomic publish -- even though the
  artifact store right next to it already refused escapes and published atomically.
* The bounded chunk reader existed as a library call but no operation was published,
  so a host could not request a chunk at all while ACCEPTANCE C13 requires a real
  chunk contract (offset/length reconstruction, pinned hash, bounded memory).

The memory test deliberately measures a consumer that folds chunks into a rolling
digest: a consumer that accumulates the payload would measure itself, not the reader.
"""
from __future__ import annotations

import base64
import hashlib
import tracemalloc
from pathlib import Path

import pytest

from comsol_mcp._artifact_store import ArtifactStore, artifact_read
from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._g3_ops import DISPATCH
from comsol_mcp._g3_results import _export_to_artifact


@pytest.fixture()
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Run inside a throwaway project root so artifacts never land in the repository."""
    root = tmp_path.resolve()
    monkeypatch.chdir(root)
    return root


def _stream_digest(path: Path, size: int, chunk_bytes: int, pin: str | None) -> str:
    rolling = hashlib.sha256()
    offset = 0
    while offset < size:
        args: dict[str, object] = {"path": str(path), "offset": offset, "length": chunk_bytes}
        if pin is not None:
            args["expected_sha256"] = pin
        out = artifact_read(None, None, args)
        block = base64.b64decode(out["data_base64"])
        assert out["chunk_sha256"] == hashlib.sha256(block).hexdigest()
        rolling.update(block)
        offset += out["length"]
        if out["eof"]:
            break
    return rolling.hexdigest()


def _stream_peak(path: Path, size: int, chunk_bytes: int, pin: str | None) -> float:
    rolling = hashlib.sha256()
    offset = 0
    tracemalloc.start()
    while offset < size:
        args: dict[str, object] = {"path": str(path), "offset": offset, "length": chunk_bytes}
        if pin is not None:
            args["expected_sha256"] = pin
        out = artifact_read(None, None, args)
        rolling.update(base64.b64decode(out["data_base64"]))
        offset += out["length"]
        if out["eof"]:
            break
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return peak / 1024


def _full_read_peak(path: Path) -> float:
    tracemalloc.start()
    blob = path.read_bytes()
    hashlib.sha256(blob).hexdigest()
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    del blob
    return peak / 1024


def test_published_artifact_is_contained_and_atomic(project: Path) -> None:
    meta = _export_to_artifact({"x": [1.0, 2.0]}, "unit")
    ref = Path(meta["artifact_ref"]).resolve()
    assert ref.is_relative_to(project), ref
    assert hashlib.sha256(ref.read_bytes()).hexdigest() == meta["sha256"]
    assert [p.name for p in ref.parent.iterdir() if p.name != ref.name] == []


def test_export_refuses_escaping_destination_before_side_effects(project: Path) -> None:
    # Note: the delivered store allows the process temp tree as a second root, so a
    # "sibling of tmp_path" destination is *inside* its policy.  A refusal case has to
    # aim outside the project root and outside the temp allowance.
    outside = Path("/etc/g3_d17_export_probe.json")
    assert not outside.exists(), "probe destination must not pre-exist"
    store = ArtifactStore()
    with pytest.raises(ExecutionContractError) as excinfo:
        store.export_field_data(str(outside), {"values": [1, 2, 3]})
    assert excinfo.value.code == "ACCESS_VIOLATION"
    assert not outside.exists(), "a refused export must not create the destination"


def test_export_allows_the_temp_root_the_read_operation_refuses(project: Path) -> None:
    """Records the delivered destination policy: temp is an allowed export root."""
    import tempfile

    target = Path(tempfile.gettempdir()).resolve() / "d17_export_policy_probe.json"
    target.unlink(missing_ok=True)
    try:
        result = ArtifactStore().export_field_data(str(target), {"values": [1, 2, 3]})
        assert Path(result["file_path"]).is_file()
        assert result["sha256"] == hashlib.sha256(target.read_bytes()).hexdigest()
    finally:
        target.unlink(missing_ok=True)


def test_host_chunk_read_reconstructs_pinned_digest(project: Path) -> None:
    assert "artifact.read" in DISPATCH
    payload = {"x": [i * 0.5 for i in range(20000)], "y": list(range(20000))}
    meta = _export_to_artifact(payload, "unit", eval_context={"requested_storage": "auto"})
    ref = Path(meta["artifact_ref"]).resolve()
    assert _stream_digest(ref, meta["byte_size"], 32 * 1024, meta["sha256"]) == meta["sha256"]
    # a pinned digest must still be checked on later chunks that omit it
    assert _stream_digest(ref, meta["byte_size"], 32 * 1024, None) == meta["sha256"]


def test_host_chunk_read_refusals(project: Path) -> None:
    meta = _export_to_artifact({"x": list(range(2000))}, "unit")
    ref = Path(meta["artifact_ref"]).resolve()
    size = meta["byte_size"]
    cases = {
        "ACCESS_VIOLATION": {"path": "../../etc/passwd", "offset": 0, "length": 16},
        "INVALID_CHUNK_RANGE": {"path": str(ref), "offset": size + 1, "length": 16},
        "ARTIFACT_HASH_MISMATCH": {
            "path": str(ref), "offset": 0, "length": 16, "expected_sha256": "0" * 64,
        },
        "CHUNK_HASH_MISMATCH": {
            "path": str(ref), "offset": 0, "length": 16, "expected_chunk_sha256": "0" * 64,
        },
        "INVALID_REQUEST": {"path": str(ref), "offset": -1, "length": 16},
        "INVALID_REQUEST_BOOL": {"path": str(ref), "offset": True, "length": 16},
    }
    for label, arguments in cases.items():
        expected = "INVALID_REQUEST" if label.startswith("INVALID_REQUEST") else label
        with pytest.raises(ExecutionContractError) as excinfo:
            artifact_read(None, None, arguments)
        assert excinfo.value.code == expected, (label, excinfo.value.code)


def test_request_cannot_widen_the_project_root(project: Path, tmp_path: Path) -> None:
    outside = tmp_path.parent / "not_in_project.txt"
    outside.write_text("outside\n", encoding="utf-8")
    try:
        with pytest.raises(ExecutionContractError) as excinfo:
            # The delivered signature had no root at all; the request body must not be
            # able to introduce one either.
            artifact_read(
                None, None,
                {"path": str(outside), "project_root": "/", "offset": 0, "length": 4},
            )
        assert excinfo.value.code == "ACCESS_VIOLATION"
        # and the store's own root stays the process's project root
        assert ArtifactStore().project_root == project
    finally:
        outside.unlink(missing_ok=True)


def test_read_scope_is_the_project_root_not_the_temp_allowance(project: Path) -> None:
    """The store admits the temp directory; the read operation does not."""
    import tempfile

    temp_file = Path(tempfile.gettempdir()).resolve() / "d17_read_scope_probe.txt"
    temp_file.write_text("temp\n", encoding="utf-8")
    try:
        # the store resolves it (delivered policy) ...
        assert ArtifactStore().resolve_safe_path(str(temp_file)).is_file()
        # ... but the operation refuses it, because artifacts are project-scoped
        with pytest.raises(ExecutionContractError) as excinfo:
            artifact_read(None, None, {"path": str(temp_file), "offset": 0, "length": 4})
        assert excinfo.value.code == "ACCESS_VIOLATION"
    finally:
        temp_file.unlink(missing_ok=True)


def test_streaming_peak_is_size_independent_while_full_read_tracks_size(project: Path) -> None:
    small = _export_to_artifact({"x": [i * 0.5 for i in range(20000)]}, "small")
    big = _export_to_artifact({"x": [i * 0.5 for i in range(200000)]}, "big")
    small_ref, big_ref = Path(small["artifact_ref"]).resolve(), Path(big["artifact_ref"]).resolve()

    small_stream = _stream_peak(small_ref, small["byte_size"], 64 * 1024, None)
    big_stream = _stream_peak(big_ref, big["byte_size"], 64 * 1024, None)
    small_full = _full_read_peak(small_ref)
    big_full = _full_read_peak(big_ref)

    assert big["byte_size"] / small["byte_size"] > 5, (small["byte_size"], big["byte_size"])
    # the reader may not buffer the file: its peak must not grow with the file
    assert big_stream / small_stream < 1.6, (small_stream, big_stream)
    # the forbidden shape does grow with the file, which is what makes the above meaningful
    assert big_full / small_full > 3.0, (small_full, big_full)
