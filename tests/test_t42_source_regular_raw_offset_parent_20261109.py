"""Assert the raw opened-file offset, not just buffered return size.

Only a test-owned regular file is grown. This does not measure physical disk
I/O or establish safety against pathname races or same-size replacement.
"""
import os
from pathlib import Path

import pytest

from tools import source_lock_notices as notices


@pytest.fixture
def own_case_directory(tmp_path_factory, request):
    # No numbered directory: avoid replacing pytest's `current` symlink.
    return tmp_path_factory.mktemp(request.node.name, numbered=False)


class _OffsetObservedHandle:
    def __init__(self, handle, path, original_open, growth, events, fired):
        self.handle = handle
        self.path = path
        self.original_open = original_open
        self.growth = growth
        self.events = events
        self.fired = fired

    def __enter__(self):
        self.handle.__enter__()
        return self

    def __exit__(self, *args):
        return self.handle.__exit__(*args)

    def read(self, size=-1):
        if self.growth is not None:
            with self.original_open(self.path, "ab") as writer:
                assert writer.write(self.growth) == len(self.growth)
            self.fired.append(True)
            self.growth = None
        before = os.lseek(self.handle.fileno(), 0, os.SEEK_CUR)
        data = self.handle.read(size)
        after = os.lseek(self.handle.fileno(), 0, os.SEEK_CUR)
        self.events.append((size, len(data), before, after))
        return data


def _assert_bounded_growth(directory, monkeypatch, initial, growth, maximum):
    path = directory / "source.bin"
    with path.open("xb") as writer:
        assert writer.write(initial) == len(initial)
    assert path.stat().st_size == len(initial)
    original_open = Path.open
    events = []
    fired = []

    def observed_open(self, mode="r", *args, **kwargs):
        handle = original_open(self, mode, *args, **kwargs)
        if self == path and mode == "rb":
            return _OffsetObservedHandle(handle, self, original_open, growth, events, fired)
        return handle

    monkeypatch.setattr(Path, "open", observed_open)
    with pytest.raises(notices.SourceLockNoticeError, match="drifted while reading"):
        notices._read_regular(path, maximum, "synthetic")

    assert fired == [True]
    assert path.stat().st_size == len(initial) + len(growth)
    assert events and events[0][2] == 0
    assert sum(event[1] for event in events) <= len(initial) + 1, events
    assert max(event[3] for event in events) <= len(initial) + 1, events


def test_raw_offset_small(own_case_directory, monkeypatch):
    _assert_bounded_growth(own_case_directory, monkeypatch, b"seed", b"G" * 50, 16)


def test_raw_offset_notice_limit(own_case_directory, monkeypatch):
    maximum = notices.MAX_NOTICE_BYTES
    _assert_bounded_growth(own_case_directory, monkeypatch, b"A" * maximum, b"G" * 300_000, maximum)
