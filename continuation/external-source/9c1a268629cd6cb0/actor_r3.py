"""Proposed exact32 download-only entrypoint; dry-run by default, once guard on execution."""
import argparse,hashlib,json,os,signal,sys,tempfile,time
from pathlib import Path
sys.path.insert(0,'/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2/local-preservation/w26-helper-successor-r2')
from strict_derived import read_regular
D=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2/local-preservation/w26-helper-successor-r2')
EXECUTION_DIR=Path('/private/var/folders/4d/6pphmzkx5szf8dyqx7zhqh1r0000gn/T/comsol-w26-exact32-execution-r3-p09omcx9')
E=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2/repository/docs/full_project_continuation_v2/evidence/intel-builder-execution-r1')
ORIGINAL=E/'acquire_exact_archives.py'
ORIGINAL_SHA='dde0f5c827af8e3675f2c6dee6b0792a1ae94e8d7f09be3b6f778857983df683'
MANIFEST_SHA='dbb01ae18db48104c87cafc564999cac058e2af2cdd3f1fae6ac03b495bf5b61'
TOTAL=21090946
CAP=600;RESERVE=15
class BatchDeadlineExceeded(RuntimeError):pass

def source_and_rows():
 raw,record=read_regular(ORIGINAL)
 if record['sha256']!=ORIGINAL_SHA:raise RuntimeError('original reviewed acquisition source changed')
 raw,_=read_regular(D/'EXACT32_DOWNLOAD_MANIFEST.json',2097152)
 if hashlib.sha256(raw).hexdigest()!=MANIFEST_SHA:raise RuntimeError('exact32 manifest bytes changed')
 x=json.loads(raw);rows=x['artifacts']
 if len(rows)!=32 or sum(r['expected_bytes'] for r in rows)!=TOTAL:raise RuntimeError('exact32 count/aggregate differs')
 sys.path.insert(0,str(E))
 import acquire_exact_archives as original
 seen=set()
 for r in rows:
  if r['filename'] in seen or '/' in r['filename'] or r['origin_host']!='files.pythonhosted.org':raise RuntimeError('nonunique/unapproved fixed wheel input')
  seen.add(r['filename']);original.check_url(r['url'],{'files.pythonhosted.org'})
 return original,rows

def main():
 started=time.monotonic()
 a=argparse.ArgumentParser();a.add_argument('--execute-download',action='store_true');a.add_argument('--approved-manifest-sha256');args=a.parse_args()
 if args.execute_download:
  inherited=float(os.environ['COMSOL_W26_BATCH_START_MONOTONIC'])
  if not 0<=time.monotonic()-inherited<CAP-RESERVE:raise RuntimeError('invalid or exhausted inherited outer monotonic budget')
  started=inherited
  prior_alarm_handler=signal.getsignal(signal.SIGALRM)
  signal.signal(signal.SIGALRM,lambda *_: (_ for _ in ()).throw(BatchDeadlineExceeded('inherited outer deadline exhausted')))
  signal.setitimer(signal.ITIMER_REAL,max(0.001,CAP-RESERVE-(time.monotonic()-started)))
 if args.execute_download:
  guard=json.loads(read_regular(EXECUTION_DIR/'WRAPPER_ONCE_GUARD.json')[0])
  if guard.get('pid')!=os.getppid() or guard.get('uid')!=os.getuid() or guard.get('actor_argv',[])[3:]!=sys.argv:raise RuntimeError('actor is not the exact created child of the admitted wrapper')
  bound=guard['admission']['binding'];fixed=E/'ROOT_W26_EXACT32_ONCE_DISPATCH_BINDING_R3.json'
  if bound.get('path')!=str(fixed) or read_regular(fixed)[1]!=bound:raise RuntimeError('actual Root once binding changed or is missing')
  if json.loads(read_regular(fixed)[0]).get('status')!='GRANTED_ONCE_DOWNLOAD_ONLY':raise RuntimeError('Root once binding does not admit download')
 original,rows=source_and_rows()
 if not args.execute_download:
  print(json.dumps({'status':'DRY_RUN_EXACT32_ONLY','count':32,'bytes':TOTAL,'manifest_sha256':MANIFEST_SHA,'network':0}));return
 if args.approved_manifest_sha256!=MANIFEST_SHA:raise RuntimeError('wrong approved exact manifest SHA')
 parent=Path(tempfile.gettempdir()).resolve();parent_security=original.verify_temp_parent(parent)
 # Permanent source-candidate guard is created before the new input prefix; no replays.
 dfd=os.open(EXECUTION_DIR,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
 original.write_json(dfd,'EXACT32_ONCE_GUARD.json',{'argv':sys.argv,'pid':os.getpid(),'manifest_sha256':MANIFEST_SHA,'started_unix':time.time()});os.close(dfd)
 prefix=Path(tempfile.mkdtemp(prefix='comsol-w26-exact32-inputs-r1-',dir=parent));security=original.verify_private_path_security(prefix,directory=True)
 original.verify_private_path_security(EXECUTION_DIR,directory=True,expected_identity={'device': 16777231, 'inode': 28704049})
 checkpoint_fd=os.open(EXECUTION_DIR,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
 try:original.write_json(checkpoint_fd,'PREFIX_CREATED.json',{'prefix':str(prefix),'pid':os.getpid(),'uid':os.getuid(),'security':security})
 finally:os.close(checkpoint_fd)
 pfd=os.open(prefix,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW);af=ef=None;old=prior_alarm_handler;failure=None;result={'status':'UNKNOWN','count_acquired':0,'install_build':0,'prefix':str(prefix)}
 def alarm(*_):raise BatchDeadlineExceeded('600s unit cap cleanup reserve reached; no retry')
 try:
  original.write_json(pfd,'OWNER.json',{'pid':os.getpid(),'uid':os.getuid(),'argv':sys.argv,'security':security,'parent_security':parent_security,'manifest_sha256':MANIFEST_SHA,'original_source_sha256':ORIGINAL_SHA})
  for name in ['archives','evidence']:os.mkdir(name,mode=0o700,dir_fd=pfd)
  af=os.open('archives',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=pfd);ef=os.open('evidence',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=pfd)
  signal.signal(signal.SIGALRM,alarm);signal.setitimer(signal.ITIMER_REAL,max(0.001,CAP-RESERVE-(time.monotonic()-started)))
  print(json.dumps({'status':'STARTED_DOWNLOAD_ONLY','prefix':str(prefix)}),flush=True)
  for row in rows:
   if time.monotonic()-started>=CAP-RESERVE:raise BatchDeadlineExceeded('no dispatch inside cleanup reserve')
   original.verify_private_path_security(prefix,directory=True,expected_identity=security['identity'])
   original.fetch(row,af,ef) # Exact original HTTPS/redirect/hash/size/partial/no-clobber protocol. First exception stops.
   result['count_acquired']+=1;print(json.dumps({'artifact':row['artifact_id'],'status':'ACQUIRED_EXACT'}),flush=True)
  result['status']='ACQUIRED_EXACT32'
 except BaseException as exc:
  failure=exc;result.update(status='FIRST_FAILURE_PRESERVED',cause_type=type(exc).__name__,cause=str(exc))
 finally:
  signal.setitimer(signal.ITIMER_REAL,0);signal.signal(signal.SIGALRM,old)
  result['elapsed_before_final_io']=time.monotonic()-started
  if result['elapsed_before_final_io']>CAP:result['status']='UNKNOWN_CAP'
  result['process_terminal']='WRAPPER_WAIT_REQUIRED';original.write_json(pfd,'ACTOR_RESULT.json',result)
  for fd in [ef,af,pfd]:
   if fd is not None:os.close(fd)
 if failure is not None:raise failure
 print(json.dumps(result),flush=True)
if __name__=='__main__':main()
