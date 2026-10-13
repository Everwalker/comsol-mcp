"""Source-only prepared exact32 once wrapper; no spawn until fixed Root binding exists."""
import argparse,hashlib,json,os,sys,time
from pathlib import Path
D=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2/local-preservation/w26-exact32-wrapper-r3')
A=D.parent/'w26-helper-successor-r2'
E=D.parents[1]/'repository/docs/full_project_continuation_v2/evidence/intel-builder-execution-r1'
sys.path.insert(0,str(A));from strict_derived import read_regular
METADATA_SHA='e4187e96a1df9b385054f7bf2d35978cfa0bf81aa088fba271322bcfadbdca6c'
W=Path('/private/var/folders/4d/6pphmzkx5szf8dyqx7zhqh1r0000gn/T/comsol-w26-exact32-execution-r3-p09omcx9')
ROOT_BINDING=E/'ROOT_W26_EXACT32_ONCE_DISPATCH_BINDING_R3.json'
ROOT_REVIEW=E/'ROOT_W26_EXACT32_SOURCE_REVIEW_R3.json'
CAP=600;RESERVE=15

def checked(row):
 raw,now=read_regular(Path(row['path']),1073741824)
 if now!=row:raise RuntimeError('fixed original source/input changed '+row['path'])
 return raw

def load_sources():
 raw,rec=read_regular(W/'METADATA.json')
 if rec['sha256']!=METADATA_SHA:raise RuntimeError('wrapper metadata changed')
 m=json.loads(raw)
 for row in m['source_refs']:checked(row)
 sys.path.insert(0,str(E))
 import cpython_universal2_stages as cp
 import intel_deps_stages as deps
 cp.verify_private_path_security(W,directory=True,expected_identity=m['work_identity'])
 if (W/'WRAPPER_ONCE_GUARD.json').exists() or (W/'EXACT32_ONCE_GUARD.json').exists():raise RuntimeError('permanent once guard exists; no replay')
 return m,cp,deps

def verify_old(m,deps):
 before=time.monotonic();records={}
 for row in m['archive_refs']:checked(row)
 if sorted(p.name for p in Path(m['old_archive_root']).iterdir())!=sorted(Path(q['path']).name for q in m['archive_refs']):raise RuntimeError('original43 archive/partial membership changed')
 root=Path(m['runtime_root']['path']);s=root.lstat();expected=m['runtime_root']
 if (s.st_dev,s.st_ino,s.st_uid,s.st_mode&0o777)!=(expected['device'],expected['inode'],expected['uid'],expected['mode']):raise RuntimeError('original runtime root identity changed')
 snap=checked(m['runtime_snapshot'])
 actual=deps.tree_record(root)
 if actual!=json.loads(snap):raise RuntimeError('complete successful runtime tree changed')
 return {'original43_and3failedpartials':True,'successful_runtime_complete_tree':True,'records':len(actual),'elapsed_s':time.monotonic()-before}

def binding(m,sha):
 if len(sha)!=64 or any(c not in '0123456789abcdef' for c in sha):raise RuntimeError('Root binding SHA is not exact64')
 raw,rec=read_regular(ROOT_BINDING)
 if rec['sha256']!=sha:raise RuntimeError('Root once binding differs')
 b=json.loads(raw)
 expected={'schema':'comsol-w26-exact32-once-binding/1','status':'GRANTED_ONCE_DOWNLOAD_ONLY','wrapper_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'metadata_sha256':METADATA_SHA,'actor_sha256':m['actor_sha256'],'manifest_sha256':m['manifest_sha256'],'work_directory':str(W),'work_identity':m['work_identity'],'actor_argv':m['actor_argv'],'budget_seconds':CAP,'cleanup_reserve_seconds':RESERVE,'network_files':32,'network_bytes':21090946}
 for k,v in expected.items():
  if b.get(k)!=v:raise RuntimeError('Root binding field differs: '+k)
 r=read_regular(ROOT_REVIEW)[1]
 if b.get('root_source_review')!={'path':str(ROOT_REVIEW),'sha256':r['sha256'],'bytes':r['bytes']}:raise RuntimeError('Root actual source review reference differs')
 if json.loads(read_regular(ROOT_REVIEW)[0]).get('status')!='PASS_SOURCE_EXACT32_WRAPPER_ONLY':raise RuntimeError('Root exact source review not PASS')
 return {'binding':rec,'review':r}

def main():
 # Conservative pre-Popen monotonic epoch includes preflight; actor inherits it, never resets.
 started=time.monotonic();p=argparse.ArgumentParser();p.add_argument('--binding-sha',required=True);args=p.parse_args()
 m,cp,deps=load_sources();admission=binding(m,args.binding_sha)
 # This native read-only process inventory runs only after exact Root once binding.
 import subprocess
 own=subprocess.run(['/bin/ps','-p',str(os.getpid()),'-o','pid=,ppid=,uid=,lstart=,command='],capture_output=True,text=True,timeout=10,env={'PATH':'/usr/bin:/bin','LC_ALL':'C'})
 fields=own.stdout.strip().split(None,8)
 if own.returncode or own.stderr.strip() or len(fields)!=9 or (int(fields[0]),int(fields[1]),int(fields[2]))!=(os.getpid(),os.getppid(),os.getuid()):raise RuntimeError('actual wrapper birth/UID unavailable; no actor spawn')
 wrapper_birth={'argv':own.args,'rc':own.returncode,'stdout':own.stdout,'stderr':own.stderr,'pid':os.getpid(),'ppid':os.getppid(),'uid':os.getuid(),'birth':' '.join(fields[3:8])}
 old_before=verify_old(m,deps)
 if time.monotonic()-started>=CAP-RESERVE:raise RuntimeError('preflight exhausted inherited batch budget; no spawn')
 logfd=os.open(W/'logs',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW);wfd=os.open(W,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
 errors=cp.DiagnosticErrors();result={'status':'UNKNOWN','old_before':old_before,'old_after':None,'started_monotonic':started,'wrapper_pid':os.getpid(),'uid':os.getuid(),'admission':admission,'argv':m['actor_argv'],'diagnostic_errors':errors,'next_dispatch':0};command=None
 try:
  cp.write_json(wfd,'WRAPPER_BIRTH.json',wrapper_birth)
  cp.write_json(wfd,'WRAPPER_ONCE_GUARD.json',{'pid':os.getpid(),'uid':os.getuid(),'started_monotonic':started,'admission':admission,'actor_argv':m['actor_argv']})
  env=dict(m['environment']);env['COMSOL_W26_BATCH_START_MONOTONIC']=repr(started)
  cp.write_json(wfd,'REQUEST.json',{'actor_argv':m['actor_argv'],'environment':env,'cwd':str(W),'batch_cap_s':CAP,'cleanup_reserve':RESERVE,'before_Popen_monotonic':time.monotonic(),'binding':admission})
  try:
   command=cp.run(logfd,'exact32-download',m['actor_argv'],W,env,timeout=max(0.001,CAP-RESERVE-(time.monotonic()-started)))
   result['status']='COMMAND_PASS_PENDING_ARTIFACTS'
  except cp.StageUnknown as exc:
   command=exc.original_result;result['original_unknown']=exc.original_result;result['first_cause']={'type':type(exc.original_cause).__name__,'detail':str(exc.original_cause)}
   result['same_handle_observation']=cp.observe_unknown(logfd,exc,interval=10);result['status']='UNKNOWN_RETAINED_NO_REPLAY'
  except cp.TerminalCommandFailure as exc:
   command=exc.result;result['status']='TERMINAL_FIRST_FAIL_RETAINED';result['first_cause']={'type':type(exc).__name__,'detail':str(exc)}
  result['command']=command
  if command is not None:
   prefix_checkpoint=W/'PREFIX_CREATED.json'
   if prefix_checkpoint.exists():
    checkpoint=json.loads(read_regular(prefix_checkpoint)[0]);root=Path(checkpoint['prefix'])
    if root.parent!=W.parent or not root.name.startswith('comsol-w26-exact32-inputs-r1-') or checkpoint.get('pid')!=command['pid'] or checkpoint.get('uid')!=os.getuid():raise RuntimeError('actor checkpoint owned prefix differs')
    cp.verify_private_path_security(root,directory=True,expected_identity=checkpoint['security']['identity'])
    result['prefix']=str(root)
    outcome_path=root/'ACTOR_RESULT.json'
    result['actor_result']=json.loads(read_regular(outcome_path)[0]) if outcome_path.exists() else {'status':'NOT_CAPTURED_ACTOR_DIAGNOSTIC_FAILURE'}
    cp.write_json(wfd,'COMPLETE_ACQUISITION_TREE.json',deps.tree_record(root));result['new_tree_scope']='PROVISIONAL_AFTER_SAMEHANDLE_OBSERVATION_NOT_ACCEPTED' if result.get('original_unknown') else 'AFTER_KNOWN_TERMINAL_COMMAND'
    if command.get('status')=='PASS':
     owner=json.loads(read_regular(root/'OWNER.json')[0])
     if owner.get('pid')!=command['pid'] or owner.get('uid')!=os.getuid() or owner.get('manifest_sha256')!=m['manifest_sha256']:raise RuntimeError('actor actual owned prefix differs')
     outcome=result['actor_result']
     if outcome.get('status')!='ACQUIRED_EXACT32' or outcome.get('count_acquired')!=32:raise RuntimeError('not all32 actually acquired')
     rows=json.loads(checked(m['manifest_record']))['artifacts']
     if sorted(q.name for q in (root/'archives').iterdir())!=sorted(q['filename'] for q in rows):raise RuntimeError('new archive membership differs')
     for row in rows:
      _,rec=read_regular(root/'archives'/row['filename'])
      if rec['sha256']!=row['expected_sha256'] or rec['bytes']!=row['expected_bytes']:raise RuntimeError('new32 whole wheel bytes differ')
     result['status']='PASS_DOWNLOAD_ONLY_PENDING_CLOSURE'
   elif command.get('status')=='PASS':raise RuntimeError('successful actor lacks actual prefix checkpoint')
   else:result['prefix']='NOT_CREATED_OR_NOT_CAPTURED_FIRST_FAILURE'

 except BaseException as exc:
  result['status']='FIRST_FAILURE_OR_DIAGNOSTIC_RETAINED';result.setdefault('first_cause',{'type':type(exc).__name__,'detail':str(exc)});cp.fallback(repr(result['first_cause']))
 finally:
  try:result['old_after']=verify_old(m,deps)
  except BaseException as exc:result['old_preservation_failed']=True;errors.append(cp.failure_detail('old_tree_preservation',exc));cp.fallback(repr(errors[-1]))
  result['old_roots_unchanged']=result['old_after'] is not None and not result.get('old_preservation_failed',False)
  cp.release_log(logfd,errors)
  result['elapsed_including_samehandle_wait_rawflush_oldtree_s']=time.monotonic()-started
  if errors or result['elapsed_including_samehandle_wait_rawflush_oldtree_s']>CAP or not result['old_roots_unchanged']:result['status']='UNKNOWN_CLEANUP_OR_CAP_RETAINED'
  elif result['status']=='PASS_DOWNLOAD_ONLY_PENDING_CLOSURE':result['status']='PASS_EXACT32_PENDING_FINAL_WRAPPER_WAIT'
  result['final_acceptance_requires_actual_wrapper_wait_and_final_stdout_rc']=True
  cp.diagnostic_write(wfd,'TERMINAL.json',result,errors);cp.release_log(wfd,errors)
 final_status='PASS_EXACT32_DOWNLOAD_ONLY' if result['status']=='PASS_EXACT32_PENDING_FINAL_WRAPPER_WAIT' and not errors and time.monotonic()-started<=CAP else result['status']
 cp.diagnostic_print({'status':final_status,'elapsed':time.monotonic()-started,'work':str(W),'diagnostic_errors':errors,'actual_external_wrapper_wait_required':True},errors)
 if errors or time.monotonic()-started>CAP:return 3
 return 0 if final_status=='PASS_EXACT32_DOWNLOAD_ONLY' else 1
if __name__=='__main__':sys.exit(main())
