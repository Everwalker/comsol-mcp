"""Run only by the new ARM-selected universal2 CPython; no Intel execution."""
import hashlib,importlib,json,os,platform,ssl,sys,_ssl,_hashlib
from pathlib import Path
if platform.machine()!='arm64' or sys.version_info[:3]!=(3,12,13):raise RuntimeError('exact native ARM CPython required')
expected_prefix=Path(sys.argv[1]).parent/'cpython'
if Path(sys.prefix)!=expected_prefix or Path(sys.executable)!=expected_prefix/'bin/python3.12':raise RuntimeError('actual interpreter is outside fresh fixed prefix')
for module in (_ssl,_hashlib,ssl,hashlib):
 if not Path(module.__file__).resolve().is_relative_to(expected_prefix):raise RuntimeError('required runtime module escaped fresh prefix')
if ssl.OPENSSL_VERSION_INFO[:3]!=(3,5,9):raise RuntimeError('actual SSL provider is not fixed OpenSSL3.5.9')
assert hashlib.sha256(b'abc').hexdigest()=='ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad'
assert _hashlib.openssl_sha256(b'abc').hexdigest()==hashlib.sha256(b'abc').hexdigest()
assert ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT).minimum_version>=ssl.TLSVersion.TLSv1_2
modules={}
for name in ('zlib','sqlite3','ctypes','venv','_lzma','_gdbm','_tkinter'):
 try:
  module=importlib.import_module(name);modules[name]={'status':'IMPORTED','file':getattr(module,'__file__',None)}
 except Exception as exc:modules[name]={'status':'IMPORT_FAILED','type':type(exc).__name__,'cause':str(exc)}
for name in ('zlib','sqlite3','ctypes','venv'):
 if modules[name]['status']!='IMPORTED':raise RuntimeError('existing product runtime capability regressed: '+name)
import importlib.util
spec=importlib.util.spec_from_file_location('task_cert_checks',Path(__file__).with_name('tls_cert_checks_r4.py'))
cert_checks=importlib.util.module_from_spec(spec);spec.loader.exec_module(cert_checks)
cert_result=cert_checks.runtime_cert_cases(ssl,Path(sys.argv[1]).parent)
original_source=Path(sys.argv[1]).parent/'cpython-input/Python-3.12.13/Modules/_ssl.c'
original_sha='19beed620c8b9eaec82e21235cf83f505e279ad8ede63652616d3fedea4a1710'
if hashlib.sha256(original_source.read_bytes()).hexdigest()!=original_sha:raise RuntimeError('official unmodified CP SSL source differs')
spec=importlib.util.spec_from_file_location('fixed_memorybio',Path(__file__).with_name('openssl359_memorybio_r1.py'))
transport=importlib.util.module_from_spec(spec);spec.loader.exec_module(transport)
handshakes=transport.actual_memorybio_cases(ssl,Path(sys.argv[1]).parent/'runtime-certfixtures/keycert.pem')
if ssl.HAS_SSLv3 or not ssl.HAS_TLSv1_2 or not ssl.HAS_TLSv1_3:raise RuntimeError('normal OpenSSL3.5.9 protocol capability differs')
result={'software_variant':'CPython3.12.13-official+OpenSSL3.5.9-official','source_ssl_sha256':original_sha,'official_unmodified_CPython':True,'actual_certificate_cases':cert_result,'actual_MemoryBIO':handshakes,'actual_machine':platform.machine(),'python':sys.version,'executable':sys.executable,'provider':ssl.OPENSSL_VERSION,'provider_info':ssl.OPENSSL_VERSION_INFO,'_ssl':_ssl.__file__,'_hashlib':_hashlib.__file__,'ssl_context_created':True,'sha256_known_vector':True,'modules':modules,'_lzma_requirement':'REQUIRED_PRODUCT_TAR_XZ_CAPABILITY','gdbm_tk_requirement':'UNRESOLVED_NO_SCOPE_EXCLUSION','network_connections':0,'x86_runtime':'NOT_RUN'}
print(json.dumps(result,sort_keys=True))
fd=os.open(sys.argv[1],os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
try:
 view=memoryview((json.dumps(result,sort_keys=True)+'\n').encode())
 while view:view=view[os.write(fd,view):]
 os.fsync(fd)
finally:os.close(fd)
