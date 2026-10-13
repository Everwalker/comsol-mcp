import hashlib,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import tls_ssl_backport_r4 as backport
import tls_cpython_checkpoint_r4 as checkpoint
import tls_cpython_successor_r4 as unit
import tls_cert_checks_r4 as certs
from acquire_exact_archives import Refusal

PRIMARY=Path('/private/var/folders/4d/6pphmzkx5szf8dyqx7zhqh1r0000gn/T/comsol-asn1-backport-primary-r1-o16h50eh')
BASE=checkpoint.PREFIX/'sources/cpython-source-3.12.13/Python-3.12.13/Modules/_ssl.c'
class BackportControls(unittest.TestCase):
 def test_actual_four_official_blocks_match_fixedtag_and_exactbase(self):
  raw=BASE.read_bytes();after,receipt=backport.transform(raw);upstream=(PRIMARY/'cpython_v3_13_16_ssl.c').read_bytes();self.assertEqual(hashlib.sha256(after).hexdigest(),backport.AFTER_SHA)
  self.assertEqual(len(backport.REPLACEMENTS),4);self.assertEqual(receipt['deleted_direct_field_lines'],8);self.assertEqual(receipt['added_accessor_lines'],10)
  restored=after
  for before,new in backport.REPLACEMENTS:self.assertIn(new,upstream);self.assertEqual(raw.count(before),1);restored=restored.replace(new,before)
  self.assertEqual(restored,raw)
 def test_exact_badbase_and_duplicate_site_refused_no_fuzzy(self):
  raw=BASE.read_bytes()
  with self.assertRaises(Refusal):backport.transform(raw+b'\n')
  bad=raw+backport.REPLACEMENTS[0][0]
  with patch.object(backport,'BASE_SHA',hashlib.sha256(bad).hexdigest()):
   with self.assertRaises(Refusal):backport.transform(bad)
 def test_actual_fresh_fixture_patch_identity_and_original_preserved(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);source=root/'_ssl.c';source.write_bytes(BASE.read_bytes());source.chmod(0o600);st=source.stat();receipt=backport.apply_fresh(source,root)
   self.assertEqual(source.stat().st_ino,st.st_ino);self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),backport.AFTER_SHA);self.assertEqual((root/'SSL_ORIGINAL_BEFORE_BACKPORT.c').read_bytes(),BASE.read_bytes());self.assertFalse('official unmodified' in receipt['software_variant'])
   with self.assertRaises(Refusal):backport.apply_fresh(source,root)
 def test_fresh_patch_symlink_refused(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);source=root/'original';source.write_bytes(BASE.read_bytes());source.chmod(0o600);link=root/'link';link.symlink_to(source)
   with self.assertRaises(OSError):backport.apply_fresh(link,root)
   self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),backport.BASE_SHA)
 def test_certificate_source_expectation_fields_reject_wrong_IP_URI(self):
  for name,fields in certs.EXPECTED.items():self.assertEqual(certs.validate_fields(name,dict(fields)),fields)
  bad=dict(certs.EXPECTED['nullbytecert.pem']);bad['subjectAltName']=bad['subjectAltName'][:-1]+(('IP Address','wrongIPv6'),)
  with self.assertRaises(RuntimeError):certs.validate_fields('nullbytecert.pem',bad)
  bad=dict(certs.EXPECTED['nokia.pem']);bad['caIssuers']=('https://wrong.invalid/',)
  with self.assertRaises(RuntimeError):certs.validate_fields('nokia.pem',bad)
 def test_official_host_fixture_decode_software_only_plan_and_malformed(self):
  import ssl
  folder=checkpoint.PREFIX/'sources/cpython-source-3.12.13/Python-3.12.13/Lib/test/certdata'
  for name in certs.EXPECTED:
   certs.checked_fixture(folder/name,certs.FIXTURE_BYTES[name]);certs.validate_fields(name,ssl._ssl._test_decode_cert(str(folder/name)))
  with self.assertRaises(ssl.SSLError):ssl._ssl._test_decode_cert(str(folder/'badcert.pem'))
  # This host-library planning control is never the fresh patched ARM acceptance.
 def test_actual_original_r3_and_r2_checkpoint_readonly(self):
  result=checkpoint.preservation_verified_summary();self.assertEqual(result['r3']['failed_CP_r3_tree_records'],5407);self.assertTrue(result['r3']['original_r3_unchanged']);self.assertEqual(result['r2']['tree_records'],14067)
 def test_r4_exact_six_commands_runtime_cert_profile_and_namespace(self):
  x=unit.layout();plan,env=unit.command_plan(x);self.assertEqual(x['root'],checkpoint.PREFIX/'intel-tls-cpython-r4');self.assertEqual(len(plan),6);self.assertEqual(unit.UNIT_CAP,1800);self.assertEqual(unit.RESERVE,300)
  self.assertEqual(len({unit.stage_done_record_name(label).casefold() for label,argv,cwd in plan}),6)
  self.assertIn('tls_runtime_profile_r4.py',Path(unit.__file__).read_text());self.assertNotIn('PY_UNSUPPORTED_OPENSSL_BUILD',env)
 def test_actual_terminal_r3_change_cannot_be_promoted(self):
  errors=unit.cp.DiagnosticErrors();budget=unit.Budget()
  with patch.object(checkpoint,'preservation_verified_summary',side_effect=Refusal('synthetic oldr3 changed')),patch.object(unit.cp,'run') as dispatch:
   status,facts=unit.preservation_outcome(checkpoint.PREFIX/'intel-deps-r2',[],{},'TLS_HASHLIB_SCOPED_ACTUAL_PASS',errors,budget);dispatch.assert_not_called()
  self.assertFalse(facts['old_roots_unchanged']);self.assertNotIn('PASS',status)

if __name__=='__main__':unittest.main(verbosity=2)
