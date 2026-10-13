"""Only production metadata/approval gates; never reaches fetch/private-root creation."""
import io,sys,json,unittest,contextlib
import acquire_exact32 as entry
class Controls(unittest.TestCase):
 def test_exact32_metadata(self):
  _,r=entry.source_and_rows();self.assertEqual(len(r),32);self.assertEqual(sum(q['expected_bytes'] for q in r),21090946)
 def test_wrong_admission_stops_before_once_guard(self):
  old=sys.argv;sys.argv=['acquire_exact32.py','--execute-download','--approved-manifest-sha256','0'*64]
  try:
   with self.assertRaisesRegex(RuntimeError,'wrong approved'):entry.main()
   self.assertFalse((entry.D/'EXACT32_ONCE_GUARD.json').exists())
  finally:sys.argv=old
 def test_mutated_manifest_rejected(self):
  old=entry.read_regular
  def substitute(path,*args):
   raw,rec=old(path,*args)
   if path.name=='EXACT32_DOWNLOAD_MANIFEST.json':raw+=b' '
   return raw,rec
  entry.read_regular=substitute
  try:
   with self.assertRaisesRegex(RuntimeError,'manifest bytes changed'):entry.source_and_rows()
  finally:entry.read_regular=old
if __name__=='__main__':unittest.main(verbosity=2)
