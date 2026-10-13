"""Production offline unit negative controls, synthetic byte fixtures only."""
import base64,csv,hashlib,io,json,os,stat,struct,tempfile,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
import intel_deps_prepare as p
import intel_deps_stages as s
import intel_binary_evidence as b
class OfflineControls(unittest.TestCase):
 def rows(self,root):
  result=[]
  for x in sorted(root.rglob('*')):
   st=x.lstat();r={'relative_path':x.relative_to(root).as_posix(),'uid':st.st_uid,'inode':st.st_ino,'device':st.st_dev,'mode':oct(stat.S_IMODE(st.st_mode)),'kind':'directory' if x.is_dir() else 'regular'}
   if x.is_file():r.update(bytes=st.st_size,sha256=hashlib.sha256(x.read_bytes()).hexdigest(),nlink=1)
   result.append(r)
  return result
 def test_copy_exact_and_no_clobber(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);src=root/'source';src.mkdir();(src/'a').mkdir();(src/'a/data').write_bytes(b'exact')
   dst=root/'dest';r=p.copy_exact_tree(src,self.rows(src),dst);self.assertEqual(r['regular_bytes'],5);self.assertEqual((dst/'a/data').read_bytes(),b'exact')
   with self.assertRaises(FileExistsError):p.copy_exact_tree(src,self.rows(src),dst)
 def test_copy_hash_and_extra_entry_rejected_before_destination(self):
  for fault in ('hash','extra'):
   with self.subTest(fault=fault),tempfile.TemporaryDirectory() as t:
    root=Path(t);src=root/'source';src.mkdir();(src/'data').write_bytes(b'old');rows=self.rows(src)
    if fault=='hash':(src/'data').write_bytes(b'new')
    else:(src/'extra').write_bytes(b'unreviewed')
    with self.assertRaises(p.Refusal):p.copy_exact_tree(src,rows,root/'dest')
    self.assertFalse((root/'dest').exists())
 def test_copy_intermediate_symlink_refused(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);src=root/'source';src.mkdir();(src/'dir').mkdir();(src/'dir/file').write_bytes(b'fixture');rows=self.rows(src)
   (src/'dir/file').unlink();(src/'dir').rmdir();(root/'empty').mkdir();(src/'dir').symlink_to(root/'empty',target_is_directory=True)
   with self.assertRaises(OSError):p.copy_exact_tree(src,rows,root/'dest')
   self.assertEqual(list((root/'empty').iterdir()),[])
 def wheel(self,root,files,*,bad_record=False):
  name='fixture-1.dist-info/RECORD';lines=[]
  for key,data in files.items():lines.append([key,'sha256='+base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode(),str(len(data))])
  if bad_record:lines[0][1]='sha256=wrong'
  lines.append([name,'','']);out=io.StringIO();csv.writer(out).writerows(lines);files={**files,name:out.getvalue().encode()};w=root/'fixture-1-py3-none-any.whl'
  with zipfile.ZipFile(w,'w') as z:
   for key,data in files.items():z.writestr(key,data)
  return w,{'artifact_id':'fixture','filename':w.name,'version':'1','expected_bytes':w.stat().st_size,'expected_sha256':hashlib.sha256(w.read_bytes()).hexdigest()}
 def test_wheel_record_checked_and_materialized(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);site=root/'site';scripts=root/'scripts';site.mkdir(mode=0o700);scripts.mkdir(mode=0o700)
   w,row=self.wheel(root,{'fixture/pkg.py':b'fixture only'});p.install_wheel_exact(w,row,site,scripts)
   self.assertEqual((site/'fixture/pkg.py').read_bytes(),b'fixture only')
 def test_wheel_record_and_escape_refused(self):
  for fault in ('record','escape','digest'):
   with self.subTest(fault=fault),tempfile.TemporaryDirectory() as t:
    root=Path(t);site=root/'site';scripts=root/'scripts';site.mkdir(mode=0o700);scripts.mkdir(mode=0o700)
    w,row=self.wheel(root,{'../escape' if fault=='escape' else 'fixture/pkg.py':b'fixture'},bad_record=fault=='record')
    if fault=='digest':row['expected_sha256']='0'*64
    with self.assertRaises(p.Refusal):p.install_wheel_exact(w,row,site,scripts)
    self.assertEqual(list(site.iterdir()),[])
 def test_wheel_existing_parent_symlink_never_writes_outside(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);site=root/'site';scripts=root/'scripts';outside=root/'empty';site.mkdir(mode=0o700);scripts.mkdir(mode=0o700);outside.mkdir(mode=0o700);(site/'fixture').symlink_to(outside,target_is_directory=True)
   w,row=self.wheel(root,{'fixture/deep/pkg.py':b'fixture'})
   with self.assertRaises(OSError):p.install_wheel_exact(w,row,site,scripts)
   self.assertEqual(list(outside.iterdir()),[])
 def lock(self,path):
  rows=[{'filename':f'fixture{i}-1.0.0.crate','version':'1.0.0','expected_sha256':f'{i:064x}'} for i in range(32)]
  raw='version=4\n'+''.join(f'[[package]]\nname="fixture{i}"\nversion="1.0.0"\nsource="registry+https://github.com/rust-lang/crates.io-index"\nchecksum="{i:064x}"\n' for i in range(32));path.write_text(raw);return rows
 def test_cargo_exact_closure_and_checksum_negative(self):
  with tempfile.TemporaryDirectory() as t:
   lock=Path(t)/'Cargo.lock';rows=self.lock(lock);s.validate_lock_closure(lock,rows)
   rows[0]['expected_sha256']='f'*64
   with self.assertRaises(p.Refusal):s.validate_lock_closure(lock,rows)
 def test_cargo_non_registry_and_missing_package_refused(self):
  with tempfile.TemporaryDirectory() as t:
   lock=Path(t)/'Cargo.lock';rows=self.lock(lock);lock.write_text(lock.read_text().replace('registry+https://github.com/rust-lang/crates.io-index','git+https://invalid.example/fixture',1))
   with self.assertRaises(p.Refusal):s.validate_lock_closure(lock,rows)
   rows=self.lock(lock)
   with self.assertRaises(p.Refusal):s.validate_lock_closure(lock,rows[:-1])
 def macho(self,cpu=0x1000007,minimum=0xb0000,kind=1):
  return struct.pack('<IiiIIIII',0xfeedfacf,cpu,0,kind,1,24,0,0)+struct.pack('<IIIIII',0x32,24,1,minimum,0x1a0000,0)
 def archive(self,root,data,name='unit.o'):
  w=root/'libfixture.a';header=f'{name+"/":<16}{0:<12}{0:<6}{0:<6}{"100600":<8}{len(data):<10}`\n'.encode();w.write_bytes(b'!<arch>\n'+header+data+(b'\n' if len(data)%2 else b''));return w
 def test_static_archive_actual_parser_object_and_floor(self):
  with tempfile.TemporaryDirectory() as t:
   path=self.archive(Path(t),self.macho());e=b.inspect_static_archive(path);self.assertEqual(e['object_count'],1);self.assertFalse(e['object_members'][0]['executed'])
 def test_static_archive_wrong_arch_floor_and_truncation(self):
  with tempfile.TemporaryDirectory() as t:
   for data in (self.macho(cpu=0x100000c),self.macho(minimum=0xa0000),self.macho()[:-1]):
    with self.subTest(bytes=len(data)),self.assertRaises(p.Refusal):b.inspect_static_archive(self.archive(Path(t),data))
 def test_stage_commands_paths_and_no_system_install(self):
  prefix=Path('/private/tmp/comsol-intel-inputs-r1-fixture');x=s.layout(prefix)
  for argv,cwd in s.commands('rust',prefix):
   self.assertIn('--disable-ldconfig',argv)
   for option in ('prefix','sysconfdir','bindir','libdir','datadir','mandir','docdir'):
    self.assertTrue(next(v for v in argv if v.startswith('--'+option+'=')).split('=',1)[1].startswith(str(x['rust'])))
  configure=s.commands('openssl',prefix)[0][0]
  self.assertTrue(all(v in configure for v in ('no-apps','no-tests','no-shared')))
  self.assertEqual(s.commands('openssl',prefix)[1][0],['/usr/bin/make','-j2','build_libs'])
 def test_environment_target_headers_and_offline_not_arm_runtime_dependency(self):
  prefix=Path('/private/tmp/comsol-intel-inputs-r1-fixture');env=s.env_for(prefix)
  self.assertEqual(env['CARGO_NET_OFFLINE'],'true');self.assertEqual(env.get('HOME'),os.environ.get('HOME'))
  self.assertEqual(env['PYO3_CROSS_LIB_DIR'],str(prefix/'cpython-universal2/lib/python3.12'))
  self.assertEqual(env['OPENSSL_NO_VENDOR'],'1');self.assertEqual(env['OPENSSL_STATIC'],'1')
  self.assertIn('--offline',s.commands('crypto',prefix)[0][0]);self.assertIn('--locked',s.commands('crypto',prefix)[0][0])
 def test_exact_rust_manifest_ids_and_explicit_abi_feature(self):
  manifest=json.loads(s.MANIFEST.read_text())
  expected={(row['artifact_id'],row['filename']) for row in manifest['artifacts'] if row['artifact_id'].startswith('rust-')}
  self.assertEqual(expected,{(artifact,root+'.tar.xz') for artifact,root,component in s.RUST})
  self.assertIn('pyo3/abi3-py39',s.commands('crypto',Path('/private/tmp/comsol-intel-inputs-r1-fixture'))[0][0])
 def test_prior_rust_cannot_pass_without_commands_or_escape_logs(self):
  with tempfile.TemporaryDirectory() as t:
   work=Path(t);(work/'rust').mkdir();receipt={'stage':'rust','status':'PASS','manifest_sha256':s.MANIFEST_SHA,'artifacts':{'rust':[]},'commands':[]}
   (work/'rust.PASS.json').write_text(json.dumps(receipt))
   with self.assertRaises(p.Refusal):s.prior_stage(work,'rust')
   command={'status':'PASS','rc':0,'owned_process_terminal':True,'owned_group_absent':True,'result_name':'../unowned.json'}
   receipt['commands']=[command]*4;(work/'rust.PASS.json').write_text(json.dumps(receipt))
   with self.assertRaises(p.Refusal):s.prior_stage(work,'rust')
 def test_foreign_prefix_refused_before_any_effect(self):
  with tempfile.TemporaryDirectory(prefix='comsol-intel-inputs-r1-fixture-') as t:
   root=Path(t).resolve()
   with patch.object(s.sys,'argv',['intel_deps_stages.py','--prefix',str(root),'--stage','prepare','--execute-stage']),patch.object(s,'run') as run:
    with self.assertRaises(p.Refusal):s.main()
   self.assertFalse(run.called);self.assertEqual(list(root.iterdir()),[])
if __name__=='__main__':unittest.main(verbosity=2)
