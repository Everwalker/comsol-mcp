"""Run only by the new ARM-selected universal2 CPython; no Intel execution."""
import hashlib,importlib,json,os,platform,ssl,sys,_ssl,_hashlib
from pathlib import Path
import importlib.util

INSTALLED=Path('/private/var/folders/4d/6pphmzkx5szf8dyqx7zhqh1r0000gn/T/comsol-intel-inputs-r1-obneuyy4/intel-liblzma-r1')
OUTPUT_ROOT=INSTALLED
def validate_provider(info,number,text):
 if tuple(info)!=(3,5,0,9,0) or number!=0x30500090 or text!='OpenSSL 3.5.9 29 Sep 2026':raise RuntimeError('exact actual OpenSSL3.5.9 full tuple/number/string differs: '+json.dumps({'provider_info':info,'provider_number':number,'provider':text},sort_keys=True))
def validate_identity(prefix,executable):
 if Path(prefix)!=INSTALLED/'cpython' or Path(executable)!=INSTALLED/'cpython/bin/python3.12':raise RuntimeError('fixed installed R4 interpreter differs')
def main(output):
 if Path(output)!=OUTPUT_ROOT/'ARM_RUNTIME_PROFILE.json':raise RuntimeError('fixed fresh runtime output differs')
 if platform.machine()!='arm64' or sys.version_info[:3]!=(3,12,13):raise RuntimeError('exact native ARM CPython required')
 expected_prefix=INSTALLED/'cpython'
 validate_identity(sys.prefix,sys.executable)
 if Path(sys.prefix)!=expected_prefix or Path(sys.executable)!=expected_prefix/'bin/python3.12':raise RuntimeError('actual interpreter is outside fresh fixed prefix')
 for module in (_ssl,_hashlib,ssl,hashlib):
  if not Path(module.__file__).resolve().is_relative_to(expected_prefix):raise RuntimeError('required runtime module escaped fresh prefix')
 info,number,provider=ssl.OPENSSL_VERSION_INFO,ssl.OPENSSL_VERSION_NUMBER,ssl.OPENSSL_VERSION
 print(json.dumps({'ACTUAL_PROVIDER_FIRST_READ':{'provider_info':info,'provider_number':number,'provider':provider}},sort_keys=True),flush=True)
 validate_provider(info,number,provider)
 assert hashlib.sha256(b'abc').hexdigest()=='ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad'
 assert _hashlib.openssl_sha256(b'abc').hexdigest()==hashlib.sha256(b'abc').hexdigest()
 assert ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT).minimum_version>=ssl.TLSVersion.TLSv1_2
 modules={}
 for name in ('zlib','sqlite3','ctypes','venv','_lzma','_gdbm','_tkinter'):
  try:
   module=importlib.import_module(name);modules[name]={'status':'IMPORTED','file':getattr(module,'__file__',None)}
  except Exception as exc:modules[name]={'status':'IMPORT_FAILED','type':type(exc).__name__,'cause':str(exc)}
 for name in ('zlib','sqlite3','ctypes','venv','_lzma'):
  if modules[name]['status']!='IMPORTED':raise RuntimeError('existing product runtime capability regressed: '+name)
 spec=importlib.util.spec_from_file_location('task_cert_checks',Path(__file__).with_name('liblzma_cert_checks_r1.py'))
 cert_checks=importlib.util.module_from_spec(spec);spec.loader.exec_module(cert_checks)
 cert_result=cert_checks.runtime_cert_cases(ssl,INSTALLED)
 original_source=INSTALLED/'cpython-input/Python-3.12.13/Modules/_ssl.c'
 original_sha='19beed620c8b9eaec82e21235cf83f505e279ad8ede63652616d3fedea4a1710'
 if hashlib.sha256(original_source.read_bytes()).hexdigest()!=original_sha:raise RuntimeError('official unmodified CP SSL source differs')
 spec=importlib.util.spec_from_file_location('fixed_memorybio',Path(__file__).with_name('openssl359_memorybio_r1.py'))
 transport=importlib.util.module_from_spec(spec);spec.loader.exec_module(transport)
 handshakes=transport.actual_memorybio_cases(ssl,INSTALLED/'runtime-certfixtures/keycert.pem')
 if ssl.HAS_SSLv3 or not ssl.HAS_TLSv1_2 or not ssl.HAS_TLSv1_3:raise RuntimeError('normal OpenSSL3.5.9 protocol capability differs')
 import io,lzma,tarfile
 payload=b'comsol-public-tar-xz-product-fixture'
 compressed=lzma.compress(payload,format=lzma.FORMAT_XZ)
 if lzma.decompress(compressed,format=lzma.FORMAT_XZ)!=payload:raise RuntimeError('required real LZMA roundtrip differs')
 archive=io.BytesIO()
 with tarfile.open(fileobj=archive,mode='w:xz') as tar:
  entry=tarfile.TarInfo('public-product-fixture.txt');entry.size=len(payload);tar.addfile(entry,io.BytesIO(payload))
 archive.seek(0)
 with tarfile.open(fileobj=archive,mode='r:xz') as tar:
  if tar.getnames()!=['public-product-fixture.txt'] or tar.extractfile('public-product-fixture.txt').read()!=payload:raise RuntimeError('required real tar.xz roundtrip differs')
 result={'actual_lzma_roundtrip':True,'actual_tar_xz_product_roundtrip':True,'_lzma':importlib.import_module('_lzma').__file__,'software_variant':'CPython3.12.13-official+OpenSSL3.5.9-official+liblzma5.8.4-official','source_ssl_sha256':original_sha,'official_unmodified_CPython':True,'actual_certificate_cases':cert_result,'actual_MemoryBIO':handshakes,'actual_machine':platform.machine(),'python':sys.version,'executable':sys.executable,'provider':provider,'provider_info':info,'provider_number':number,'_ssl':_ssl.__file__,'_hashlib':_hashlib.__file__,'ssl_context_created':True,'sha256_known_vector':True,'modules':modules,'_lzma_requirement':'REQUIRED_PRODUCT_TAR_XZ_CAPABILITY','gdbm_tk_requirement':'UNRESOLVED_NO_SCOPE_EXCLUSION','network_connections':0,'x86_runtime':'NOT_RUN'}
 print(json.dumps(result,sort_keys=True))
 fd=os.open(output,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 try:
  view=memoryview((json.dumps(result,sort_keys=True)+'\n').encode())
  while view:view=view[os.write(fd,view):]
  os.fsync(fd)
 finally:os.close(fd)

if __name__=='__main__':main(sys.argv[1])
