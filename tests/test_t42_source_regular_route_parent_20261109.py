"""Synthetic lock-bound routes without rewriting pre-existing fixture files."""
import os
import tarfile
from pathlib import Path

import pytest

from tests._t42_lock_notices_support import Fixture, TARGET
from tools import source_lock_notices as notices


@pytest.fixture
def own_case_directory(tmp_path_factory, request):
    return tmp_path_factory.mktemp(request.node.name, numbered=False)


def _single_component(directory, license_bytes):
    fixture = Fixture(directory)
    fixture.add_package("synth-alpha", "7.1.0", "License.txt", license_bytes)
    lock = fixture.save_lock()
    receipt = fixture.save_receipt()
    return fixture, lock, receipt


def test_route_accepts_notice_at_limit(own_case_directory):
    payload = b"N" * notices.MAX_NOTICE_BYTES
    fixture, lock, receipt = _single_component(own_case_directory, payload)
    results, route = notices.lock_bound_notice_payloads(receipt, source_lock=lock, target=TARGET)
    assert route["applied"] == ["synth-alpha"]
    assert results["synth-alpha"][0]["license_bytes"] == payload
    assert fixture.licenses["synth-alpha"] == payload


def test_route_rejects_oversize_before_extract(own_case_directory, monkeypatch):
    payload = b"N" * (notices.MAX_NOTICE_BYTES + 1)
    _fixture, lock, receipt = _single_component(own_case_directory, payload)
    calls = []

    def unexpected_extract(self, *args, **kwargs):
        calls.append(True)
        raise AssertionError("oversize notice must be refused before extraction")

    monkeypatch.setattr(tarfile.TarFile, "extractfile", unexpected_extract)
    with pytest.raises(notices.SourceLockNoticeError, match="primary source notice exceeds its byte maximum"):
        notices.lock_bound_notice_payloads(receipt, source_lock=lock, target=TARGET)
    assert calls == []


class _GrowOnFirstRead:
    def __init__(self, handle, path, original_open, events):
        self.handle = handle
        self.path = path
        self.original_open = original_open
        self.events = events
        self.grown = False

    def __enter__(self):
        self.handle.__enter__()
        return self

    def __exit__(self, *args):
        return self.handle.__exit__(*args)

    def read(self, size=-1):
        if not self.grown:
            with self.original_open(self.path, "ab") as writer:
                assert writer.write(b"G" * 4096) == 4096
            self.grown = True
        before = os.lseek(self.handle.fileno(), 0, os.SEEK_CUR)
        result = self.handle.read(size)
        after = os.lseek(self.handle.fileno(), 0, os.SEEK_CUR)
        self.events.append((size, len(result), before, after))
        return result


def test_route_refuses_growth_with_bounded_raw_offset(own_case_directory, monkeypatch):
    payload = b"synthetic license text\n"
    fixture, lock, receipt = _single_component(own_case_directory, payload)
    target_path = fixture.materials / "synth-alpha" / "notice-0.txt"
    original_open = Path.open
    events = []

    def grow_notice_open(self, mode="r", *args, **kwargs):
        handle = original_open(self, mode, *args, **kwargs)
        if self != target_path or mode != "rb":
            return handle
        try:
            return _GrowOnFirstRead(handle, self, original_open, events)
        except BaseException:
            handle.close()
            raise

    monkeypatch.setattr(Path, "open", grow_notice_open)
    with pytest.raises(notices.SourceLockNoticeError, match="saved notice input drifted while reading"):
        notices.lock_bound_notice_payloads(receipt, source_lock=lock, target=TARGET)
    assert target_path.stat().st_size == len(payload) + 4096
    assert events and events[0][2] == 0
    assert sum(event[1] for event in events) == len(payload) + 1, events
    assert max(event[3] for event in events) <= len(payload) + 1, events
