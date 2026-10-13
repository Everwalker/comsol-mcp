"""Focused R2 production decoder tests, synthetic wheels/Mach-O only."""
import struct,tempfile,unittest,zipfile
from pathlib import Path
import intel_binary_evidence as b
from acquire_exact_archives import Refusal
class DecoderIdentity(unittest.TestCase):
 def macho(self,path=None):
  commands=struct.pack('<IIIIII',0x32,24,1,0xb0000,0x1a0000,0)
  count=1
  if path is not None:
   name=path.encode()+b'\0';size=(24+len(name)+7)//8*8
   commands+=struct.pack('<IIIIII',0xc,size,24,0,0,0)+name+bytes(size-24-len(name));count+=1
  return struct.pack('<IiiIIIII',0xfeedfacf,0x1000007,0,6,count,len(commands),0,0)+commands
 def wheel(self,root,metadata,name=None):
  path=root/(name or b.TARGET_WHEEL_NAME)
  with zipfile.ZipFile(path,'w') as z:
   z.writestr('cryptography/hazmat/bindings/_rust.abi3.so',self.macho('/usr/lib/libSystem.B.dylib'))
   z.writestr('cryptography-50.0.1.dist-info/WHEEL',metadata)
  return path
 def test_exact_filename_unique_tag_and_raw_system_path(self):
  with tempfile.TemporaryDirectory() as t:
   meta='Wheel-Version: 1.0\nGenerator: synthetic fixture\nTag: '+b.TARGET_WHEEL_TAG+'\n\n'
   result=b.inspect_crypto_wheel(self.wheel(Path(t),meta))
   self.assertEqual(result['wheel_metadata'],meta);self.assertEqual(result['extension']['linked_dylibs'],['/usr/lib/libSystem.B.dylib']);self.assertEqual(result['Intel_runtime'],'NOT_RUN')
 def test_wrong_filename_abi_platform_and_version_refused(self):
  with tempfile.TemporaryDirectory() as t:
   for name in ('cryptography-50.0.1-cp312-abi3-macosx_11_0_x86_64.whl','cryptography-50.0.1-cp39-abi3-macosx_11_0_arm64.whl','cryptography-50.0.2-cp39-abi3-macosx_11_0_x86_64.whl'):
    with self.subTest(name=name),self.assertRaises(Refusal):b.inspect_crypto_wheel(self.wheel(Path(t),'Tag: '+b.TARGET_WHEEL_TAG+'\n\n',name))
 def test_conflicting_duplicate_body_and_wrong_tag_refused(self):
  with tempfile.TemporaryDirectory() as t:
   good='Tag: '+b.TARGET_WHEEL_TAG+'\n'
   for metadata in (good+good,good+'tag: cp312-abi3-macosx_11_0_x86_64\n','Tag: cp312-abi3-macosx_11_0_x86_64\n','Wheel-Version: 1.0\n\n'+good,good+'\nforged body text', 'Unrelated: '+good):
    with self.subTest(metadata=metadata),self.assertRaises(Refusal):b.inspect_crypto_wheel(self.wheel(Path(t),metadata))
 def test_canonical_system_dylib_and_framework_paths_preserved(self):
  for path in ('/usr/lib/libSystem.B.dylib','/usr/lib/libc++.1.dylib','/usr/lib/swift/libswiftCore.dylib','/System/Library/Frameworks/CoreFoundation.framework/Versions/A/CoreFoundation','/System/Library/PrivateFrameworks/Fixture.framework/Fixture'):
   with self.subTest(path=path):
    self.assertEqual(b.canonical_system_dylib(path),path);self.assertEqual(b.x86_macho(self.macho(path),6)['linked_dylibs'],[path])
 def test_actual_load_command_noncanonical_or_external_path_refused(self):
  for path in ('/usr/lib/../../opt/homebrew/lib/libcrypto.dylib','/System/Library/../Frameworks/F.framework/F','/usr/lib//libSystem.B.dylib','/usr/lib/./libSystem.B.dylib','//usr/lib/libSystem.B.dylib','/usr/lib/libSystem.B.dylib/','/usr/lib/not-a-library.txt','/System/Library/Frameworks/F.framework/Versions/A/notF','/opt/homebrew/lib/libcrypto.dylib','@rpath/libcrypto.dylib','/private/tmp/libcrypto.dylib','/usr/lib/libSystem.B.dylib\x00not-padding'):
   with self.subTest(path=path),self.assertRaises(Refusal):b.x86_macho(self.macho(path),6)
 def test_direct_nul_backslash_and_control_component_refused(self):
  for path in ('/usr/lib/libSystem.B.dylib\x00','/usr/lib/fixt\\ure.dylib','/usr/lib/fixture\n.dylib'):
   with self.subTest(path=path),self.assertRaises(Refusal):b.canonical_system_dylib(path)
if __name__=='__main__':unittest.main(verbosity=2)
