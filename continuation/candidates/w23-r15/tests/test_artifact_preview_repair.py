from __future__ import annotations

import json
import os
import struct
import zlib

import pytest

from tests.test_artifact_preview import _error_code, _new_daemon, _png_chunk, _read, _register


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
SECRET_PATH = "/private/tmp/REVIEW_SECRET_ROOT/fixture.csv"


@pytest.mark.parametrize("name,source", [
    (
        "indexed-trns-before-plte.png",
        PNG_SIGNATURE
        + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 3, 0, 0, 0))
        + _png_chunk(b"tRNS", b"\x00")
        + _png_chunk(b"PLTE", b"\x10\x20\x30")
        + _png_chunk(b"IDAT", zlib.compress(b"\x00\x00"))
        + _png_chunk(b"IEND", b""),
    ),
    (
        "rgb-plte-after-trns.png",
        PNG_SIGNATURE
        + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + _png_chunk(b"tRNS", struct.pack(">HHH", 10, 20, 30))
        + _png_chunk(b"PLTE", b"\x10\x20\x30")
        + _png_chunk(b"IDAT", zlib.compress(b"\x00\x0a\x14\x1e"))
        + _png_chunk(b"IEND", b""),
    ),
])
def test_png_tRNS_must_follow_any_PLTE_on_real_control_daemon_route(tmp_path, name, source):
    daemon, project_id, root = _new_daemon(tmp_path)
    (root / "inputs" / name).write_bytes(source)
    try:
        artifact_id = _register(daemon, project_id, "inputs/" + name, key="register-" + name)
        daemon.backend.service = None
        response = _read(daemon, "artifact.preview", project_id, artifact_id)
        assert response["success"] is False
        assert _error_code(response) == "ARTIFACT_FORMAT_INVALID"
        assert "preview_png_base64" not in response.get("data", {})
        assert str(root) not in json.dumps(response)
    finally:
        daemon.close()


@pytest.mark.parametrize("fault", [
    "initial_hash_seek", "initial_hash_read", "preview_seek", "preview_read",
    "final_hash_seek", "final_hash_read", "preparse_fstat", "postparse_fstat",
    "open_fstat", "close",
])
def test_pinned_descriptor_oserrors_are_typed_and_never_logged_as_unknown(tmp_path, monkeypatch, fault):
    import comsol_mcp._artifact_preview_io as preview_io
    import comsol_mcp._artifact_store as artifact_store

    daemon, project_id, root = _new_daemon(tmp_path)
    (root / "inputs" / "io.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    artifact_id = _register(daemon, project_id, "inputs/io.csv", key="register-io")
    real_open = preview_io._open_pinned_artifact
    real_os = preview_io.os

    class FaultingHandle:
        def __init__(self, handle):
            self.handle = handle
            self.seek_calls = 0
            self.read_calls = 0

        def seek(self, *args, **kwargs):
            self.seek_calls += 1
            fail_at = {"initial_hash_seek": 1, "preview_seek": 2, "final_hash_seek": 3}.get(fault)
            if self.seek_calls == fail_at:
                raise OSError("injected seek denial at " + SECRET_PATH)
            return self.handle.seek(*args, **kwargs)

        def read(self, *args, **kwargs):
            self.read_calls += 1
            fail_at = {"initial_hash_read": 1, "preview_read": 3, "final_hash_read": 4}.get(fault)
            if self.read_calls == fail_at:
                raise OSError("injected read denial at " + SECRET_PATH)
            return self.handle.read(*args, **kwargs)

        def fileno(self):
            return self.handle.fileno()

        def close(self):
            if fault == "close":
                self.handle.close()
                raise OSError("injected close denial at " + SECRET_PATH)
            return self.handle.close()

        def __getattr__(self, name):
            return getattr(self.handle, name)

    class FaultingOs:
        def __init__(self):
            self.calls = 0

        def __getattr__(self, name):
            return getattr(real_os, name)

        def fstat(self, fd):
            self.calls += 1
            expected_call = 1 if fault == "preparse_fstat" else 2
            if fault in {"preparse_fstat", "postparse_fstat"} and self.calls == expected_call:
                raise OSError("injected fstat denial at " + SECRET_PATH)
            return real_os.fstat(fd)

    class FaultingStoreOs:
        def __getattr__(self, name):
            return getattr(real_os, name)

        def fstat(self, fd):
            if fault == "open_fstat":
                raise OSError("injected opening fstat denial at " + SECRET_PATH)
            return real_os.fstat(fd)

    def open_with_fault(path):
        handle, observed = real_open(path)
        return FaultingHandle(handle), observed

    log_path = daemon.home / "control-errors.log"
    previous_log = log_path.read_bytes() if log_path.exists() else None
    monkeypatch.setattr(preview_io, "_open_pinned_artifact", open_with_fault)
    monkeypatch.setattr(preview_io, "os", FaultingOs())
    if fault == "open_fstat":
        monkeypatch.setattr(artifact_store, "os", FaultingStoreOs())
    try:
        response = _read(daemon, "artifact.preview", project_id, artifact_id)
        assert response["success"] is False
        assert _error_code(response) == "ARTIFACT_READ_FAILED"
        serialized = json.dumps(response)
        assert SECRET_PATH not in serialized
        assert str(root) not in serialized
        assert (log_path.read_bytes() if log_path.exists() else None) == previous_log
    finally:
        daemon.close()


def test_close_oserror_does_not_replace_existing_format_refusal(tmp_path, monkeypatch):
    import comsol_mcp._artifact_preview_io as preview_io

    daemon, project_id, root = _new_daemon(tmp_path)
    (root / "inputs" / "invalid.csv").write_text('a,b\n1,"unfinished\n', encoding="utf-8")
    artifact_id = _register(daemon, project_id, "inputs/invalid.csv", key="register-invalid-close")
    real_open = preview_io._open_pinned_artifact

    class CloseFaultingHandle:
        def __init__(self, handle):
            self.handle = handle

        def close(self):
            self.handle.close()
            raise OSError("injected close denial at " + SECRET_PATH)

        def __getattr__(self, name):
            return getattr(self.handle, name)

    def open_with_close_fault(path):
        handle, observed = real_open(path)
        return CloseFaultingHandle(handle), observed

    monkeypatch.setattr(preview_io, "_open_pinned_artifact", open_with_close_fault)
    try:
        response = _read(daemon, "artifact.preview", project_id, artifact_id)
        assert response["success"] is False
        assert _error_code(response) == "ARTIFACT_FORMAT_INVALID"
        assert SECRET_PATH not in json.dumps(response)
    finally:
        daemon.close()
