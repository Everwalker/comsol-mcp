"""Affected deps receipt bound and checkpoint identity; production readers only."""
import json,os,tempfile,unittest
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
if __name__=='__main__':unittest.main(verbosity=2)
