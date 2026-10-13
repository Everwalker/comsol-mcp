"""Private exact crypto provenance adapter. No target code executes."""
import hashlib,io,json,os,stat,tomllib,zipfile
from email import policy
from email.parser import BytesParser
from pathlib import Path
from native_parser import x86_macho, Refusal
NAME='cryptography-50.0.1-cp39-abi3-macosx_11_0_x86_64.whl'
TAG='cp39-abi3-macosx_11_0_x86_64'
DECLARED='https://www.openssl.org/source/openssl-4.0.2.tar.gz'
ACQUIRED='https://github.com/openssl/openssl/releases/download/openssl-4.0.2/openssl-4.0.2.tar.gz'
OPENSSL_SHA='736b467530f916737b7031310ccb21d8218c6229e61e8e160cd1d3458cd543a8'
MANIFEST_SHA='28f53a6f03cc57383a433064bd2fb4459d301bcce46f846962e1af342a6e9c42'

def read_regular(path,limit=134217728):
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
 try:
  s=os.fstat(fd)
  if not stat.S_ISREG(s.st_mode) or s.st_uid!=os.getuid() or s.st_nlink!=1 or s.st_size>limit or s.st_mode&0o022:raise Refusal('untrusted or oversized regular input')
  raw=b''
  while chunk:=os.read(fd,min(1048576,limit+1-len(raw))):
   raw+=chunk
   if len(raw)>limit:raise Refusal('input exceeds bound')
  t=os.fstat(fd);n=os.stat(path,follow_symlinks=False)
  identity=lambda q:(q.st_dev,q.st_ino,q.st_uid,q.st_nlink,q.st_mode,q.st_size,q.st_mtime_ns,q.st_ctime_ns)
  if identity(s)!=identity(t) or identity(s)!=identity(n) or len(raw)!=s.st_size:raise Refusal('input identity changed during same FD read')
  return raw,{'path':str(path),'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'dev':s.st_dev,'inode':s.st_ino,'uid':s.st_uid,'mode':stat.S_IMODE(s.st_mode),'nlink':s.st_nlink}
 finally:os.close(fd)

def verify(provenance_path,wheel_path,source_lock,license_path,target):
 if target!='macos_x86_64' or Path(wheel_path).name!=NAME:raise Refusal('wrong exact target/wheel filename')
 raw,pid=read_regular(provenance_path);p=json.loads(raw)
 wr,wid=read_regular(wheel_path);lr,lid=read_regular(source_lock);lic,licid=read_regular(license_path)
 if p.get('schema')!='comsol-mcp-derived-wheel/2' or p.get('status')!='BUILD_CANDIDATE' or p.get('target')!=target:raise Refusal('wrong provenance schema/status/target')
 if p.get('package')!={'name':'cryptography','version':'50.0.1'}:raise Refusal('wrong exact package')
 wheel={'filename':NAME,'sha256':wid['sha256'],'bytes':wid['bytes'],'tag':TAG}
 if p.get('wheel')!=wheel:raise Refusal('wheel bytes/identity/tag differ')
 lock=tomllib.loads(lr.decode());rows=[q for q in lock['package'] if q['name']=='cryptography' and q['version']=='50.0.1']
 if len(rows)!=1:raise Refusal('locked package is not unique')
 sdist=rows[0]['sdist'];source={'url':sdist['url'],'sha256':sdist['hash'].removeprefix('sha256:')}
 if p.get('source')!=source or p.get('source_lock')!={'filename':Path(source_lock).name,'sha256':lid['sha256'],'sdist_sha256':source['sha256']}:raise Refusal('source/lock binding differs')
 # These are exact archived public-input identities, not arbitrary URL aliases.
 origins=p.get('source_origin_binding',{})
 binding_raw,binding_record=read_regular(Path(__file__).parent/'ORIGIN_BINDING.json')
 if binding_record['sha256']!='4ae8a27e4c6a1d01f10d82fed44b44b71eb961c87577cdaf55c9efa3d866348f':raise Refusal('origin binding does not match source-frozen SHA')
 binding=json.loads(binding_raw)
 if origins!=binding['origin_identity']:raise Refusal('official origin mapping differs from frozen acquisition')
 for item in binding['verified_inputs']:
  br,bid=read_regular(Path(item['path']))
  if any(bid[k]!=item[k] for k in ('sha256','bytes','dev','inode','uid','mode','nlink')):raise Refusal('frozen acquisition/auth/archive input changed')
 if p.get('native_component')!={'name':'OpenSSL','version':'4.0.2','license':'Apache-2.0','linkage':'static','source_url':ACQUIRED,'source_sha256':OPENSSL_SHA,'license_file':{'filename':Path(license_path).name,'sha256':licid['sha256'],'bytes':licid['bytes']}}:raise Refusal('crypto OpenSSL4 source/license differs')
 with zipfile.ZipFile(io.BytesIO(wr)) as z:
  infos=z.infolist();names=[q.filename for q in infos]
  if len(names)!=len(set(names)) or any(n.startswith('/') or '\\' in n or any(c in ('','..','.') for c in n.rstrip('/').split('/')) for n in names):raise Refusal('unsafe or duplicate wheel members')
  if any(q.file_size>134217728 for q in infos) or sum(q.file_size for q in infos)>268435456:raise Refusal('expanded wheel exceeds bound')
  if [n for n in names if n.endswith(('.so','.dylib'))]!=['cryptography/hazmat/bindings/_rust.abi3.so']:raise Refusal('native closure differs')
  h=BytesParser(policy=policy.default).parsebytes(z.read('cryptography-50.0.1.dist-info/WHEEL'))
  if h.defects or h.get_all('Tag',[])!=[TAG] or h.get_payload().strip():raise Refusal('WHEEL requires one exact consistent Tag')
  m=BytesParser(policy=policy.default).parsebytes(z.read('cryptography-50.0.1.dist-info/METADATA'))
  if m.defects or m.get_all('Name',[])!=['cryptography'] or m.get_all('Version',[])!=['50.0.1']:raise Refusal('distribution identity differs')
  native=x86_macho(z.read('cryptography/hazmat/bindings/_rust.abi3.so'),6)
 audit={'architecture':'x86_64','minimum_os':'11.0','openssl_dynamic_dependency':False,'temporary_dylib_dependencies':[],'dynamic_libraries':native['linked_dylibs'],'extension_sha256':native['sha256']}
 if not native['linked_dylibs'] or set(native['linked_dylibs'])-{'/usr/lib/libSystem.B.dylib','/usr/lib/libiconv.2.dylib'} or p.get('native_audit')!=audit:raise Refusal('native actual bytes/audit differ')
 if p.get('uv_lock_modified') is not False or p.get('upstream_source_patched') is not False:raise Refusal('source/lock modification is not allowed')
 for item in binding['verified_inputs']:
  _,last=read_regular(Path(item['path']))
  if any(last[k]!=item[k] for k in ('sha256','bytes','dev','inode','uid','mode','nlink')):raise Refusal('first accepted acquisition input drifted')
 # All first accepted regular bytes stay pinned through complete verification.
 for path,first in [(provenance_path,pid),(wheel_path,wid),(source_lock,lid),(license_path,licid)]:
  _,last=read_regular(path)
  if last!=first:raise Refusal('first accepted input drifted')
 return {'provenance':p,'provenance_sha256':pid['sha256'],'provenance_filename':Path(provenance_path).name,'wheel':wheel,'native_component':p['native_component'],'license_path':Path(license_path),'source_lock_sha256':lid['sha256'],'actual_native':native,'product_minimum_os':'12.0','Intel_runtime':'NOT_RUN'}
