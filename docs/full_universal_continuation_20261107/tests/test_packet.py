from __future__ import annotations
import hashlib, io, json, os, shutil, stat, sys, tempfile, unittest, zipfile
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
import bootstrap as b
import verify_package as v
import selected_tests as s

def manifest(root):
    rows = []
    for path in sorted(root.rglob('*')):
        if path.is_file() and path.name != 'PACKAGE_MANIFEST.json':
            raw = path.read_bytes()
            rows.append({'path': path.relative_to(root).as_posix(),
                         'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()})
    (root/'PACKAGE_MANIFEST.json').write_text(json.dumps({'files': rows}), encoding='utf-8')

def fixture(base):
    packet = base/'packet'
    packet.mkdir()
    original = [
        ('AGENTS.md', b'original upstream instructions\n'),
        ('pyproject.toml', b'[project]\nname="fixture"\n'),
        ('docs/universal_mcp_execution/state/REVIEW.json', b'{"status":"NOT_RUN"}\n'),
    ]
    rows = []
    archive = base/'fixture.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        for name, raw in original:
            blob = hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
            rows.append({'path': name, 'mode': '100644', 'blob': blob})
            info = zipfile.ZipInfo('fixture-root/'+name)
            info.create_system = 3
            info.external_attr = (stat.S_IFREG|0o644)<<16
            z.writestr(info, raw)
    pin = json.loads((ROOT/'PIN.json').read_text(encoding='utf-8'))
    pin['tree'] = b.tree_digest(rows)
    for name, text in {
        'PIN.json': json.dumps(pin), 'AGENTS.md': 'continuation instructions\n',
        'TASK.md': 'all frozen requirements\n', 'GOAL.txt': 'continuous scope\n',
        'references/note.md': 'reference only\n',
    }.items():
        path = packet/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
    manifest(packet)
    return packet, pin, archive, original

class PacketTests(unittest.TestCase):
    def test_integrity_and_corruption(self):
        with tempfile.TemporaryDirectory() as temp:
            packet, _, _, _ = fixture(Path(temp))
            self.assertEqual(v.verify(packet)['status'], 'WORKPACK_INTEGRITY_PASS')
            (packet/'TASK.md').write_text('changed', encoding='utf-8')
            self.assertEqual(v.verify(packet)['status'], 'FAIL')

    def test_complete_archive_entry_copies_all_packet_files_preserves_history(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            packet, pin, archive, original = fixture(base)
            target = base/'目录 带 空格'
            with patch.object(b, 'ROOT', packet), patch.object(b, 'download',
                    side_effect=lambda url, path: shutil.copyfile(archive, path)), redirect_stdout(io.StringIO()):
                result = b.main(['--method','archive','--target',str(target)])
            self.assertEqual(result, 0)
            for name, raw in original:
                self.assertEqual((target/name).read_bytes(), raw)
            overlay = target/pin['handoff_directory']
            self.assertTrue((overlay/'AGENTS.md').is_file())
            self.assertTrue((overlay/'TASK.md').is_file())
            self.assertEqual(v.verify(overlay)['status'], 'WORKPACK_INTEGRITY_PASS')
            receipt = json.loads((overlay/'RESTORE_RECEIPT.json').read_text())
            self.assertEqual(receipt['commit'], pin['commit'])
            self.assertEqual(receipt['candidate_status'], pin['candidate_status'])
            self.assertFalse(receipt['runtime_authority_restored'])
            self.assertFalse(receipt['comsol_started'])
            self.assertFalse(receipt['git_history_ready'])

    def test_existing_target_not_touched_or_fetched(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            packet, _, _, _ = fixture(base)
            target = base/'existing'
            target.mkdir()
            (target/'keep').write_bytes(b'user-data')
            with patch.object(b, 'ROOT', packet), patch.object(b,'download') as down, redirect_stdout(io.StringIO()):
                result = b.main(['--method','archive','--target',str(target)])
            self.assertEqual(result, 2)
            self.assertEqual((target/'keep').read_bytes(), b'user-data')
            down.assert_not_called()

    def test_git_failure_does_not_fallback_or_change_target(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            packet, _, _, _ = fixture(base)
            target = base/'new'
            with patch.object(b, 'ROOT', packet), patch.object(b,'restore_git',
                    side_effect=RuntimeError('synthetic fetch failure')), patch.object(b,'download') as down, redirect_stdout(io.StringIO()):
                result = b.main(['--method','git','--target',str(target)])
            self.assertEqual(result, 2)
            self.assertFalse(target.exists())
            down.assert_not_called()
            self.assertTrue((packet/'_state/recovery_failure.json').is_file())

    def test_tampered_packet_refused_before_network(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            packet, _, _, _ = fixture(base)
            (packet/'GOAL.txt').write_text('untrusted replacement')
            with patch.object(b, 'ROOT', packet), patch.object(b,'restore_git') as restore, redirect_stdout(io.StringIO()):
                result = b.main(['--method','git','--target',str(base/'new')])
            self.assertEqual(result, 2)
            restore.assert_not_called()

    def make_fake_repo(self, base):
        repo = base/'repo'
        repo.mkdir()
        (repo/'pyproject.toml').write_text('[project]\nname="fixture"\n')
        for node in s.selected_nodes():
            path = repo/node.split('::')[0]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('# synthetic empty module; never imported by this test\n')
        return repo

    def test_selected_print_does_not_run_pytest(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = self.make_fake_repo(Path(temp))
            with patch.object(s.subprocess,'run') as run, redirect_stdout(io.StringIO()):
                self.assertEqual(s.main(['--repo',str(repo)]), 0)
            run.assert_not_called()
            self.assertFalse((repo/'_state').exists())

    def test_selected_run_retains_exact_nodes_and_plugin_isolation(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = self.make_fake_repo(Path(temp))
            with patch.dict(os.environ, {'PYTEST_ADDOPTS': 'tests/test_java_worker.py', 'PYTEST_PLUGINS': 'unrelated_external_plugin'}), patch.object(s.subprocess,'run',return_value=SimpleNamespace(returncode=3)) as run, redirect_stdout(io.StringIO()):
                self.assertEqual(s.main(['--repo',str(repo),'--run']), 3)
            args, kwargs = run.call_args
            nodes = s.selected_nodes()
            self.assertEqual(len(nodes), 15)
            self.assertEqual(args[0][-len(nodes):], nodes)
            self.assertEqual(kwargs['env']['PYTEST_DISABLE_PLUGIN_AUTOLOAD'],'1')
            self.assertNotIn('PYTEST_ADDOPTS', kwargs['env'])
            self.assertNotIn('PYTEST_PLUGINS', kwargs['env'])
            self.assertNotIn('tests/test_java_worker.py', args[0])
            self.assertFalse(any('source_compiler' in node for node in nodes))

if __name__ == '__main__':
    unittest.main()

