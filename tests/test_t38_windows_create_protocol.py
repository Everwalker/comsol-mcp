"""Abstract namespace simulation for the current Windows publisher.

This checks orchestration around the production API seam only. It does not
model or prove Windows sharing, rename, or disposition semantics.
"""
from contextlib import contextmanager
import ctypes as C
from dataclasses import dataclass, field
import hashlib
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch

from comsol_mcp import _windows_parameter_publish as publish


@dataclass
class Node:
    identity: tuple[int, int]
    data: bytearray = field(default_factory=bytearray)
    deleted: bool = False


class FakeApi:
    def __init__(self, *, replace_stage_during_write=False, collide_at_rename=False,
                 ambiguous_create=False, stage_collision=False):
        self.entries = {}
        self.next_identity = 2
        self.stage_leaf = None
        self.replacement = None
        self.replace_stage_during_write = replace_stage_during_write
        self.collide_at_rename = collide_at_rename
        self.ambiguous_create = ambiguous_create
        self.stage_collision = stage_collision
        self.root_identity = (7, 1)
        self.delete_calls = []
        self.close_calls = []
        self.exists_checks = []

    def exists_child(self, _parent, leaf):
        self.exists_checks.append(leaf)
        return leaf in self.entries

    def create_owned(self, _parent, leaf):
        self.stage_leaf = leaf
        if self.stage_collision or leaf in self.entries:
            raise FileExistsError(183, "stage already exists")
        node = Node((7, self.next_identity))
        self.next_identity += 1
        self.entries[leaf] = node
        if self.ambiguous_create:
            raise publish.PublishError(
                "ACQUISITION_STATE_UNKNOWN",
                "injected native output whose create receipt was not returned",
                details={"safe_retry": False},
                stage="post_dispatch",
            )
        return node

    def info(self, handle, *, directory=False):
        assert not directory
        return handle.identity, len(handle.data)

    def write(self, handle, payload):
        handle.data.extend(payload)
        if self.replace_stage_during_write:
            self.replacement = Node((7, self.next_identity), bytearray(b"foreign stage"))
            self.next_identity += 1
            self.entries[self.stage_leaf] = self.replacement
            raise OSError("injected failure after staging-name replacement")

    def flush(self, _handle):
        return None

    def named_identity(self, _path, *, directory=False):
        assert directory
        return self.root_identity

    def rename(self, handle, _parent, leaf):
        if self.collide_at_rename:
            competitor = Node((7, self.next_identity), bytearray(b"foreign target"))
            self.next_identity += 1
            self.entries[leaf] = competitor
            raise FileExistsError(183, "destination appeared after precheck")
        if leaf in self.entries:
            raise FileExistsError(183, "destination exists")
        if self.entries.get(self.stage_leaf) is handle:
            del self.entries[self.stage_leaf]
        self.entries[leaf] = handle

    def readback(self, handle):
        payload = bytes(handle.data)
        return hashlib.sha256(payload).hexdigest(), len(payload)

    def child_identity(self, _parent, leaf):
        return self.entries[leaf].identity

    def delete_owned(self, handle):
        self.delete_calls.append(handle)
        handle.deleted = True
        for leaf, current in list(self.entries.items()):
            if current is handle:
                del self.entries[leaf]

    def close(self, _handle):
        self.close_calls.append(_handle)


class FakeRegistry:
    def __init__(self, acquire_error=None):
        self.entries = []
        self.acquire_error = acquire_error

    def acquire(self, adapter, handle, closer, *, label, keepalive=()):
        if self.acquire_error is not None:
            raise self.acquire_error
        entry = SimpleNamespace(adapter=adapter, handle=handle, closer=closer, label=label,
                                keepalive=tuple(keepalive), state="ACQUIRED", error=None)
        self.entries.append(entry)
        return entry

    def find(self, adapter, handle):
        return next((entry for entry in self.entries
                     if entry.adapter is adapter and entry.handle.value == handle.value), None)


def _native_create_case(*, status=0, information=2, output_value=0x1234,
                        raised=None, object_created=True, registry_error=None):
    api = publish._API.__new__(publish._API)
    registry = FakeRegistry(registry_error)
    close_calls = []
    retained = []
    state = {"staging_present": object_created}
    closer = lambda handle: close_calls.append(handle) or True

    def nt_create(output_arg, _access, _attributes, io_arg, *_args):
        output = C.cast(output_arg, C.POINTER(publish.HANDLE)).contents
        io_status = C.cast(io_arg, C.POINTER(publish._IoStatusBlock)).contents
        if output_value is not None:
            output.value = output_value
        io_status.Status = status
        io_status.Information = information
        if raised is not None:
            raise raised
        return status

    api.nt = SimpleNamespace(NtCreateFile=nt_create, RtlNtStatusToDosError=lambda _status: 183)
    api._ownership_registry = lambda: registry
    api._resource_closer = lambda: closer
    api.close = lambda handle: close_calls.append(handle)
    state_module = SimpleNamespace(
        retain_unknown=lambda owner, primary, **kwargs:
            retained.append({"owner": owner, "primary": primary, **kwargs})
    )
    return api, registry, close_calls, retained, state, state_module


def _publish(api):
    @contextmanager
    def pinned_parent(project_root, destination, *, _api=None):
        _root, parts = publish.validate_paths(project_root, destination)
        yield api, object(), parts, [(project_root, api.root_identity)]

    payload = b"complete package bytes"
    with patch.object(publish, "pinned_parent", pinned_parent):
        return publish.publish_chunks(
            r"C:\project", "packages/model.zip", (payload,),
            expected_sha256=hashlib.sha256(payload).hexdigest(),
            expected_size=len(payload),
            _api=api,
        )


class ProtocolSimulationTests(unittest.TestCase):
    def test_rename_uses_source_handle_and_pinned_parent_without_replace(self):
        api = publish._API.__new__(publish._API)
        source = C.c_void_p(101)
        parent = C.c_void_p(202)
        calls = []

        def native(handle, io_status_arg, buffer, length, info_class):
            calls.append(info_class)
            info = C.cast(buffer, C.POINTER(publish._RenameInfo)).contents
            self.assertIs(handle, source)
            self.assertEqual(info.RootDirectory, 202)
            self.assertEqual(info.ReplaceIfExists, 0)
            self.assertEqual(info.FileNameLength % 2, 0)
            self.assertEqual(info_class, 10)
            name = C.string_at(
                C.addressof(buffer) + publish._RenameInfo.FileName.offset,
                info.FileNameLength,
            ).decode("utf-16-le")
            self.assertEqual(name, "模型.zip")
            status = io_status_arg._obj
            status.Status = 0
            status.Information = 0
            return 0

        api.k = SimpleNamespace(CloseHandle=lambda _handle: 1)
        api.nt = SimpleNamespace(
            NtSetInformationFile=native,
            RtlNtStatusToDosError=lambda _status: 183,
        )
        api._ownership_registry = lambda: None
        api.rename(source, parent, "模型.zip")
        self.assertEqual(calls, [10])

    def test_successful_publish_commits_the_owned_stage(self):
        api = FakeApi()

        receipt = _publish(api)

        self.assertEqual(receipt["relative_path"], "packages/model.zip")
        self.assertEqual(bytes(api.entries["model.zip"].data), b"complete package bytes")
        self.assertEqual(api.delete_calls, [])

    def test_native_create_exception_retains_original_output_as_unknown(self):
        api, registry, close_calls, retained, state, restore_module = _native_create_case(
            raised=KeyboardInterrupt("injected after native dispatch"),
        )

        with patch.dict(sys.modules, {"comsol_mcp._windows_state_restore": restore_module}):
            with self.assertRaises(publish.PublishError) as caught:
                api._open_relative(C.c_void_p(900), "stage.partial", create=True)

        error = caught.exception
        self.assertEqual(error.code, "ACQUISITION_STATE_UNKNOWN")
        self.assertEqual(error.stage, "post_dispatch")
        self.assertIs(error.safe_retry, False)
        self.assertEqual(error.original_native_output.value, 0x1234)
        self.assertEqual(error.original_native_context[-1].Information, 2)
        self.assertEqual(registry.entries[0].state, "UNKNOWN")
        self.assertIs(registry.entries[0].handle, error.original_native_output)
        self.assertTrue(state["staging_present"])
        self.assertEqual(close_calls, [])
        self.assertIs(retained[0]["output"], error.original_native_output)

    def test_success_status_with_wrong_create_information_is_unknown(self):
        api, registry, close_calls, retained, state, restore_module = _native_create_case(
            information=1,
        )

        with patch.dict(sys.modules, {"comsol_mcp._windows_state_restore": restore_module}):
            with self.assertRaises(publish.PublishError) as caught:
                api._open_relative(C.c_void_p(900), "stage.partial", create=True)

        error = caught.exception
        self.assertEqual(error.code, "ACQUISITION_STATE_UNKNOWN")
        self.assertEqual(error.stage, "post_dispatch")
        self.assertIs(error.safe_retry, False)
        self.assertEqual(error.__cause__.code, "NATIVE_COMPLETION_MISMATCH")
        self.assertEqual(error.original_native_output.value, 0x1234)
        self.assertEqual(error.original_native_context[-1].Information, 1)
        self.assertEqual(registry.entries[0].state, "UNKNOWN")
        self.assertTrue(state["staging_present"])
        self.assertEqual(close_calls, [])
        self.assertIs(retained[0]["output"], error.original_native_output)

    def test_successful_create_information_registers_acquired_handle(self):
        api, registry, close_calls, retained, state, restore_module = _native_create_case()

        with patch.dict(sys.modules, {"comsol_mcp._windows_state_restore": restore_module}):
            output = api._open_relative(C.c_void_p(900), "stage.partial", create=True)

        self.assertEqual(output.value, 0x1234)
        self.assertEqual(registry.entries[0].state, "ACQUIRED")
        self.assertIs(registry.entries[0].handle, output)
        self.assertTrue(state["staging_present"])
        self.assertEqual(close_calls, [])
        self.assertEqual(retained, [])

    def test_incomplete_success_output_is_not_treated_as_absent(self):
        api, registry, close_calls, retained, state, restore_module = _native_create_case(
            output_value=None,
        )

        with patch.dict(sys.modules, {"comsol_mcp._windows_state_restore": restore_module}):
            with self.assertRaises(publish.PublishError) as caught:
                api._open_relative(C.c_void_p(900), "stage.partial", create=True)

        self.assertEqual(caught.exception.code, "ACQUISITION_STATE_UNKNOWN")
        self.assertFalse(caught.exception.details["native_output_present"])
        self.assertEqual(registry.entries, [])
        self.assertTrue(state["staging_present"])
        self.assertEqual(close_calls, [])
        self.assertIsNone(retained[0]["output"].value)

    def test_registry_capture_failure_still_retains_native_output_as_unknown(self):
        registry_error = RuntimeError("injected registry capture failure")
        api, registry, close_calls, retained, state, restore_module = _native_create_case(
            registry_error=registry_error,
        )

        with patch.dict(sys.modules, {"comsol_mcp._windows_state_restore": restore_module}):
            with self.assertRaises(publish.PublishError) as caught:
                api._open_relative(C.c_void_p(900), "stage.partial", create=True)

        error = caught.exception
        self.assertEqual(error.code, "ACQUISITION_STATE_UNKNOWN")
        self.assertIs(error.safe_retry, False)
        self.assertIs(error.ownership_registry_error, registry_error)
        self.assertEqual(error.original_native_output.value, 0x1234)
        self.assertEqual(registry.entries, [])
        self.assertTrue(state["staging_present"])
        self.assertEqual(close_calls, [])
        self.assertIs(retained[0]["output"], error.original_native_output)

    def test_file_create_name_collision_remains_a_definite_refusal(self):
        collision = C.c_int32(0xC0000035).value
        api, registry, close_calls, retained, state, restore_module = _native_create_case(
            status=collision, information=0, output_value=None, object_created=False,
        )

        with patch.dict(sys.modules, {"comsol_mcp._windows_state_restore": restore_module}):
            with self.assertRaises(FileExistsError):
                api._open_relative(C.c_void_p(900), "stage.partial", create=True)

        self.assertEqual(registry.entries, [])
        self.assertEqual(retained, [])
        self.assertEqual(close_calls, [])
        self.assertFalse(state["staging_present"])

    def test_failed_native_status_with_output_remains_unknown(self):
        failed_status = C.c_int32(0xC0000001).value
        api, registry, close_calls, retained, state, restore_module = _native_create_case(
            status=failed_status, information=0,
        )

        with patch.dict(sys.modules, {"comsol_mcp._windows_state_restore": restore_module}):
            with self.assertRaises(publish.PublishError) as caught:
                api._open_relative(C.c_void_p(900), "stage.partial", create=True)

        error = caught.exception
        self.assertEqual(error.code, "ACQUISITION_STATE_UNKNOWN")
        self.assertEqual(error.original_native_output.value, 0x1234)
        self.assertEqual(registry.entries[0].state, "UNKNOWN")
        self.assertTrue(state["staging_present"])
        self.assertEqual(close_calls, [])
        self.assertIs(retained[0]["output"], error.original_native_output)

    def test_stage_name_collision_keeps_the_existing_failure_contract(self):
        api = FakeApi(stage_collision=True)

        with self.assertRaises(publish.PublishError) as caught:
            _publish(api)

        self.assertEqual(caught.exception.code, "ARTIFACT_PUBLISH_FAILED")
        self.assertTrue(caught.exception.details["cleanup_verified"])
        self.assertEqual(caught.exception.details["residual_status"], "ABSENT")
        self.assertEqual(caught.exception.details["final_state"], "ROLLED_BACK")
        self.assertNotIn(api.stage_leaf, api.entries)
        self.assertEqual(api.delete_calls, [])

    def test_no_clobber_collision_preserves_the_competing_destination(self):
        api = FakeApi(collide_at_rename=True)

        with self.assertRaises(publish.PublishError) as caught:
            _publish(api)

        self.assertEqual(caught.exception.code, "DESTINATION_EXISTS")
        self.assertTrue(caught.exception.details["cleanup_verified"])
        self.assertEqual(caught.exception.details["final_state"], "ROLLED_BACK")
        self.assertEqual(bytes(api.entries["model.zip"].data), b"foreign target")
        self.assertNotIn(api.stage_leaf, api.entries)

    def test_stage_name_replacement_is_not_deleted_by_handle_cleanup(self):
        api = FakeApi(replace_stage_during_write=True)

        with self.assertRaises(publish.PublishError) as caught:
            _publish(api)

        self.assertEqual(caught.exception.code, "ARTIFACT_PUBLISH_UNKNOWN")
        self.assertFalse(caught.exception.details["cleanup_verified"])
        self.assertEqual(caught.exception.details["residual_status"], "UNKNOWN")
        self.assertEqual(caught.exception.details["final_state"], "UNKNOWN")
        self.assertIs(api.entries[api.stage_leaf], api.replacement)
        self.assertEqual(bytes(api.replacement.data), b"foreign stage")

    def test_ambiguous_create_is_reported_unknown_without_cleanup_or_name_probe(self):
        api = FakeApi(ambiguous_create=True)

        with self.assertRaises(publish.PublishError) as caught:
            _publish(api)

        self.assertEqual(caught.exception.code, "ARTIFACT_PUBLISH_UNKNOWN")
        self.assertEqual(caught.exception.stage, "post_dispatch")
        self.assertIs(caught.exception.safe_retry, False)
        self.assertTrue(caught.exception.details["partial_change"])
        self.assertFalse(caught.exception.details["publication_committed"])
        self.assertFalse(caught.exception.details["cleanup_verified"])
        self.assertEqual(caught.exception.details["residual_status"], "UNKNOWN")
        self.assertEqual(caught.exception.details["final_state"], "UNKNOWN")
        self.assertEqual(caught.exception.details["staging_state"], "UNKNOWN")
        self.assertEqual(caught.exception.details["cause_code"], "ACQUISITION_STATE_UNKNOWN")
        self.assertIn(api.stage_leaf, api.entries)
        self.assertEqual(api.delete_calls, [])
        self.assertEqual(api.close_calls, [])
        self.assertEqual(api.exists_checks, ["model.zip"])

    def test_pre_dispatch_destination_refusal_remains_definite(self):
        api = FakeApi()
        existing = Node((7, 99), bytearray(b"preserve destination"))
        api.entries["model.zip"] = existing

        with self.assertRaises(publish.PublishError) as caught:
            _publish(api)

        self.assertEqual(caught.exception.code, "DESTINATION_EXISTS")
        self.assertEqual(caught.exception.stage, "pre_dispatch")
        self.assertEqual(caught.exception.details, {})
        self.assertIs(api.entries["model.zip"], existing)
        self.assertEqual(api.delete_calls, [])


if __name__ == "__main__":
    unittest.main()
