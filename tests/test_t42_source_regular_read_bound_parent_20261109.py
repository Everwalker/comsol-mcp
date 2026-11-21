"""Bounded source-notice input reads under synthetic file growth.

The fault changes only a file created by the individual test. It does not
establish protection against same-size replacement or other pathname races.
"""
from pathlib import Path

import pytest

from tools import source_lock_notices as notices


class _ObservedHandle:
    def __init__(self, handle, reads, before_read=None):
        self.handle = handle
        self.reads = reads
        self.before_read = before_read

    def __enter__(self):
        self.handle.__enter__()
        return self

    def __exit__(self, *args):
        return self.handle.__exit__(*args)

    def read(self, size=-1):
        if self.before_read is not None:
            callback, self.before_read = self.before_read, None
            callback()
        raw = self.handle.read(size)
        self.reads.append((size, len(raw)))
        return raw


def _write_new(path, data):
    with path.open("xb") as handle:
        handle.write(data)


def test_growth_refusal_has_bounded_read(tmp_path, monkeypatch):
    path = tmp_path / "source.bin"
    _write_new(path, b"seed")
    initial_size = path.stat().st_size
    maximum = 16
    growth = b"G" * 50
    reads = []
    fired = []
    original_open = Path.open

    def open_with_growth(self, mode="r", *args, **kwargs):
        handle = original_open(self, mode, *args, **kwargs)
        if self != path or mode != "rb":
            return handle

        def grow_before_read():
            with original_open(self, "ab") as writer:
                writer.write(growth)
            fired.append(True)

        return _ObservedHandle(handle, reads, grow_before_read)

    monkeypatch.setattr(Path, "open", open_with_growth)
    with pytest.raises(notices.SourceLockNoticeError, match="drifted while reading"):
        notices._read_regular(path, maximum, "synthetic")

    assert fired == [True]
    assert path.stat().st_size == initial_size + len(growth)
    assert reads
    assert all(0 <= requested <= maximum + 1 for requested, _ in reads), reads
    assert sum(returned for _, returned in reads) <= maximum + 1, reads


def test_small_valid_input_avoids_maximum_sized_read(tmp_path, monkeypatch):
    path = tmp_path / "source.bin"
    expected = b"A" * 10_000
    _write_new(path, expected)
    maximum = 32 * 1024 * 1024
    reads = []
    original_open = Path.open

    def observe_open(self, mode="r", *args, **kwargs):
        handle = original_open(self, mode, *args, **kwargs)
        if self == path and mode == "rb":
            return _ObservedHandle(handle, reads)
        return handle

    monkeypatch.setattr(Path, "open", observe_open)
    assert notices._read_regular(path, maximum, "synthetic") == expected
    assert reads
    assert all(0 <= requested <= 1_000_000 for requested, _ in reads), reads
    assert sum(returned for _, returned in reads) == len(expected), reads


def test_normal_files_at_byte_limit_are_unchanged(tmp_path):
    maximum = 16
    for name, data in (("empty", b""), ("below", b"small"), ("exact", b"A" * maximum)):
        path = tmp_path / name
        _write_new(path, data)
        assert notices._read_regular(path, maximum, "synthetic") == data
