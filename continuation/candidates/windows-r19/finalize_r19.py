from pathlib import Path
import ast, json, hashlib, difflib, datetime, xml.etree.ElementTree as ET
P=Path(__file__).resolve().parent; B=P.parent/'windows_appcontainer_sol_r18_20261013'; C=P/'candidate'; A=B/'candidate'
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def write(name,value): (P/name).write_text(json.dumps(value,indent=2,sort_keys=True)+'\n')
def ref(p): return {'path':str(p),'bytes':p.stat().st_size,'sha256':sha(p)}
F=B/'SOURCE_FREEZE_R18.json'; assert sha(F)=='422564af7a07b30558be0cd74d496a657611d18300588d7a4d9fd18930aad0e5'
f=json.loads(F.read_bytes())
review=B.parent/'windows-r18-independent-source-review/REVIEW_RECEIPT.json';assert sha(review)=='8b196d2837e516ad1c23c2385f77d07dfce2ae2a353813bc99e3214f59cae387'
repro=review.parent/'ACTUAL_UNCLASSIFIED_CREATE_RECONCILE_RESIDUAL.json';assert sha(repro)=='796a12a87e282ffb45df0a5c8ec3e855bc1757f1167843e4143ef305db3b568a'
for rel,h in f['source_files'].items():assert sha(A/rel)==h
for rel,h in f['evidence_files'].items():assert sha(B/rel)==h
names=set(f['source_files'])|{str(p.relative_to(C)) for p in C.rglob('*.py') if '.pytest_cache' not in p.parts}
sources={name:sha(C/name) for name in sorted(names)}
delta={name:{'before':f['source_files'].get(name),'after':h} for name,h in sources.items() if f['source_files'].get(name)!=h}
assert set(delta)=={'comsol_mcp/_windows_appcontainer_native.py','comsol_mcp/_windows_service_sid.py','tests/test_windows_appcontainer_r19_owner.py'}
py=[name for name in sources if name.endswith('.py')]
for name in py:ast.parse((C/name).read_bytes(),filename=name)
patch=''.join(''.join(difflib.unified_diff((A/name).read_text().splitlines(True) if (A/name).exists() else [],(C/name).read_text().splitlines(True),fromfile='R18/'+name,tofile='R19/'+name)) for name in sorted(delta))
(P/'SOURCE_DELTA_R19.patch').write_text(patch)
closure=json.loads((B/'CALLER_CLOSURE_R18.json').read_bytes());closure['chain']+=' → original CREATE call closure terminal → existing SCM preflight predicate → both Controller strong-owner consumers'
for name,row in closure['references'].items():row['sha256']=sources[name]
closure['references']['comsol_mcp/_windows_appcontainer_native.py']['ranges']=[{'start':1,'end':len((C/'comsol_mcp/_windows_appcontainer_native.py').read_text().splitlines())}]
closure['references']['comsol_mcp/_control_daemon.py']['ranges'].append({'start':757,'end':783})
closure['references']['comsol_mcp/_windows_service_sid.py']={'sha256':sources['comsol_mcp/_windows_service_sid.py'],'ranges':[{'start':270,'end':276}]}
closure['classification']='SOURCE_SOFTWARE_ONLY_NO_NATIVE_OR_OS_GENERATION_PROOF';closure['original_call_fact_source']='captured production closure, no public diagnostic state authorization'
write('CALLER_CLOSURE_R19.json',closure)
lanes={}
for label in ('r19-owner-attempt01','r19-owner-attempt02','r19-owner-final01','r19-original-regression01','r19-owner-final02','r19-original-regression02'):
 cases=list(ET.parse(P/f'evidence/{label}.junit.xml').iter('testcase'))
 counts={key:sum(bool(list(row.iter(tag))) for row in cases) for key,tag in [('FAIL','failure'),('ERROR','error'),('SKIP','skipped')]};counts['PASS']=len(cases)-sum(counts.values())
 receipt=json.loads((P/f'evidence/{label}.receipt.json').read_bytes());assert receipt['guard_active']==1 and receipt['guard_denied']==0
 counts.update(receipt=ref(P/f'evidence/{label}.receipt.json'),junit=ref(P/f'evidence/{label}.junit.xml'),guard=ref(P/f'evidence/{label}.guard.jsonl'),guardACTIVE=1,guardDENY=0)
 lanes[label]=counts
assert lanes['r19-owner-final02']['PASS']==50 and lanes['r19-original-regression02']['PASS']==274
mac=json.loads((B/'PRESERVED_MAC_RUNTIME_FAILURES_R18.json').read_bytes())
write('INHERITED_MAC_FAILURES_R19.json',{'classification':'TEN_ORIGINAL_MAC_FAIL_PRESERVED_NOT_RERUN','parent':ref(B/'PRESERVED_MAC_RUNTIME_FAILURES_R18.json'),'original_FAIL_ids':[x['id'] for x in mac['actual_R18_failing_tests']],'current_rerun':'NOT_RUN','not_reclassified_to_PASS':True,'byte_unchanged':{rel:sources[rel]==f['source_files'][rel] for rel in mac['unchanged_memory_and_tests']}})
gaps=dict(f['status']['next_required_gaps']);gaps.update(T037='REQUIRED_OPEN',quota='REQUIRED_OPEN',full_filesystem_isolation='REQUIRED_OPEN',service_bootstrap='REQUIRED_OPEN',child_issuer='REQUIRED_OPEN',private_root='REQUIRED_OPEN')
write('NEXT_ORIGINAL_GAPS_R19.json',gaps)
status={'schema':'WINDOWS_APPCONTAINER_R19_STATUS/1','classification':'SOURCE_SOFTWARE_ONLY','finding_WINDOWS_APPCONTAINER_UNKNOWN_CALL_OWNER_01':'FIXED_PENDING_INDEPENDENT_REVIEW','independent_review':'PENDING_ROOT','software':lanes,'fresh_final_PASS':324,'fresh_final_FAIL':0,'fresh_final_ERROR':0,'fresh_final_SKIP':0,'first_attempt_FAIL':2,'first_attempt_preserved':'test seam intercepted pre-admission SCM LocalFree; fixed by arming at actual capability allocation','prior_mac_FAIL':10,'prior_mac_rerun':'NOT_RUN','full477_suite_rerun':'NOT_RUN','native_windows_COMSOL_science':'NOT_RUN','live_repository_CAS_PROGRESS_Git_effects':0,'CREATE_retry_Delete_native_service_system_config_effects':0,'next_required_gaps':gaps,'generation_for_memory_cleanup':'NOT_REQUIRED_NO_NEW_OS_AUTHORITY','Store_UNKNOWN_safe_retry_false':'PRESERVED','appcontainer_state':'CONFIGURED_UNVERIFIED_ONLY','restore_state':'RESTORE_NOT_IMPLEMENTED'}
write('CURRENT_STATUS_R19.json',status)
parent_chain={'R18_freeze':ref(F),'R18_independent_review':ref(review),'R18_actual_repro':ref(repro),'R18_source_files_byte_preserved':len(f['source_files']),'R18_raw_evidence_files_byte_preserved':len(f['evidence_files']),'all_R18_preserved':True}
write('PARENT_PRESERVATION_R19.json',parent_chain)
freeze={'schema':'WINDOWS_APPCONTAINER_R19_SOURCE_FREEZE/1','created_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'candidate':str(C),'baseline':ref(F),'source_count':len(sources),'ast_count':len(py),'ast_parse':'PASS_SYNTAX_ONLY','source_files':sources,'delta':delta,'deleted_sources':[],'same_parent_sources':sum(sources[n]==h for n,h in f['source_files'].items()),'excluded_non_source':['.pytest_cache generated cache; inherited cache copies not acceptance source','-B / PYTHONDONTWRITEBYTECODE used'],'parent_preservation':parent_chain,'status':status}
freeze['evidence_files']={str(p.relative_to(P)):sha(p) for p in sorted(P.rglob('*')) if p.is_file() and 'candidate' not in p.relative_to(P).parts and p.name!='SOURCE_FREEZE_R19.json'}
write('SOURCE_FREEZE_R19.json',freeze)
print(json.dumps({'freeze':ref(P/'SOURCE_FREEZE_R19.json'),'source_count':len(sources),'AST':len(py),'delta':list(delta),'evidence_count':len(freeze['evidence_files'])}))
