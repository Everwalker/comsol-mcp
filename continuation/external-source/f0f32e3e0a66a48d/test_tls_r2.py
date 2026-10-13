import hashlib,io,os,tarfile,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import tls_sources_r2 as source
import tls_successor_r2 as unit
from acquire_exact_archives import Refusal

class SourcesR2(unittest.TestCase):
 def test_actual_fixed_archive_readonly_identity_hash(self):
  fd=os.open(source.PREFIX/'archives'/source.CP_ARCHIVE['filename'],os.O_RDONLY|os.O_NOFOLLOW)
  try:
   self.assertEqual(source.verify_archive_fd(fd,source.CP_ARCHIVE)['sha256'],source.CP_ARCHIVE['sha256']);self.assertEqual(os.lseek(fd,0,os.SEEK_CUR),0)
  finally:os.close(fd)
 def test_same_fd_wrong_hash_and_identity_rejected(self):
  with tempfile.TemporaryDirectory() as t:
   path=Path(t)/'input';path.write_bytes(b'synthetic exact archive bytes');path.chmod(0o600);st=path.stat();expected={'mode':0o600,'identity':{'device':st.st_dev,'inode':st.st_ino},'bytes':st.st_size,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()};fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
   try:
    with self.assertRaises(Refusal):source.verify_archive_fd(fd,dict(expected,sha256='0'*64))
    with self.assertRaises(Refusal):source.verify_archive_fd(fd,dict(expected,identity={'device':st.st_dev,'inode':st.st_ino+1}))
   finally:os.close(fd)
 def tar_fixture(self,path,extra=False,escape=False):
  data=b'exact synthetic CP content'
  with tarfile.open(path,'w') as archive:
   directory=tarfile.TarInfo('Python-3.12.13');directory.type=tarfile.DIRTYPE;directory.mode=0o755;archive.addfile(directory)
   member=tarfile.TarInfo('Python-3.12.13/configure');member.mode=0o755;member.size=len(data);archive.addfile(member,io.BytesIO(data))
   if extra:
    member=tarfile.TarInfo('Python-3.12.13/unreviewed');member.size=1;archive.addfile(member,io.BytesIO(b'x'))
   if escape:
    member=tarfile.TarInfo('Python-3.12.13/link');member.type=tarfile.SYMTYPE;member.linkname='../..';archive.addfile(member)
  return [{'relative_path':'configure','kind':'regular','mode':'0o700','bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}]
 def test_production_safeextract_fresh_content_exact(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);rows=self.tar_fixture(root/'archive');fd=os.open(root/'archive',os.O_RDONLY|os.O_NOFOLLOW)
   try:result=source.extract_pristine(fd,root/'fresh',rows,2,len(b'exact synthetic CP content'))
   finally:os.close(fd)
   self.assertTrue(result['fresh_complete_content_verified']);self.assertEqual(result['archive_programs_executed'],0);self.assertEqual((root/'fresh/Python-3.12.13/configure').read_bytes(),b'exact synthetic CP content')
 def test_production_safeextract_extra_member_and_wrong_content_rejected(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);rows=self.tar_fixture(root/'archive',extra=True);fd=os.open(root/'archive',os.O_RDONLY|os.O_NOFOLLOW)
   try:
    with self.assertRaises(Refusal):source.extract_pristine(fd,root/'fresh',rows)
   finally:os.close(fd)
   rows=self.tar_fixture(root/'archive2');rows[0]['sha256']='0'*64;fd=os.open(root/'archive2',os.O_RDONLY|os.O_NOFOLLOW)
   try:
    with self.assertRaises(Refusal):source.extract_pristine(fd,root/'fresh2',rows)
   finally:os.close(fd)
 def test_production_safeextract_symlink_escape_rejected_before_output(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);rows=self.tar_fixture(root/'archive',escape=True);fd=os.open(root/'archive',os.O_RDONLY|os.O_NOFOLLOW)
   try:
    with self.assertRaises(Refusal):source.extract_pristine(fd,root/'fresh',rows)
   finally:os.close(fd)
   self.assertFalse(list((root/'fresh').iterdir()))
 def test_actual_previous_failed_and_contaminated_trees_readonly(self):
  result=source.verify_prior_tls_and_source();self.assertEqual(result['failed_tls_r1_tree_records'],6352);self.assertEqual(result['old_CP_source_records'],5082);self.assertTrue(result['old_fences_retained'])
 def test_r2_command_plan_uses_fresh_tar_root_no_old_source(self):
  x=unit.layout();plan,env=unit.command_plan(x)
  self.assertEqual(x['root'],source.PREFIX/'intel-tls-r2');self.assertEqual(x['cp_source'],x['root']/'cpython-input/Python-3.12.13');self.assertEqual(len(plan),10)
  cfg=next(argv for label,argv,cwd in plan if label=='cp-configure');self.assertEqual(cfg[1],str(x['cp_source']/'configure'));self.assertIn('--with-openssl-rpath=no',cfg);self.assertEqual(unit.UNIT_CAP,1800);self.assertEqual(unit.RESERVE,300)
 def test_r2_failed_r1_conservation_error_cannot_promote_terminal(self):
  errors=unit.cp.DiagnosticErrors();budget=unit.Budget()
  with patch.object(source,'verify_prior_tls_and_source',side_effect=Refusal('synthetic prior r1 changed')),patch.object(unit.cp,'run') as run:
   status,facts=unit.preservation_outcome(source.PREFIX/'intel-deps-r2',[],{},'TLS_HASHLIB_SCOPED_ACTUAL_PASS',errors,budget)
   run.assert_not_called()
  self.assertFalse(facts['old_roots_unchanged']);self.assertIn('prior r1 changed',facts['preservation_failure']['error']);self.assertNotIn('PASS',status)

if __name__=='__main__':unittest.main(verbosity=2)
