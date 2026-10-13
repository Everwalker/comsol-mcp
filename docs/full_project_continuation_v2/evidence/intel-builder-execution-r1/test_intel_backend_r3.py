"""Production backend RECORD-root repair, synthetic vendored wheel bytes only."""
import base64,csv,hashlib,io,json,tempfile,unittest,zipfile
from pathlib import Path
from intel_deps_prepare import install_wheel_exact
from acquire_exact_archives import Refusal
class VendorRecordIdentity(unittest.TestCase):
 def wheel(self,root,*,root_record='fixture-1.dist-info/RECORD',second_root=False,corrupt_nested=False):
  files={'fixture/module.py':b'synthetic package'}
  for i in range(16):files[f'fixture/_vendor/item{i}-1.dist-info/RECORD']=f'ordinary nested RECORD {i}'.encode()
  if second_root:files['other-1.dist-info/RECORD']=b'not an admitted root'
  out=io.StringIO();writer=csv.writer(out)
  for name,data in files.items():
   digest=base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode()
   writer.writerow([name,'sha256='+digest,str(len(data))])
  writer.writerow([root_record,'','']);files[root_record]=out.getvalue().encode()
  if corrupt_nested:files['fixture/_vendor/item0-1.dist-info/RECORD']=b'mutated nested bytes'
  path=root/'fixture-1-py3-none-any.whl'
  with zipfile.ZipFile(path,'w') as z:
   for name,data in files.items():z.writestr(name,data)
  return path,{'artifact_id':'fixture','filename':path.name,'version':'1','expected_bytes':path.stat().st_size,'expected_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
 def test_nested_16_records_are_covered_by_exact_outer_record(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);site=root/'site';scripts=root/'scripts';site.mkdir(mode=0o700);scripts.mkdir(mode=0o700)
   path,row=self.wheel(root);result=install_wheel_exact(path,row,site,scripts)
   self.assertEqual(result['members'],18);self.assertEqual(len(list(site.rglob('RECORD'))),17)
 def test_wrong_root_version_extra_root_and_nested_tampering_refused(self):
  for kwargs in ({'root_record':'fixture-2.dist-info/RECORD'},{'second_root':True},{'corrupt_nested':True}):
   with self.subTest(kwargs=kwargs),tempfile.TemporaryDirectory() as t:
    root=Path(t);site=root/'site';scripts=root/'scripts';site.mkdir(mode=0o700);scripts.mkdir(mode=0o700);path,row=self.wheel(root,**kwargs)
    with self.assertRaises(Refusal):install_wheel_exact(path,row,site,scripts)
    self.assertEqual(list(site.iterdir()),[])
 def test_filename_version_mismatch_refused(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);site=root/'site';scripts=root/'scripts';site.mkdir(mode=0o700);scripts.mkdir(mode=0o700);path,row=self.wheel(root);row['version']='2'
   with self.assertRaises(Refusal):install_wheel_exact(path,row,site,scripts)
   self.assertEqual(list(site.iterdir()),[])
 def test_duplicate_top_record_member_refused(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);site=root/'site';scripts=root/'scripts';site.mkdir(mode=0o700);scripts.mkdir(mode=0o700);path,row=self.wheel(root)
   with self.assertWarns(UserWarning):
    with zipfile.ZipFile(path,'a') as z:z.writestr('fixture-1.dist-info/RECORD',b'duplicate')
   row.update(expected_bytes=path.stat().st_size,expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
   with self.assertRaises(Refusal):install_wheel_exact(path,row,site,scripts)
   self.assertEqual(list(site.iterdir()),[])
 def test_missing_top_record_refused(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);site=root/'site';scripts=root/'scripts';site.mkdir(mode=0o700);scripts.mkdir(mode=0o700);path=root/'fixture-1-py3-none-any.whl'
   with zipfile.ZipFile(path,'w') as z:z.writestr('fixture/_vendor/item-1.dist-info/RECORD',b'nested only')
   row={'artifact_id':'fixture','filename':path.name,'version':'1','expected_bytes':path.stat().st_size,'expected_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
   with self.assertRaises(Refusal):install_wheel_exact(path,row,site,scripts)
   self.assertEqual(list(site.iterdir()),[])
if __name__=='__main__':unittest.main(verbosity=2)
