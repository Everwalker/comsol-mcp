"""Run only by the new ARM-selected universal2 CPython; no Intel execution."""
import hashlib,importlib,json,os,platform,ssl,sys,_ssl,_hashlib
from pathlib import Path
if platform.machine()!='arm64' or sys.version_info[:3]!=(3,12,13):raise RuntimeError('exact native ARM CPython required')
if ssl.OPENSSL_VERSION_INFO[:3]!=(4,0,2):raise RuntimeError('actual SSL provider is not fixed OpenSSL4.0.2')
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
result={'actual_machine':platform.machine(),'python':sys.version,'executable':sys.executable,'provider':ssl.OPENSSL_VERSION,'provider_info':ssl.OPENSSL_VERSION_INFO,'_ssl':_ssl.__file__,'_hashlib':_hashlib.__file__,'ssl_context_created':True,'sha256_known_vector':True,'modules':modules,'_lzma_requirement':'REQUIRED_PRODUCT_TAR_XZ_CAPABILITY','gdbm_tk_requirement':'UNRESOLVED_NO_SCOPE_EXCLUSION','network_connections':0,'x86_runtime':'NOT_RUN'}
print(json.dumps(result,sort_keys=True))
fd=os.open(sys.argv[1],os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
try:
 view=memoryview((json.dumps(result,sort_keys=True)+'\n').encode())
 while view:view=view[os.write(fd,view):]
 os.fsync(fd)
finally:os.close(fd)
