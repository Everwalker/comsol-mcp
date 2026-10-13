"""Focused direct-host-tool and independent crypto namespace controls."""
import os,tempfile,unittest
from pathlib import Path
import intel_crypto_successor_r1 as c
import intel_deps_stages as d
from acquire_exact_archives import Refusal
class CryptoSuccessor(unittest.TestCase):
 def test_direct_fixed_host_tool_and_fresh_mutable_paths(self):
  prefix=Path('/private/tmp/comsol-intel-inputs-r1-fixture');owned=c.paths(prefix);argv=c.crypto_argv(prefix,owned);env=c.crypto_env(prefix,owned)
  self.assertEqual(argv[0],str(prefix/'intel-deps-r2/host-bin/maturin'));self.assertNotIn('-m',argv);self.assertIn('--offline',argv);self.assertIn('--locked',argv);self.assertIn('pyo3/abi3-py39',argv)
  self.assertEqual(env['CARGO_HOME'],str(owned['cargo_home']));self.assertEqual(env['CARGO_TARGET_DIR'],str(owned['target']));self.assertEqual(env.get('HOME'),os.environ.get('HOME'));self.assertTrue(env['PYTHONPATH'].startswith(str(prefix/'intel-deps-r2/host-site')))
 def test_actual_pinned_host_tool_fat_slices_min_os_read_only(self):
  old=d.EXACT_PREFIX/'intel-deps-r2';prepare=d.prior_stage(old,'prepare');result=c.inspect_host_tool(old/'host-bin/maturin',prepare['artifacts']['scripts'][0])
  self.assertEqual({s['architecture'] for s in result['slices']},{'arm64','x86_64'});self.assertEqual(result['x86_slice_execution'],'NOT_RUN')
 def fixture(self,root):
  old=root/'old';inputs=old/'inputs';source=inputs/'cryptography-sdist-50.0.1/cryptography-50.0.1';source.mkdir(parents=True);(source/'Cargo.lock').write_bytes(b'exact synthetic lock');(source/'src').mkdir();(source/'src/code.py').write_bytes(b'synthetic code')
  prepare={'artifacts':{'inputs':d.tree_record(inputs)}};owned=c.paths(root);owned['root'].mkdir(mode=0o700);return old,source,prepare,owned
 def test_exact_checkpoint_source_copied_only_into_successor(self):
  with tempfile.TemporaryDirectory() as t:
   old,source,prepare,owned=self.fixture(Path(t));before=d.tree_record(old);result=c.copy_crypto_checkpoint(old,owned,prepare)
   self.assertEqual(result['regular_bytes'],len(b'exact synthetic lock')+len(b'synthetic code'));self.assertEqual((owned['source']/'Cargo.lock').read_bytes(),b'exact synthetic lock');self.assertEqual(d.tree_record(old),before)
 def test_changed_checkpoint_source_rejected_before_copy(self):
  with tempfile.TemporaryDirectory() as t:
   old,source,prepare,owned=self.fixture(Path(t));(source/'Cargo.lock').write_bytes(b'changed synthetic lock')
   with self.assertRaises(Refusal):c.copy_crypto_checkpoint(old,owned,prepare)
   self.assertFalse(owned['source'].exists())
 def test_actual_old_terminal_tree_and_static_objects_bind_same_fd_bytes(self):
  old=d.EXACT_PREFIX/'intel-deps-r2';tree=c.verify_old_unit_tree(old);self.assertEqual(tree['tree_records'],13588)
  openssl=d.prior_stage(old,'openssl')
  from intel_binary_evidence import inspect_static_archive
  count=0
  for original in openssl['target_binary_evidence']:
   path=Path(original['path']);relative=path.relative_to(old/'openssl-x86_64').as_posix();expected=next(row for row in openssl['artifacts']['openssl'] if row['relative_path']==relative)
   raw,record=c.checkpoint_bytes(path,expected);self.assertEqual(inspect_static_archive(c.VerifiedFileBytes(path,raw)),original);count+=original['object_count']
  self.assertEqual(count,1107)
if __name__=='__main__':unittest.main(verbosity=2)
