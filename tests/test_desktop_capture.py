"""Software tests for the isolated HWND capture helper; no native UI calls."""
from __future__ import annotations

import hashlib
import multiprocessing
from pathlib import Path
import time

import pytest

from comsol_mcp import _desktop_capture as capture
from comsol_mcp._desktop_service import WindowIdentity


def _identity() -> WindowIdentity:
    return WindowIdentity(
        "windows", "HWND:1a2b", 4321, "filetime:123456", "S-1-5-21-test", "WTS:4", "6.4.0.293",
    )


def _fixture_worker(identity, output_path, max_dimension, max_pixels, max_bytes, sender):
    payload = capture._encode_bmp(bytes([0x30, 0x20, 0x10, 0xFF]) * 4, 2, 2)
    with open(output_path, "xb") as stream:
        stream.write(payload)
    fingerprint = identity.fingerprint()
    sender.send({
        "ok": True,
        "output_path": output_path,
        "width": 2,
        "height": 2,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "target_identity_fingerprint": fingerprint,
        "pre_identity_fingerprint": fingerprint,
        "post_identity_fingerprint": fingerprint,
    })


def _bad_reported_path_worker(identity, output_path, max_dimension, max_pixels, max_bytes, sender):
    _fixture_worker(identity, output_path, max_dimension, max_pixels, max_bytes, _RecordingSender(sender, "../outside.bmp"))


class _RecordingSender:
    def __init__(self, sender, path):
        self.sender = sender
        self.path = path

    def send(self, value):
        value = dict(value)
        value["output_path"] = self.path
        self.sender.send(value)


def _timeout_worker(identity, output_path, max_dimension, max_pixels, max_bytes, sender):
    time.sleep(10)


def test_identity_validation_rejects_non_windows_or_incomplete_handle_fields():
    assert capture._validate_identity(_identity()) == 0x1A2B
    with pytest.raises(capture.WindowCaptureError, match="canonical HWND"):
        capture._validate_identity(WindowIdentity("windows", "title:COMSOL", 4321, "birth", "sid", "session", "6.4"))
    with pytest.raises(capture.WindowCaptureError, match="process birth"):
        capture._validate_identity(WindowIdentity("windows", "HWND:1a2b", 4321, "UNVERIFIED", "sid", "session", "6.4"))
    with pytest.raises(capture.WindowCaptureError, match="Windows WindowIdentity"):
        capture._validate_identity(WindowIdentity("macos", "CGWindow:42", 4321, "birth", "uid:42", "session", "6.4"))


def test_bounded_helper_returns_only_hash_checked_target_window_bmp(tmp_path):
    identity = _identity()
    with __import__("tempfile").TemporaryDirectory(dir=tmp_path) as temp_name:
        scratch = Path(temp_name)
        output = scratch / "window.bmp"
        response = capture._run_isolated_capture_worker(
            identity, output, 5, worker=_fixture_worker,
        )
        data = capture._read_and_verify_capture(identity, output, scratch.resolve(), response)
    assert data[:2] == b"BM"
    assert capture._bmp_dimensions(data) == (2, 2)
    assert hashlib.sha256(data).hexdigest() == response["sha256"]
    assert response["target_identity_fingerprint"] == identity.fingerprint()


def test_helper_rejects_output_path_escape_and_changed_identity(tmp_path):
    identity = _identity()
    with __import__("tempfile").TemporaryDirectory(dir=tmp_path) as temp_name:
        scratch = Path(temp_name)
        output = scratch / "window.bmp"
        response = capture._run_isolated_capture_worker(identity, output, 5, worker=_bad_reported_path_worker)
        with pytest.raises(capture.WindowCaptureError, match="outside the exact private target path"):
            capture._read_and_verify_capture(identity, output, scratch.resolve(), response)

    with __import__("tempfile").TemporaryDirectory(dir=tmp_path) as temp_name:
        scratch = Path(temp_name)
        output = scratch / "window.bmp"
        response = capture._run_isolated_capture_worker(identity, output, 5, worker=_fixture_worker)
        response["post_identity_fingerprint"] = "f" * 64
        with pytest.raises(capture.WindowCaptureError, match="identity changed"):
            capture._read_and_verify_capture(identity, output, scratch.resolve(), response)


def test_helper_stops_only_its_own_process_when_deadline_expires(tmp_path):
    identity = _identity()
    with __import__("tempfile").TemporaryDirectory(dir=tmp_path) as temp_name:
        output = Path(temp_name) / "window.bmp"
        started = time.monotonic()
        with pytest.raises(capture.WindowCaptureError, match="COMSOL was not terminated") as exc:
            capture._run_isolated_capture_worker(identity, output, 0.25, worker=_timeout_worker)
        elapsed = time.monotonic() - started
    assert exc.value.code == "CAPTURE_HELPER_TIMEOUT"
    assert elapsed < 3


def test_parent_rejects_tampered_bmp_receipt(tmp_path):
    identity = _identity()
    with __import__("tempfile").TemporaryDirectory(dir=tmp_path) as temp_name:
        scratch = Path(temp_name)
        output = scratch / "window.bmp"
        response = capture._run_isolated_capture_worker(identity, output, 5, worker=_fixture_worker)
        response["sha256"] = "0" * 64
        with pytest.raises(capture.WindowCaptureError, match="hash does not match"):
            capture._read_and_verify_capture(identity, output, scratch.resolve(), response)


def test_bmp_dimension_parser_rejects_oversized_or_malformed_images():
    oversized = capture._encode_bmp(b"\0" * 4, 1, 1)
    damaged = bytearray(oversized)
    damaged[18:22] = (9000).to_bytes(4, "little", signed=True)
    with pytest.raises(capture.WindowCaptureError, match="dimensions exceed"):
        capture._bmp_dimensions(bytes(damaged))
    with pytest.raises(capture.WindowCaptureError, match="not a Windows BMP"):
        capture._bmp_dimensions(b"not-a-bitmap")


def test_public_capture_never_runs_off_windows(monkeypatch):
    monkeypatch.setattr(capture.sys, "platform", "darwin")
    with pytest.raises(capture.WindowCaptureError) as exc:
        capture.capture_windows_window(_identity())
    assert exc.value.code == "UNSUPPORTED_PLATFORM"


def test_scratch_root_must_be_an_existing_real_directory(tmp_path):
    link = tmp_path / "linked"
    link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(capture.WindowCaptureError, match="symbolic link"):
        capture._validated_scratch_root(link)
    with pytest.raises(capture.WindowCaptureError, match="existing, readable directory"):
        capture._validated_scratch_root(tmp_path / "missing")
