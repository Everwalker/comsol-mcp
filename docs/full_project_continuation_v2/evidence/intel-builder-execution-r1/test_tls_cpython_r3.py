import json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import tls_cpython_checkpoint_r3 as checkpoint
import tls_cpython_successor_r3 as unit
from acquire_exact_archives import Refusal
from intel_deps_prepare import exclusive_json

class CPCheckpointR3(unittest.TestCase):
 def test_actual_raw_terminal_linkage_change_rejected(self):
  rows=checkpoint.snapshot_rows();reader=checkpoint.r2_bytes
  def changed(relative,rows):
   raw=reader(relative,rows)
   if relative.startswith('logs/') and relative.endswith('.result.json'):
    body=json.loads(raw);body['owned_group_absent']=False;return json.dumps(body).encode()
   return raw
  with patch.object(checkpoint,'r2_bytes',side_effect=changed):
   with self.assertRaises(Refusal):checkpoint.native_closure(rows)
 def test_actual_completed_r2_checkpoint_readonly(self):
  result=checkpoint.verify_tls_checkpoint();self.assertEqual(len(result['commands']),4);self.assertEqual(result['r2']['tree_records'],14067)
  self.assertEqual({r['name'] for r in result['artifacts']},{'libssl.a','libcrypto.a'});self.assertEqual(len(result['headers']),145)
  self.assertEqual(sum(s['object_count'] for r in result['artifacts'] for s in r['binary']['slices'].values()),2207)
 def test_exact_six_cp_only_commands_and_fresh_namespace(self):
  x=unit.layout();plan,env=unit.command_plan(x);self.assertEqual(x['root'],checkpoint.PREFIX/'intel-tls-cpython-r3');self.assertEqual(len(plan),6)
  self.assertEqual([label for label,argv,cwd in plan],['cp-configure','header-arm64','header-x86_64','cp-build','cp-install','runtime'])
  self.assertFalse(any(argv[0] in ('/usr/bin/perl','/usr/bin/lipo') for label,argv,cwd in plan));self.assertIn('--with-openssl='+str(x['fat']),plan[0][1]);self.assertEqual(unit.UNIT_CAP,1800);self.assertEqual(unit.RESERVE,300)
 def test_actual_case_insensitive_namespace_distinct_exclusive_records(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);labels=[l for l,a,c in unit.command_plan(unit.layout())[0]];names=[unit.stage_done_record_name(l) for l in labels]+['MERGE_OUTPUT_VERIFIED.json','CHECKPOINT_INPUT_COPIED_VERIFIED.json','ARM_RUNTIME_PROFILE.json','ACTUAL_BINARIES_AND_INSTALL.json','RESULT.json','CLOSED.json']
   self.assertEqual(len({n.casefold() for n in names}),len(names));fd=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
   try:
    for name in names:unit.write_json(fd,name,{'synthetic':True})
    for name in names:
     with self.assertRaises(FileExistsError):unit.write_json(fd,name,{'must_not_overwrite':True})
   finally:os.close(fd)
   self.assertEqual(len(list(root.iterdir())),len(names))
 def copied_fixture(self,root):
  x={'root':root,'fat':root/'fat'};(x['fat']/'include').mkdir(parents=True);(x['fat']/'include/header').write_bytes(b'exact synthetic header');(x['fat']/'lib').mkdir();rows=[]
  for name in ('libssl.a','libcrypto.a'):
   path=x['fat']/'lib'/name;checkpoint.copy_blob(path,b'synthetic copied bytes');record=unit.record(path);rows.append({'name':name,'new':record,'original':dict(record)})
  payload={'headers':unit.deps.tree_record(x['fat']/'include'),'fat_copies':rows};path=root/'CHECKPOINT_INPUT_COPIED_VERIFIED.json';exclusive_json(path,payload);return x,dict(payload,accepted_checkpoint_record=unit.record(path))
 def test_copied_checkpoint_samefd_and_tampering_rejected(self):
  with tempfile.TemporaryDirectory() as t:
   x,accepted=self.copied_fixture(Path(t));self.assertTrue(checkpoint.verify_copied_inputs(x,accepted)['copied_two_fat_libraries_and_header_tree_unchanged'])
   (x['fat']/'lib/libssl.a').write_bytes(b'changed copied bytes')
   with self.assertRaises(Refusal):checkpoint.verify_copied_inputs(x,accepted)
  with tempfile.TemporaryDirectory() as t:
   x,accepted=self.copied_fixture(Path(t));path=x['root']/'CHECKPOINT_INPUT_COPIED_VERIFIED.json';path.write_bytes(path.read_bytes().replace(b'sha256',b'sha257'))
   with self.assertRaises(Refusal):checkpoint.verify_copied_inputs(x,accepted)
 def test_copy_blob_never_overwrites_owned_artifact(self):
  with tempfile.TemporaryDirectory() as t:
   path=Path(t)/'artifact';checkpoint.copy_blob(path,b'original')
   with self.assertRaises(FileExistsError):checkpoint.copy_blob(path,b'replacement')
   self.assertEqual(path.read_bytes(),b'original')
 def test_actual_terminal_r2_checkpoint_failure_keeps_preservation_false(self):
  errors=unit.cp.DiagnosticErrors();budget=unit.Budget()
  with patch.object(checkpoint,'preservation_verified_summary',side_effect=Refusal('synthetic r2 snapshot altered')),patch.object(unit.cp,'run') as dispatch:
   status,facts=unit.preservation_outcome(checkpoint.PREFIX/'intel-deps-r2',[],{},'TLS_HASHLIB_SCOPED_ACTUAL_PASS',errors,budget);dispatch.assert_not_called()
  self.assertFalse(facts['old_roots_unchanged']);self.assertIn('r2 snapshot altered',facts['preservation_failure']['error']);self.assertNotIn('PASS',status)

if __name__=='__main__':unittest.main(verbosity=2)
