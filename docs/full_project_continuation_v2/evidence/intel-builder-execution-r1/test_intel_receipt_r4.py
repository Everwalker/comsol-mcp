"""Affected deps receipt bound and checkpoint identity; production readers only."""
import json,os,tempfile,unittest,hashlib
from unittest.mock import patch
from pathlib import Path
import intel_deps_stages as s
from acquire_exact_archives import Refusal
class ReceiptCompatibility(unittest.TestCase):
 def test_large_valid_inventory_receipt_within_dedicated_bound(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t)/'receipt.json';data={'synthetic_inventory':'x'*2000000};p.write_text(json.dumps(data))
   self.assertEqual(s.read_dep_stage_record(p),data)
 def test_above_cap_and_symlink_refused(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t)/'large.json'
   with p.open('wb') as f:f.truncate(s.DEP_STAGE_RECEIPT_MAX+1)
   with self.assertRaises(Refusal):s.read_dep_stage_record(p)
   link=Path(t)/'link.json';link.symlink_to(p)
   with self.assertRaises(OSError):s.read_dep_stage_record(link)
 def test_actual_successful_prepare_checkpoint_full_verification(self):
  work=s.EXACT_PREFIX/'intel-deps-r2';result=s.prior_stage(work,'prepare')
  self.assertEqual(result['stage'],'prepare');self.assertEqual(result['status'],'PASS');self.assertEqual(result['commands'],[])
 def test_changed_prepare_identity_refused_before_following_paths(self):
  with tempfile.TemporaryDirectory() as t:
   work=Path(t);(work/'prepare.PASS.json').write_text(json.dumps({'stage':'prepare','status':'PASS','manifest_sha256':s.MANIFEST_SHA}))
   with self.assertRaises(Refusal):s.prior_stage(work,'prepare')
 def expected(self,path):
  raw=path.read_bytes();return {'identity':s.identity(path.stat()),'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
 def test_replaced_path_between_old_check_and_read_never_supplies_wrong_json(self):
  with tempfile.TemporaryDirectory() as t:
   path=Path(t)/'receipt.json';path.write_bytes(b'{"original":1}');expected=self.expected(path);replacement=Path(t)/'replacement.json';replacement.write_bytes(b'{"attacker":2}')
   real_read=s.os.read;swapped=False
   def swapping(fd,count):
    nonlocal swapped
    if not swapped:os.replace(replacement,path);swapped=True
    return real_read(fd,count)
   with patch.object(s.os,'read',swapping),self.assertRaises(Refusal):s.read_dep_stage_record(path,expected)
   self.assertTrue(swapped)
 def test_same_inode_content_mutation_rejected_by_same_raw_sha(self):
  with tempfile.TemporaryDirectory() as t:
   path=Path(t)/'receipt.json';path.write_bytes(b'{"original":1}');expected=self.expected(path);real_read=s.os.read;changed=False
   def mutating(fd,count):
    nonlocal changed
    if not changed:path.write_bytes(b'{"attacker":2}');changed=True
    return real_read(fd,count)
   with patch.object(s.os,'read',mutating),self.assertRaises(Refusal):s.read_dep_stage_record(path,expected)
 def test_replace_at_json_decode_uses_only_original_approved_raw(self):
  with tempfile.TemporaryDirectory() as t:
   path=Path(t)/'receipt.json';path.write_bytes(b'{"original":1}');expected=self.expected(path);replacement=Path(t)/'replacement.json';replacement.write_bytes(b'{"attacker":2}');loads=s.json.loads;seen=[]
   def decode(raw):
    seen.append(raw);os.replace(replacement,path);return loads(raw)
   with patch.object(s.json,'loads',decode):result=s.read_dep_stage_record(path,expected)
   self.assertEqual(result,{'original':1});self.assertEqual(seen,[b'{"original":1}']);self.assertEqual(path.read_bytes(),b'{"attacker":2}')
if __name__=='__main__':unittest.main(verbosity=2)
