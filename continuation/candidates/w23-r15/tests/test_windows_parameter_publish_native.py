"""Actual Windows stdlib unittest controls; no MCP or COMSOL dependency.

Run: python test_windows_parameter_publish_native.py --helper ABSOLUTE_HELPER
Mac execution covers lexical controls and SKIPS native tests; never Win PASS.
"""
from __future__ import annotations
import argparse
import ctypes as C
import hashlib
import importlib.util
import ntpath
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from unittest.mock import patch

parser = argparse.ArgumentParser(add_help=False)
parser.add_argument('--helper', default=str(Path(__file__).parents[1] / 'comsol_mcp' / '_windows_parameter_publish.py'))
arguments, remaining = parser.parse_known_args() if __name__ == "__main__" else (parser.parse_args([]), [])
spec = importlib.util.spec_from_file_location('windows_parameter_publish', arguments.helper)
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
if __name__ == "__main__":
    sys.argv = [sys.argv[0], *remaining]


class PathControls(unittest.TestCase):
    def test_ordinary_unicode_and_space_path(self):
        self.assertEqual(module.validate_paths('C:\\owned project\\目录', '导出/table name.json')[1], ('导出', 'table name.json'))

    def test_aliases_traversal_ads_reserved_names_are_refused(self):
        for destination in ('../escape', '/absolute', 'a//b', 'a/./b', 'x:y', 'NUL.txt', 'COM1', 'LPT².txt', 'a.', 'a ', 'a~1', 'a\x00b'):
            with self.subTest(destination=destination), self.assertRaises(module.PublishError):
                module.validate_paths('C:\\owned', destination)
        for root in ('C:relative', '\\\\host\\share', '\\\\?\\C:\\owned', 'C:\\owned\\..\\other'):
            with self.subTest(root=root), self.assertRaises(module.PublishError):
                module.validate_paths(root, 'table.json')

    def test_stream_metadata_is_validated_before_windows_admission(self):
        for digest, size in (("0" * 63, 1), ("g" * 64, 1), ("0" * 64, 0), ("0" * 64, True)):
            with self.subTest(digest=digest, size=size), self.assertRaises(module.PublishError) as error:
                module.publish_chunks('not-a-windows-path', 'table.json', (),
                                      expected_sha256=digest, expected_size=size)
            self.assertEqual(error.exception.code, 'INVALID_REQUEST')

    def test_private_stage_cleanup_deletes_only_exact_file_and_directory_handles(self):
        root_identity = {"device": 7, "inode": 70, "security_sha256": "a" * 64}
        file_identity = {"device": 7, "inode": 71, "security_sha256": "b" * 64}

        class FakeAPI:
            def __init__(self, *, foreign_file=False):
                self.deleted = []
                self.closed = []
                self.foreign_file = foreign_file

            def open_child_directory_for_delete(self, parent, leaf):
                return "owned-directory-handle"

            def open_child_file_for_delete(self, parent, leaf):
                return "owned-file-handle"

            def info(self, handle, *, directory=False):
                return ((7, 70), 0)

            def source_fingerprint(self, handle):
                identity = (7, 999 if self.foreign_file else 71)
                return (identity, 123, 1, 2, 1)

            def delete_owned(self, handle):
                self.deleted.append(handle)

            def close(self, handle):
                self.closed.append(handle)

            def exists_child(self, parent, leaf):
                return False

            def verify_named_binding(self, binding):
                return None

            def exists(self, path):
                return False

        @contextmanager
        def fake_pinned_parent(_parent, _leaf, *, add_file=False, _api=None):
            api = _api
            yield api, "pinned-parent-handle", ("stage",), [(r"C:\Temp", (7, 1))]

        api = FakeAPI()
        with patch.object(module.os, "name", "nt"), patch.object(module, "pinned_parent", fake_pinned_parent):
            module.delete_exact_private_stage(
                r"C:\Temp\stage", root_identity, file_identity, _api=api,
                _verify_security=lambda _path, *, directory, expected_identity: {"identity": expected_identity},
            )
        self.assertEqual(api.deleted, ["owned-file-handle", "owned-directory-handle"])
        self.assertEqual(api.closed, ["owned-file-handle", "owned-directory-handle"])

        foreign = FakeAPI(foreign_file=True)

        @contextmanager
        def foreign_parent(_parent, _leaf, *, add_file=False, _api=None):
            yield _api, "pinned-parent-handle", ("stage",), [(r"C:\Temp", (7, 1))]

        with patch.object(module.os, "name", "nt"), patch.object(module, "pinned_parent", foreign_parent):
            with self.assertRaises(module.PublishError) as caught:
                module.delete_exact_private_stage(
                    r"C:\Temp\stage", root_identity, file_identity, _api=foreign,
                    _verify_security=lambda _path, *, directory, expected_identity: {"identity": expected_identity},
                )
        self.assertEqual(caught.exception.code, "ACCESS_VIOLATION")
        self.assertEqual(foreign.deleted, [])


@unittest.skipUnless(os.name == 'nt', 'requires actual Windows kernel/filesystem')
class NativeControls(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix='comsol-v2-win-publisher-')
        self.root = Path(self.scratch.name) / '项目 with spaces'; self.root.mkdir()
        (self.root / 'export').mkdir()

    def tearDown(self):
        self.scratch.cleanup()

    def test_unicode_publish_and_no_clobber(self):
        destination = 'export/参数 table.json'; payload = '{"unicode":"温度"}'.encode()
        receipt = module.publish(self.root, destination, payload)
        self.assertEqual((self.root / destination).read_bytes(), payload)
        self.assertEqual(receipt['size_bytes'], len(payload))
        with self.assertRaises(module.PublishError) as error:
            module.publish(self.root, destination, b'competing bytes')
        self.assertEqual(error.exception.code, 'DESTINATION_EXISTS')
        self.assertEqual((self.root / destination).read_bytes(), payload)
        self.assertEqual(list((self.root / 'export').glob('*.partial')), [])

    def test_streaming_publish_checks_chunks_digest_and_size_before_rename(self):
        destination = 'export/streamed model.mph'
        payload = b'first chunk' + b' and second chunk'
        chunks = iter((b'first chunk', b'', b' and second chunk'))
        receipt = module.publish_chunks(self.root, destination, chunks,
                                        expected_sha256=hashlib.sha256(payload).hexdigest(),
                                        expected_size=len(payload))
        self.assertEqual((self.root / destination).read_bytes(), payload)
        self.assertEqual(receipt['sha256'], hashlib.sha256(payload).hexdigest())
        self.assertEqual(receipt['size_bytes'], len(payload))

        mismatch = 'export/mismatch.mph'
        with self.assertRaises(module.PublishError) as error:
            module.publish_chunks(self.root, mismatch, (b'wrong bytes',),
                                  expected_sha256=hashlib.sha256(b'expected').hexdigest(),
                                  expected_size=len(b'wrong bytes'))
        self.assertEqual(error.exception.code, 'ARTIFACT_HASH_MISMATCH')
        self.assertTrue(error.exception.details['cleanup_verified'])
        self.assertFalse((self.root / mismatch).exists())
        self.assertEqual(list((self.root / 'export').glob('*.partial')), [])

    def test_stream_reader_exception_cleans_temp_and_keeps_typed_receipt(self):
        def broken_reader():
            yield b'partial source'
            raise RuntimeError('injected reader failure')

        with self.assertRaises(module.PublishError) as error:
            module.publish_chunks(self.root, 'export/reader-error.mph', broken_reader(),
                                  expected_sha256=hashlib.sha256(b'complete source').hexdigest(),
                                  expected_size=len(b'complete source'))
        self.assertEqual(error.exception.code, 'ARTIFACT_PUBLISH_FAILED')
        self.assertEqual(error.exception.details['cause_type'], 'RuntimeError')
        self.assertTrue(error.exception.details['cleanup_verified'])
        self.assertEqual(error.exception.details['source_size_observed'], len(b'partial source'))
        self.assertFalse((self.root / 'export' / 'reader-error.mph').exists())
        self.assertEqual(list((self.root / 'export').glob('*.partial')), [])

    def test_parent_and_project_rename_blocked_while_pinned(self):
        with module.pinned_parent(self.root, 'export/table.json'):
            for source in (self.root / 'export', self.root):
                with self.subTest(source=source), self.assertRaises(OSError):
                    source.rename(source.with_name(source.name + '-moved'))
        parent = self.root / 'export'; parent.rename(self.root / 'renamed')
        self.assertTrue((self.root / 'renamed').is_dir())

    def test_junction_rejected_without_touching_target(self):
        outside = Path(self.scratch.name) / 'outside'; outside.mkdir()
        junction = self.root / 'junction'
        result = subprocess.run(['cmd', '/d', '/c', 'mklink', '/J', str(junction), str(outside)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        try:
            with self.assertRaises(module.PublishError) as error:
                module.publish(self.root, 'junction/table.json', b'no escape')
            self.assertEqual(error.exception.code, 'ACCESS_VIOLATION')
            self.assertEqual(list(outside.iterdir()), [])
        finally:
            os.rmdir(junction)  # Owned junction only, does not recurse into target.

    def test_write_failure_removes_only_owned_temp(self):
        class FailingWrite(module._API):
            def write(self, handle, payload):
                super().write(handle, payload[:3])
                raise OSError('injected write failure after actual write')
        survivor = self.root / 'export' / 'survivor'; survivor.write_bytes(b'keep')
        with self.assertRaises(module.PublishError) as error:
            module.publish(self.root, 'export/table.json', b'payload', _api=FailingWrite())
        self.assertEqual(error.exception.code, 'ARTIFACT_PUBLISH_FAILED')
        self.assertTrue(error.exception.details['cleanup_verified'])
        self.assertEqual([p.name for p in (self.root / 'export').iterdir()], ['survivor'])
        self.assertEqual(survivor.read_bytes(), b'keep')

    def test_postcommit_read_failure_rolls_back_and_reports_unknown(self):
        class FailingRead(module._API):
            def readback(self, handle):
                raise OSError('injected handle read failure')
        with self.assertRaises(module.PublishError) as error:
            module.publish(self.root, 'export/table.json', b'payload', _api=FailingRead())
        self.assertEqual(error.exception.code, 'ARTIFACT_PUBLISH_UNKNOWN')
        self.assertTrue(error.exception.details['publication_committed'])
        self.assertTrue(error.exception.details['cleanup_verified'])
        self.assertFalse((self.root / 'export' / 'table.json').exists())

    def test_postcommit_cleanup_failure_preserves_unknown_receipt(self):
        class FailingCleanup(module._API):
            def readback(self, handle):
                raise OSError('injected handle read failure')
            def delete_owned(self, handle):
                raise OSError('injected same-handle disposition failure')
        with self.assertRaises(module.PublishError) as error:
            module.publish(self.root, 'export/table.json', b'payload', _api=FailingCleanup())
        self.assertEqual(error.exception.code, 'ARTIFACT_PUBLISH_UNKNOWN')
        self.assertFalse(error.exception.details['cleanup_verified'])
        self.assertEqual((self.root / 'export' / 'table.json').read_bytes(), b'payload')

    def test_pinned_parent_reparse_race_never_writes_outside(self):
        outside = Path(self.scratch.name) / 'outside'; outside.mkdir()
        victim = self.root / 'export'
        api = module._API()
        api.k.CreateFileW.argtypes = [C.c_wchar_p, module.DWORD, module.DWORD, C.c_void_p,
                                      module.DWORD, module.DWORD, module.HANDLE]
        api.k.CreateFileW.restype = module.HANDLE
        attacker = api.k.CreateFileW(str(victim), 0x100, 0x7, None, 3,
                                     0x00200000 | 0x02000000, None)
        self.assertNotEqual(attacker, C.c_void_p(-1).value, f'CreateFileW failed: {C.get_last_error()}')

        def final_path(handle):
            get_final = api.k.GetFinalPathNameByHandleW
            get_final.argtypes = [module.HANDLE, C.c_wchar_p, module.DWORD, module.DWORD]
            get_final.restype = module.DWORD
            buffer = C.create_unicode_buffer(32768)
            length = get_final(handle, buffer, len(buffer), 0)
            self.assertTrue(0 < length < len(buffer), f'GetFinalPathNameByHandleW failed: {C.get_last_error()}')
            return buffer.value

        victim_name = os.path.normcase(ntpath.normpath(final_path(attacker)))
        outside_handle = api.k.CreateFileW(str(outside), 0x80, 0x7, None, 3,
                                           0x00200000 | 0x02000000, None)
        self.assertNotEqual(outside_handle, C.c_void_p(-1).value,
                            f'opening outside directory failed: {C.get_last_error()}')
        outside_name = os.path.normcase(ntpath.normpath(final_path(outside_handle)))

        fsctl_set_reparse_point = 0x000900A4
        fsctl_delete_reparse_point = 0x000900A8
        mount_point_tag = 0xA0000003
        injected = {'value': False}

        def device_io(api_instance, code, data):
            returned = module.DWORD()
            buffer = C.create_string_buffer(data)
            ok = api_instance.k.DeviceIoControl(attacker, code, buffer, len(data), None, 0,
                                                C.byref(returned), None)
            self.assertTrue(ok, f'DeviceIoControl(0x{code:08x}) failed: {C.get_last_error()}')

        def set_junction(api_instance):
            substitute = ('\\??\\' + str(outside.resolve())).encode('utf-16-le')
            printable = str(outside.resolve()).encode('utf-16-le')
            path_buffer = substitute + b'\x00\x00' + printable + b'\x00\x00'
            payload = (len(path_buffer) + 8).to_bytes(2, 'little')
            reparse_data = (b'\x00\x00' + len(substitute).to_bytes(2, 'little') +
                            (len(substitute) + 2).to_bytes(2, 'little') +
                            len(printable).to_bytes(2, 'little') + path_buffer)
            data = (mount_point_tag.to_bytes(4, 'little') + payload + b'\x00\x00' + reparse_data)
            device_io(api_instance, fsctl_set_reparse_point, data)
            injected['value'] = True

        class ReparseOnCreate(module._API):
            def __init__(self):
                super().__init__()
                self.write_targets = []

            def create_owned(self, parent_handle, leaf):
                # This runs after all directory handles have been pinned and
                # immediately before NtCreateFile is issued for the temp leaf.
                set_junction(self)
                return super().create_owned(parent_handle, leaf)

            def write(self, handle, payload):
                self.write_targets.append(final_path(handle))
                return super().write(handle, payload)

        race_error = None
        race_receipt = None
        try:
            raced = ReparseOnCreate()
            try:
                race_receipt = module.publish(self.root, 'export/table.json', b'outside must stay empty',
                                              _api=raced)
            except module.PublishError as error:
                race_error = error
                self.assertFalse(error.details.get('publication_committed', False), error.details)
                self.assertIn(error.code, {'ACCESS_VIOLATION', 'ARTIFACT_PUBLISH_FAILED',
                                           'ARTIFACT_PUBLISH_UNKNOWN'}, error.details)
                self.assertIsInstance(error.details, dict)
                self.assertIn('cleanup_verified', error.details)
            self.assertTrue(injected['value'])
            for target in raced.write_targets:
                target_name = os.path.normcase(ntpath.normpath(target))
                self.assertFalse(target_name == outside_name or target_name.startswith(outside_name + os.sep),
                                 f'publication wrote through the junction: {target}')
                self.assertTrue(target_name == victim_name or target_name.startswith(victim_name + os.sep),
                                f'publication escaped the pinned parent: {target}')
            self.assertEqual(list(outside.iterdir()), [])
            if race_receipt is not None:
                self.assertEqual((victim / 'table.json').read_bytes(), b'outside must stay empty')
            elif race_error.details.get('cleanup_verified'):
                self.assertEqual(list(victim.iterdir()), [])
        finally:
            if injected['value']:
                device_io(api, fsctl_delete_reparse_point,
                          mount_point_tag.to_bytes(4, 'little') + b'\x00\x00\x00\x00')
            api.close(outside_handle)
            api.close(attacker)
        self.assertEqual(list(outside.iterdir()), [])
        if race_receipt is None and race_error is not None and race_error.details.get('cleanup_verified'):
            self.assertEqual(list(victim.iterdir()), [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
