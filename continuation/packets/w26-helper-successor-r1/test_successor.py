import copy,hashlib,io,json,struct,tempfile,unittest,zipfile
from pathlib import Path
import full_project_release as release
import strict_derived as strict
D=Path(__file__).parent
ROOT=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2')
P=Path('/private/var/folders/4d/6pphmzkx5szf8dyqx7zhqh1r0000gn/T/comsol-intel-inputs-r1-obneuyy4')
W=P/'intel-crypto-r3/wheelhouse'/strict.NAME
LOCK=ROOT/'repository/uv.lock';LIC=P/'sources/openssl-source-4.0.2/openssl-4.0.2/LICENSE.txt'
BASE=json.loads((D/'ACTUAL_CRYPTO_PROVENANCE_R1.json').read_text())
class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(prefix='comsol-w26-derived-controls-');self.r=Path(self.tmp.name);self.p=copy.deepcopy(BASE);self.w=self.r/strict.NAME
  self.w.write_bytes(strict.read_regular(W)[0]);self.lock=self.r/'uv.lock';self.lock.write_bytes(strict.read_regular(LOCK)[0]);self.lic=self.r/'LICENSE.txt';self.lic.write_bytes(strict.read_regular(LIC)[0])
 def tearDown(self):self.tmp.cleanup()
 def call(self):
  self.p['wheel']['sha256']=hashlib.sha256(self.w.read_bytes()).hexdigest();self.p['wheel']['bytes']=self.w.stat().st_size
  q=self.r/'provenance.json';q.write_text(json.dumps(self.p));return release.verify_derived_wheel(q,self.w,self.lock,self.lic,'macos_x86_64')
 def reject(self):
  with self.assertRaises(release.ReleaseError):self.call()
 def zipchange(self,member,fn):
  out=io.BytesIO()
  with zipfile.ZipFile(self.w) as z,zipfile.ZipFile(out,'w') as dest:
   for n in z.namelist():dest.writestr(n,fn(z.read(n)) if n==member else z.read(n))
  self.w.write_bytes(out.getvalue())
 def test_actual_positive(self):
  a=self.call();self.assertEqual(a['actual_native']['minimum_os'],[11,0,0]);self.assertEqual(a['product_minimum_os'],'12.0');self.assertEqual(release.TARGETS['macos_x86_64']['minimum_platform'],(12,0))
 def test_actual_overlay_without_lock_write(self):
  q=self.r/'provenance.json';q.write_text(json.dumps(self.p))
  req=ROOT/'repository/docs/full_project_continuation_v2/evidence/release_data_lifecycle/release-delivery-plan-r1/requirements-macos-x86_64-cp312.txt'
  before=hashlib.sha256(LOCK.read_bytes()).hexdigest()
  out=self.r/'derived.txt'
  release.compose_derived_wheel_lock(req,self.w,self.lock,q,self.lic,'macos_x86_64',out)
  self.assertIn(self.p['wheel']['sha256'],out.read_text());self.assertEqual(hashlib.sha256(LOCK.read_bytes()).hexdigest(),before)
 def test_wrong_filename(self):self.w=self.w.rename(self.r/'cryptography-50.0.1-cp312-abi3-macosx_12_0_x86_64.whl');self.reject()
 def test_wrong_internal_abi(self):self.zipchange('cryptography-50.0.1.dist-info/WHEEL',lambda b:b.replace(b'cp39',b'cp312'));self.reject()
 def test_duplicate_internal_tag(self):self.zipchange('cryptography-50.0.1.dist-info/WHEEL',lambda b:b+b'Tag: '+strict.TAG.encode()+b'\n');self.reject()
 def test_conflicting_tag(self):self.zipchange('cryptography-50.0.1.dist-info/WHEEL',lambda b:b+b'Tag: cp312-abi3-macosx_12_0_x86_64\n');self.reject()
 def test_wrong_native_floor(self):
  def change(b):
   b=bytearray(b);count=struct.unpack_from('<I',b,16)[0];o=32
   for _ in range(count):
    cmd,size=struct.unpack_from('<II',b,o)
    if cmd==0x32:struct.pack_into('<I',b,o+12,0xc0000)
    elif cmd==0x24:struct.pack_into('<I',b,o+8,0xc0000)
    o+=size
   return bytes(b)
  self.zipchange('cryptography/hazmat/bindings/_rust.abi3.so',change);self.reject()
 def test_wrong_native_arch(self):self.zipchange('cryptography/hazmat/bindings/_rust.abi3.so',lambda b:b[:4]+struct.pack('<I',0x100000c)+b[8:]);self.reject()
 def test_dylib_traversal(self):self.zipchange('cryptography/hazmat/bindings/_rust.abi3.so',lambda b:b.replace(b'/usr/lib/libiconv.2.dylib',b'/usr/../libiconv.2.dylib'));self.reject()
 def test_declared_alias(self):self.p['source_origin_binding']['declared_url']='https://openssl-library.org/source/openssl-4.0.2.tar.gz';self.reject()
 def test_acquired_alias(self):self.p['source_origin_binding']['acquired_official_url']='https://mirror.invalid/openssl-4.0.2.tar.gz';self.reject()
 def test_wrong_crypto_ssl(self):self.p['native_component']['version']='3.5.9';self.reject()
 def test_wrong_archive_hash(self):self.p['source_origin_binding']['archive_sha256']='0'*64;self.reject()
 def test_wrong_locked_source(self):self.p['source']['sha256']='0'*64;self.reject()
 def test_wrong_license(self):self.lic.write_bytes(b'wrong license');self.reject()
 def test_source_patch_refused(self):self.p['upstream_source_patched']=True;self.reject()
 def test_first_accepted_wheel_drift(self):
  old=strict.x86_macho
  def mutate(raw,kind):
   result=old(raw,kind)
   with self.w.open('r+b') as f:f.seek(-1,2);f.write(b'X')
   return result
  strict.x86_macho=mutate
  try:self.reject()
  finally:strict.x86_macho=old
if __name__=='__main__':unittest.main(verbosity=2)
