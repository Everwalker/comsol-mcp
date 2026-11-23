"""Synthetic Windows API test of root identity binding; not native Windows evidence."""
import hashlib
import ntpath

import pytest  # pyright: ignore[reportMissingImports]

from comsol_mcp import _windows_parameter_publish as publisher


class FakeWindowsAPI:
    def __init__(self):
        self.identities = {"C:\\": (7, 1), "C:\\owned": (7, 20), "C:\\owned\\export": (7, 30)}
        self.created = []
        self.closed = []
        self.data = b""
        self.destination = None

    def open(self, path, **_kwargs):
        return path

    def open_child_directory(self, parent, part, **_kwargs):
        return ntpath.join(parent, part)

    def info(self, handle, **_kwargs):
        return ((7, 99) if handle == "owned-file" else self.identities[handle], 0)

    def named_identity(self, path, **_kwargs):
        return self.identities[path]

    def exists_child(self, parent, leaf):
        return self.destination == ntpath.join(parent, leaf)

    def create_owned(self, parent, leaf):
        self.created.append((parent, leaf))
        return "owned-file"

    def write(self, _handle, data):
        self.data += data

    def flush(self, _handle):
        pass

    def rename(self, _handle, parent, leaf):
        self.destination = ntpath.join(parent, leaf)

    def readback(self, _handle):
        return hashlib.sha256(self.data).hexdigest(), len(self.data)

    def child_identity(self, _parent, _leaf):
        return (7, 99)

    def close(self, handle):
        self.closed.append(handle)


@pytest.mark.parametrize("expected,accepted", [((7, 20), True), ((7, 21), False)])
def test_windows_pinned_root_identity_before_any_file_creation(expected, accepted):
    api = FakeWindowsAPI()
    payload = b"report"
    kwargs = dict(expected_sha256=hashlib.sha256(payload).hexdigest(), expected_size=len(payload),
                  expected_project_identity=expected, _api=api)
    if accepted:
        receipt = publisher.publish_chunks("C:\\owned", "export/report.json", (payload,), **kwargs)
        assert receipt["relative_path"] == "export/report.json"
        assert api.data == payload and len(api.created) == 1
    else:
        with pytest.raises(publisher.PublishError) as exc:
            publisher.publish_chunks("C:\\owned", "export/report.json", (payload,), **kwargs)
        assert exc.value.code == "PROJECT_WORKSPACE_IDENTITY_MISMATCH"
        assert api.created == [] and api.data == b"" and api.destination is None
    assert set(api.closed) >= {"C:\\", "C:\\owned", "C:\\owned\\export"}
