"""Once scoped offline dependency venv unit. Requires actual Root binding; SOURCE preparation alone cannot run."""
import argparse,json,os,socket,sys,time,hashlib
from pathlib import Path
D=Path(__file__).parent;sys.path.insert(0,str(D));from common import raw_file,publish,secure_dir,exact_copy,Refusal,system_binary
META_SHA='d127ca09640f4f4d8af7927bead65e1e44a9a7ca8d9070d21d80c12570000b8e'
E=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2/repository/docs/full_project_continuation_v2/evidence/intel-builder-execution-r1')
BINDING=E/'ROOT_W26_OFFLINE_VENV_ONCE_BINDING_R1.json'
REVIEW=E/'ROOT_W26_OFFLINE_VENV_SOURCE_REVIEW_R1.json'
CAP=1800;RESERVE=300

def inputs(m):
 system_binary(Path('/usr/bin/sandbox-exec'),m['fixed_sandbox_binary'])
 for r in m['source_refs']+m['input_refs']:raw_file(Path(r['path']),expected=r)
 raw_file(Path(m['bootstrap']['path']),expected=m['bootstrap'])

def old_trees(m,deps):
 for r in m['archive_refs']:raw_file(Path(r['path']),expected=r)
 if sorted(p.name for p in Path(m['old_archive_root']).iterdir())!=sorted(Path(r['path']).name for r in m['archive_refs']):raise Refusal('old archive/failedpartial full membership changed')
 results=[]
 for entry in m['old_trees']:
  root=Path(entry['root']);secure_dir(root,entry['identity']);raw,_=raw_file(Path(entry['snapshot']['path']),expected=entry['snapshot'])
  rows=deps.tree_record(root)
  if rows!=json.loads(raw):raise Refusal('original complete tree changed: '+str(root))
  results.append({'root':str(root),'records':len(rows),'complete_current_first_snapshot_same':True})
 return results

def source_gate(binding_sha):
 if sys.platform!='darwin':raise Refusal('only admitted native Darwin profile route supported')
 raw,rec=raw_file(D/'METADATA.json')
 if rec['sha256']!=META_SHA:raise Refusal('fixed metadata changed')
 m=json.loads(raw);secure_dir(D,m['W_identity']);inputs(m)
 br,brec=raw_file(BINDING)
 if brec['sha256']!=binding_sha:raise Refusal('Root once SHA differs')
 b=json.loads(br);required={'schema':'comsol-w26-offline-venv-once/1','status':'GRANTED_ONCE_DEPENDENCY_VENVS_ONLY','metadata_sha256':META_SHA,'controller_sha256':raw_file(Path(__file__))[1]['sha256'],'W':str(D),'W_identity':m['W_identity'],'unit_root':m['context']['unit_root'],'budget_seconds':CAP,'cleanup_reserve':RESERVE,'commands':8,'target_application_build':False}
 for k,v in required.items():
  if b.get(k)!=v:raise Refusal('actual once binding differs '+k)
 rr,rrec=raw_file(REVIEW)
 if b.get('review')!={'path':str(REVIEW),'bytes':rrec['bytes'],'sha256':rrec['sha256']} or json.loads(rr).get('status')!='PASS_SOURCE_OFFLINE_DEPENDENCY_VENVS_ONLY':raise Refusal('actual Root source review not present')
 root=Path(m['context']['unit_root'])
 if root.exists() or (D/'UNIT_ONCE_GUARD.json').exists():raise Refusal('fresh unit/once guard not absent; no replay')
 sys.path.insert(0,str(E));import cpython_universal2_stages as cp;import intel_deps_stages as deps
 cp.verify_private_path_security(root.parent,directory=True,expected_identity=m['unit_parent_identity'])
 return m,cp,deps,{'binding':brec,'review':rrec}

def sandbox_profile(root,W):
 # Official installed SB primary demonstrates deny file-write* and allow subpath.
 quote=lambda p:json.dumps(str(p))
 return '(version 1)\n(allow default)\n(deny network*)\n(deny file-write*)\n'+''.join('(allow file-write* (subpath '+quote(p)+'))\n' for p in [root/'builder',root/'runtime',root/'tmp',root/'evidence',root/'logs',W/'logs'])+'(allow file-write* (literal "/dev/null"))\n'

def main():
 started=time.monotonic();a=argparse.ArgumentParser();a.add_argument('--binding-sha',required=True);args=a.parse_args();m,cp,deps,admission=source_gate(args.binding_sha)
 import subprocess
 own=subprocess.run(['/bin/ps','-p',str(os.getpid()),'-o','pid=,ppid=,uid=,lstart=,command='],capture_output=True,text=True,timeout=10,env={'PATH':'/usr/bin:/bin','LC_ALL':'C'})
 fields=own.stdout.strip().split(None,8)
 if own.returncode or own.stderr.strip() or len(fields)!=9 or (int(fields[0]),int(fields[1]),int(fields[2]))!=(os.getpid(),os.getppid(),os.getuid()):raise Refusal('actual controller birth unavailable; no new unit')
 previous=subprocess.run(['/bin/ps','-p','30672,30699','-o','pid=,ppid=,uid=,lstart=,command='],capture_output=True,text=True,timeout=10,env={'PATH':'/usr/bin:/bin','LC_ALL':'C'})
 if previous.returncode!=1 or previous.stdout.strip() or previous.stderr.strip() or not cp.group_absent(30699):raise Refusal('previous owned process/group not absent; no new unit')
 birth={'argv':own.args,'rc':own.returncode,'stdout':own.stdout,'stderr':own.stderr,'previous_actual_ps':{'argv':previous.args,'rc':previous.returncode,'stdout':previous.stdout,'stderr':previous.stderr},'previous_owned_group_absent':True}
 root=Path(m['context']['unit_root']);errors=cp.DiagnosticErrors();result={'status':'UNKNOWN','commands':[],'first_cause':None,'old_before':None,'old_after':None,'old_roots_unchanged':False,'admission':admission,'pid':os.getpid(),'created_unix':time.time(),'effects':{'COMSOL':0,'JVM_start':0,'GUI':0,'public_network_GETs':0,'system_install':0,'application_build':0}}
 wfd=os.open(D,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW);logfd=None;copies=[];profile_records=[]
 try:
  result['old_before']=old_trees(m,deps)
  if time.monotonic()-started>600:raise Refusal('prepare cap exhausted before creating new unit')
  publish(D/'CONTROLLER_BIRTH.json',birth)
  publish(D/'UNIT_ONCE_GUARD.json',{'pid':os.getpid(),'uid':os.getuid(),'admission':admission,'started_monotonic':started,'unit_root':str(root)})
  root.mkdir(mode=0o700)
  for child in ['inputs','wheelhouse','tmp','evidence','outside','logs']: (root/child).mkdir(mode=0o700)
  rootid=secure_dir(root);publish(root/'OWNER.json',{'identity':rootid,'uid':os.getuid(),'pid':os.getpid(),'admission':admission,'unit_monotonic':started})
  for r in m['input_refs']:copies.append(exact_copy(Path(r['path']),root/'wheelhouse'/Path(r['path']).name,r))
  fixture=exact_copy(D/'MPh.ini',root/'outside/MPh.ini',m['fixture']);copies.append(fixture)
  for name in ['requirements-runtime.lock','requirements-backend.lock']:
   record=next(q for q in m['source_refs'] if q['path']==str(D/name));copies.append(exact_copy(D/name,root/'inputs'/name,record))
  profile=root/'inputs/network-and-write-deny.sb';copy= sandbox_profile(root,D).encode();fd=os.open(profile,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
  try:os.write(fd,copy);os.fsync(fd)
  finally:os.close(fd)
  profile_record=raw_file(profile)[1]
  commands=m['commands'];plan_record=publish(root/'inputs/COMMAND_PLAN.json',{'commands':commands,'root':str(root)});logfd=os.open(root/'logs',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
  env=dict(m['environment']);env['TMPDIR']=str(root/'tmp')
  publish(root/'PREPARED_INPUTS.json',{'copied_first_records':copies,'profile':profile_record,'command_plan':plan_record,'prepare_elapsed_s':time.monotonic()-started})
  if time.monotonic()-started>600:raise Refusal('complete prepare exceeds600s')
  for stage in commands:
   remaining=CAP-RESERVE-(time.monotonic()-started)
   if remaining<=0:raise Refusal('no next dispatch in cleanup reserve')
   inputs(m);secure_dir(root,rootid)
   for r in copies:raw_file(Path(r['path']),expected=r)
   raw_file(profile,expected=profile_record);raw_file(root/'inputs/COMMAND_PLAN.json',expected=plan_record)
   with socket.socket() as listener:
    listener.bind(('127.0.0.1',0));listener.listen(1);listener.settimeout(.05);port=listener.getsockname()[1]
    argv=['/usr/bin/sandbox-exec','-f',str(profile),m['context']['base_prefix']+'/bin/python3.12','-I','-B',str(D/'stage_gate.py'),'--plan',plan_record['path'],'--plan-sha',plan_record['sha256'],'--label',stage['label'],'--port',str(port)]
    # One owned Popen. Gate execs the fixed command in the same session/group; ensurepip descendants inherit it.
    remaining=CAP-RESERVE-(time.monotonic()-started)
    if remaining<=0:raise Refusal('no dispatch inside cleanup reserve after source checks')
    command=cp.run(logfd,stage['label'],argv,root/'outside',env,timeout=min(stage['cap'],remaining))
    result['commands'].append(command)
    try:
     client,address=listener.accept();client.close();accepted=True
    except TimeoutError:accepted=False
   probe_path=root/'evidence'/('PROBE_'+stage['label']+'.json');probe,probe_record=raw_file(probe_path)
   if accepted or json.loads(probe).get('status')!='PASS_ACTUAL_EPERM' or json.loads(probe).get('errno') not in (1,13):raise Refusal('actual inherited task network denial not established')
   publish(root/'evidence'/('STAGE_'+stage['label']+'_DONE.json'),{'command':command,'network_denial_probe_first_record':probe_record,'listener_accepted':accepted,'process_tree_network_deny':'SCOPED_NOT_T037_OR_FULL_OS'})
   if stage['role_profile']:
    # The first record came from actual producer stdout. Never recompute a profile as a new baseline.
    lines=(root/'logs'/command['stdout']).read_text().splitlines();data=json.loads(lines[-1]);first=data['first_profile_record'];expected_path=root/'evidence'/('PROFILE_'+stage['role_profile']+'.json')
    if first['path']!=str(expected_path):raise Refusal('actual profile path outside fixed namespace')
    sys.path.insert(0,str(D));from consumers import verify_profile
    verify_profile(expected_path,first,stage['role_profile'],m['context']);profile_records.append(first)
  result['status']='PASS_DEPENDENCY_ENVIRONMENTS_PENDING_CLOSURE';result['profiles']=profile_records
 except cp.StageUnknown as exc:
  result['status']='UNKNOWN_ORIGINAL_OWNER_RETAINED';result['first_cause']={'type':type(exc.original_cause).__name__,'detail':str(exc.original_cause)};result['original_unknown']=exc.original_result;result['same_handle']=cp.observe_unknown(logfd,exc,interval=10)
 except cp.TerminalCommandFailure as exc:
  result['status']='FIRST_TERMINAL_COMMAND_FAIL';result['first_cause']={'type':type(exc).__name__,'detail':str(exc)};result['failed_command']=exc.result
 except BaseException as exc:
  result['status']='FIRST_VALIDATION_OR_PREPARE_FAIL';result['first_cause']={'type':type(exc).__name__,'detail':str(exc)};cp.fallback(repr(result['first_cause']))
 finally:
  try:result['old_after']=old_trees(m,deps);result['old_roots_unchanged']=True;inputs(m)
  except BaseException as exc:result['old_roots_unchanged']=False;errors.append(cp.failure_detail('old_preservation_and_inputs',exc));cp.fallback(repr(errors[-1]))
  if root.exists():
   try:publish(D/'COMPLETE_NEW_UNIT_TREE.json',deps.tree_record(root))
   except BaseException as exc:errors.append(cp.failure_detail('new_tree_capture',exc));cp.fallback(repr(errors[-1]))
  if logfd is not None:cp.release_log(logfd,errors)
  result['elapsed_including_cleanup']=time.monotonic()-started;result['diagnostic_errors']=errors;result['external_tool_wait_required']=True
  if errors or not result['old_roots_unchanged'] or result['elapsed_including_cleanup']>CAP:result['status']='UNKNOWN_CLEANUP_OR_CAP'
  elif result['status']=='PASS_DEPENDENCY_ENVIRONMENTS_PENDING_CLOSURE':result['status']='PASS_SCOPED_DEPENDENCY_ENVIRONMENTS_PENDING_FINAL_TOOL_WAIT'
  cp.diagnostic_write(wfd,'TERMINAL.json',result,errors);cp.release_log(wfd,errors)
 final='PASS_SCOPED_DEPENDENCY_ENVIRONMENTS' if result['status']=='PASS_SCOPED_DEPENDENCY_ENVIRONMENTS_PENDING_FINAL_TOOL_WAIT' and not errors and time.monotonic()-started<=CAP else result['status']
 cp.diagnostic_print({'status':final,'elapsed':time.monotonic()-started,'unit_root':str(root),'errors':errors,'actual_tool_wait_required':True},errors)
 return 0 if final=='PASS_SCOPED_DEPENDENCY_ENVIRONMENTS' and not errors else 1
if __name__=='__main__':sys.exit(main())
