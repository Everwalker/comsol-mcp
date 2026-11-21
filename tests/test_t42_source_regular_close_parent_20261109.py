"""Short raw reads and real-handle closure for source-notice input reads."""
import errno
import io
from pathlib import Path

import pytest

from tools import source_lock_notices as notices


@pytest.fixture
def own_case_directory(tmp_path_factory, request):
    return tmp_path_factory.mktemp(request.node.name, numbered=False)


class _ShortReadHandle:
    """Cap requests on an owned raw handle; optionally fail after one read."""

    def __init__(self, handle, fail_after_first):
        self.handle = handle
        self.fail_after_first = fail_after_first
        self.reads = []
        self.exits = 0

    def __enter__(self):
        self.handle.__enter__()
        return self

    def __exit__(self, *args):
        self.exits += 1
        return self.handle.__exit__(*args)

    def read(self, size=-1):
        if self.fail_after_first and self.reads:
            raise OSError(errno.EIO, "synthetic read failure after short read")
        # The unbounded baseline's read-to-EOF is not simulated as a short read.
        requested = min(size, 3) if size >= 0 else size
        data = self.handle.read(requested)
        self.reads.append((size, len(data)))
        return data


def _instrument(path, monkeypatch, *, fail_after_first):
    original_open = Path.open
    observed = []

    def observed_open(self, mode="r", *args, **kwargs):
        handle = original_open(self, mode, *args, **kwargs)
        if self != path or mode != "rb":
            return handle
        try:
            assert isinstance(handle, io.RawIOBase), type(handle)
            wrapper = _ShortReadHandle(handle, fail_after_first)
            observed.append(wrapper)
            return wrapper
        except BaseException:
            handle.close()
            raise

    monkeypatch.setattr(Path, "open", observed_open)
    return observed


def _write_new(path, data):
    with path.open("xb") as writer:
        assert writer.write(data) == len(data)


def _close_if_never_exited(observed):
    # Only close a still-owned handle when no context-exit close was attempted.
    for wrapper in observed:
        if wrapper.exits == 0 and not wrapper.handle.closed:
            wrapper.handle.close()


def test_short_reads_preserve_data_and_close(own_case_directory, monkeypatch):
    path = own_case_directory / "input.bin"
    payload = b"abcdefghij"
    _write_new(path, payload)
    observed = _instrument(path, monkeypatch, fail_after_first=False)

    try:
        assert notices._read_regular(path, len(payload), "synthetic") == payload
        assert len(observed) == 1
        handle = observed[0]
        assert len([returned for _, returned in handle.reads if returned]) > 1, handle.reads
        assert handle.exits == 1 and handle.handle.closed
    finally:
        _close_if_never_exited(observed)


def test_error_after_short_read_closes_handle(own_case_directory, monkeypatch):
    path = own_case_directory / "input.bin"
    payload = b"abcdefghij"
    _write_new(path, payload)
    observed = _instrument(path, monkeypatch, fail_after_first=True)

    try:
        with pytest.raises(OSError, match="synthetic read failure after short read"):
            notices._read_regular(path, len(payload), "synthetic")
        assert len(observed) == 1
        handle = observed[0]
        assert handle.reads == [(len(payload) + 1, 3)]
        assert handle.exits == 1 and handle.handle.closed
    finally:
        _close_if_never_exited(observed)
