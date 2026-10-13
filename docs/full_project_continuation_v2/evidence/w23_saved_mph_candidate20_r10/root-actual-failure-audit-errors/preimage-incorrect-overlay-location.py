import os, stat, json, hashlib, pathlib, sqlite3, datetime
B=pathlib.Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2')
E=B/'repository/docs/full_project_continuation_v2/evidence/w23_saved_mph_candidate20_r10'
D=B/'local-preservation/w23-activity018-vs016-frequency-source-diagnosis-20261013-001'
refs={}
def read(p, h=None, n=None):
 p=pathlib.Path(p); fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW)
 try:
  s=os.fstat(fd); assert stat.S_ISREG(s.st_mode),str(p)
  buf=bytearray()
  while True:
   chunk=os.read(fd,1048576)
   if not chunk: break
   buf.extend(chunk)
  z=os.fstat(fd); assert (s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)==(z.st_dev,z.st_ino,z.st_size,z.st_mtime_ns,z.st_ctime_ns)
 finally: os.close(fd)
 v=bytes(buf); actual=hashlib.sha256(v).hexdigest()
 assert h is None or actual==h,(str(p),actual,h)
 assert n is None or len(v)==n,(str(p),len(v),n)
 refs[str(p)]={'path':str(p),'bytes':len(v),'sha256':actual,'device':s.st_dev,'inode':s.st_ino,'mode':stat.S_IMODE(s.st_mode)}
 return v
def j(p,h=None,n=None): return json.loads(read(p,h,n))
def checked(obj):
 if isinstance(obj,dict):
  if isinstance(obj.get('path'),str) and 'sha256' in obj and isinstance(obj.get('bytes'),int): read(obj['path'],obj['sha256'],obj['bytes'])
  for v in obj.values(): checked(v)
 elif isinstance(obj,list):
  for v in obj: checked(v)
P=j(E/'activity018-actual-terminal-001/PRESERVATION_RECEIPT.json','3f52e575e34a074ce25e3633203a3f04be54c819d42f89bf754f40bffd5c0c1c',7680)
Q=j(D/'ROOTCAUSE_AND_MINIMAL_PROPOSAL.json','5d43d423a4159aa840c7485c67546f83115cfe83410a15653fff53d6dcab7710',7106)
checked(P);checked(Q)
raw=j(P['full_native_capture']['path']); checked(raw);assert raw['file_count']==63
work=j(P['complete_work_durable_copy']['path']);checked(work);assert work['regular_file_count']==72
for s in work['symlinks']:
 assert s['followed'] is False and stat.S_ISLNK(os.lstat(s['path']).st_mode) and os.readlink(s['path'])==s['link_target']
summ=j(P['summary']['path']);assert summ['status']=='FAIL_OR_INCOMPLETE' and summ['native_calls']==[]
assert summ['study_run_dispatch_count']==summ['solver_run_dispatch_count']==0
assert P['original_tool']['handle_terminal_observed'] and P['original_tool']['exit_code']==2 and P['slots_submitted']==0
assert P['cleanup_status']=='CLEANUP_VERIFIED_EXACT_OWNED_PIDS_AND_PORTS_ABSENT'
assert P['birth_budget']['max_seconds']==1800 and not P['birth_budget']['budget_exceeded']
assert P['quiescent'] and P['probes_ok'] and P['firstFAIL_UNKNOWN_stopped_all_following_native'] and P['W24_not_dispatched']
cas=j(E/'activity018-actual-terminal-001/RESUME_CAS_TERMINAL_ACTUAL_NATIVE_FAIL.json'); checked(cas)
assert cas['after_sha256']=='9a0303872bf31669bb7af7dcf471ef98337257b9e5a5561aac4f9186c0baeb42' and cas['active_jobs']==[]
R=B/'state/RESUME.json';resume=j(R,cas['after_sha256']);assert resume['active_jobs']==[]
native=pathlib.Path(P['summary']['path']).parent
db=sqlite3.connect('file:'+P['managed_ledger']['raw_sqlite_snapshot']['path']+'?mode=ro',uri=True)
assert db.execute('pragma quick_check').fetchall()==[('ok',)]
jobs=db.execute('select status,count(*) from jobs group by status').fetchall();assert jobs==[('SUCCEEDED',9)],jobs
ops=db.execute('select operation,status,count(*) from operations group by operation,status').fetchall()
assert not any(o in ('study.run','solver.run','run_study') for o,_,_ in ops)
events=[json.loads(x) for x, in db.execute("select metadata from job_events where event='worker_request' order by id")]
pairs={}
for event in events: pairs.setdefault(event['request_id'],[]).append(event['phase'])
assert len(events)==600 and len(pairs)==300 and all(v==['submitted','observed']for v in pairs.values())
db.close()
ledger=j(P['managed_ledger']['receipt']['path']); assert ledger['safe_for_owned_cleanup'] and ledger['active_queued_or_running_jobs']==[] and ledger['durable_unknown_jobs']==[]
rpc=j(P['direct_rpc']['receipt']['path']);assert rpc['rpc_count']==3 and rpc['safe_for_owned_cleanup']
fields=j(Q['field_projection']['path']);a,b=fields['cases']
assert a['raw_java_main_frequency_sequence']==b['raw_java_main_frequency_sequence'] and a['raw_worker_sequence_parameter_observation']==b['raw_worker_sequence_parameter_observation']
assert a['stage_binding']==b['stage_binding']
rawdiag=j(native/'saved_solution_diagnostic_raw.json')
terminal=j(native/'managed_solution_indices_dset1_terminal_response.json')
projection=j(native/'managed_solution_indices_dset1_projection.json')
assert terminal['data']==projection['body']==b['canonical_dataset_solution_indices']
assert terminal['data']['solver_sequence_parameter_observation']==b['raw_worker_sequence_parameter_observation']
seq=next(x for x in rawdiag['readback']['solver_sequences'] if x.get('tag')=='sol1')
assert seq['direct_sequence_parameter_axis_observation']['get_size']['return_value']==[64057,1]
assert seq['direct_sequence_parameter_axis_observation']['get_size_multi']['return_value']==[1,1]
assert a['canonical_changed_fields']['axis_metadata_complete'] is False and b['canonical_changed_fields']['axis_metadata_complete'] is True
assert a['canonical_changed_fields']['outer_indices']==[] and b['canonical_changed_fields']['outer_indices']==[1]
assert sum(not v for v in b['strict_predicate_observation'].values())==1 and b['strict_predicate_observation']['canonical_axis_metadata_complete_false'] is False
for ref in Q['primary_same_version_API_chunks']: j(ref['path'],ref['sha256'],ref['bytes'])
assert Q['source_comparison'][2]['identical'] and Q['native_dispatches_this_diagnosis']==0
out={'schema':'ROOT_ACTIVITY018_ACTUAL_FAILURE_CHECK_AND_SOURCE_REPAIR_DISPOSITION/1','created_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'status':'ACTUAL_FAIL_PRESERVATION_AND_SOURCE_ROOTCAUSE_CONFIRMED_REPAIR_REQUIRED','Goal':'ACTIVE_INCOMPLETE','activity018':'FAIL_NOT_REPLAYABLE','exact_original_tool':P['original_tool'],'native_slots':0,'explicit_Study_solver':0,'internal_solver_count':'UNKNOWN_NOT_INFERRED','SQLite_jobs':jobs,'SQLite_operations':ops,'Worker_pairs':300,'direct_RPC_terminal':3,'native_raw_files_verified':63,'work_regular_source_and_durable_copy_verified':72,'work_symlink_objects_lstat_readlink_verified_not_followed':27,'cleanup':'EXACT_OWNED_ABSENCE_VERIFIED_IN_PRESERVED_FRESH_INVENTORY','fresh_probe_in_this_Root_audit':False,'RESUME_current_sha256':cas['after_sha256'],'active_jobs':[],'rootcause':'R11/R7 merge restored getSolnum(1,true) fallback after empty getOuterSolnum; this wrongly minted outer=[1] and pair/axis completeness. Actual016/018 count getters and direct Worker observations are equal. Unchanged strict protocol correctly refused canonical projection.','repair_decision':{'allowed':'New candidate from full r10, restore original R7 observed-empty-outer safety branch in SolutionBinding. Preserve all other R11 retained result-ticket ownership changes. Add actual018 typed transcript backed production algorithm positive and no-outer/multi-axis/missing-count/finite/unit/revision negatives; run affected software/SDK checks.','not_allowed':'Do not edit 018 raw/source/freeze, replay any historical request, weaken strict protocol, replace missing field data or empty axes, infer raw GHz as physical Hz, claim scientific PASS or launch COMSOL before corrected freeze/material review and new fresh ownership admission.','new_native_scope':'Same original eight existing-MPH slots zero explicit Study/solver under persistent human authorization; separate new activity, per-activity firstFAIL/UNKNOWN and original 1800 inclusive cleanup.','W24':'HOLD_AFTER_FIRSTFAIL_NOT_DISPATCHED','independent_private_build':'Reviewed liblzma R2 ordinary private build may proceed; no shared COMSOL processes or CAS/native chain.'},'source_diagnosis':refs[str(D/'ROOTCAUSE_AND_MINIMAL_PROPOSAL.json')],'preservation':refs[str(E/'activity018-actual-terminal-001/PRESERVATION_RECEIPT.json')],'actual_current_reference_pins':list(refs.values())}
p=E/'ROOT_ACTUAL_ACTIVITY018_FAILURE_CHECK_AND_REPAIR_DISPOSITION.json'
v=(json.dumps(out,indent=2,ensure_ascii=False,sort_keys=True)+'\n').encode();fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
try: os.write(fd,v);os.fsync(fd)
finally:os.close(fd)
print(json.dumps({'status':out['status'],'path':str(p),'bytes':len(v),'sha256':hashlib.sha256(v).hexdigest(),'references':len(refs)}))
