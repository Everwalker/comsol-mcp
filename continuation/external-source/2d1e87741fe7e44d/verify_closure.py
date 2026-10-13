from pathlib import Path
import sys,json,hashlib,ast,difflib,xml.etree.ElementTree as ET
B=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2');R=B/'repository';E=R/'docs/full_project_continuation_v2/evidence/intel-builder-execution-r1';O=Path(__file__).parent
sys.path[:0]=[str(E),str(R)]
import openssl359_runtime_successor_r1 as actor
import openssl359_cpython_successor_r4 as prior
PIN='02afca66d50f5e005dc2308eed0f69015937789b3f6995c1036c2badbe769d94'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def resolve(v):
 p=Path(v);return p if p.is_absolute() else R/p
F=E/'OPENSSL359_RUNTIME_SOURCE_FREEZE_R1.json';assert sha(F)==PIN
f=json.loads(F.read_bytes());parent=json.loads(resolve(f['parent_freeze']['path']).read_bytes())
assert sha(resolve(f['parent_freeze']['path']))==f['parent_freeze']['sha256']=='ce38de67f4f2e0d07fbeef49357c7f3f1608bfc242288f369cae0d0296742e36'
current={row['path']:row for row in f['source_files']};old={row['path']:row for row in parent['source_files']}
assert len(current)==65 and len(old)==61 and all(current[k]==row for k,row in old.items());new=[k for k in current if k not in old];assert len(new)==4
checked=[]
for row in f['source_files']+f['primary_refs']+[f['current_design']]:
 p=resolve(row['path']);assert prior.frozen_digest(p)=={'bytes':row['bytes'],'sha256':row['sha256']};checked.append(str(p))
assert len(f['primary_refs'])==1187 and len({row['path'] for row in f['primary_refs']})==1187
assert sha(resolve(f['current_design']['path']))=='965fda3e2e6f0afb4e491e48c2fdff5e8233ca0a1b7282e760b59c5440429db4'
for row in f['source_files']:ast.parse(resolve(row['path']).read_text(),filename=row['path'])
diffs=[]
for after,before in [('openssl359_runtime_profile_r2.py','openssl359_runtime_profile_r1.py'),('openssl359_runtime_successor_r1.py','openssl359_cpython_successor_r4.py'),('openssl359_runtime_verify_r1.py','openssl359_cpython_verify_r4.py')]:
 p=E/after;q=E/before;delta=''.join(difflib.unified_diff(q.read_text().splitlines(True),p.read_text().splitlines(True),fromfile=str(q),tofile=str(p)))
 dest=O/(after+'.independent.diff');dest.write_text(delta);diffs.append({'source':str(p),'semantic_parent_basis':str(q),'delta':str(dest),'sha256':sha(dest)})
archives=prior.verify_original_archives(f);preserved=actor.verify_inputs();assert preserved['R4_records']==14215
receipt_path=E/'OPENSSL359_CPYTHON_EXECUTION_RESULT_R4.json';assert sha(receipt_path)=='ac0bc9e027a0f8741f31e585184a0ba3d183e059d03b59c01e90b62354cbf87f'
actual=json.loads(receipt_path.read_bytes());original_plan,env=prior.command_plan(prior.layout());commands=[]
for row,(label,argv,cwd) in zip(actual['commands'],original_plan,strict=True):
 assert row['argv']==argv and row['owned_process_terminal'] and row['owned_group_absent'] and not row['diagnostic_errors']
 assert row['rc']==(2 if label=='runtime' else 0)
 commands.append({'label':label,'argv':argv,'pid':row['pid'],'rc':row['rc'],'original_terminal_wait_and_group_absence':True,'stdout':row['stdout'],'stderr':row['stderr']})
stderr=actor.INSTALLED/'logs'/actual['commands'][-1]['stderr'];text=stderr.read_text();assert 'actual SSL provider is not fixed OpenSSL3.5.9' in text
stdout=actor.INSTALLED/'logs'/actual['commands'][-1]['stdout'];assert 'ACTUAL_PROVIDER_FIRST_READ' not in stdout.read_text()
tests=[];names=[]
for item in f['controls']:
 root=Path(item['root']);cases=list(ET.parse(root/'raw.junit.xml').iter('testcase'))
 assert len(cases)==item['counts']['tests'] and not any(list(c.iter(t)) for c in cases for t in ('failure','error','skipped'))
 names.extend(c.attrib['name'] for c in cases);tests.append({'root':str(root),'executions':len(cases),'JUnit_sha256':sha(root/'raw.junit.xml'),'guard':item['guard'],'target_runtime':False})
assert len(names)==20 and len(set(names))==19
assert not actor.ROOT.exists() and not actor.ROOT.is_symlink()
recipe=actor.recipe();assert recipe.count('\n\t')==2
assert prior.UNIT_CAP==1800 and prior.RESERVE==300 and prior.CAPS['prepare']==600 and prior.CAPS['runtime']==180
report={'status':'PASS_EXACT_SOURCE_INPUT_HISTORICAL_CONTROL_CLOSURE_NOT_SEMANTIC_ACCEPTANCE','exact_freeze':str(F),'freeze_sha256':PIN,'source_count':65,'parent61_byte_exact':True,'AST':65,'new4_sources':new,'new4_all_read':True,'three_semantic_parent_diffs':diffs,
 'primary_unique_refs':1187,'actual_unique_sameFD_checks':len(set(checked)),'original43_actual_guard':archives,'actual_R4_fulltree_preservation':preserved,
 'actual_R4_original_failure':{'path':str(receipt_path),'sha256':sha(receipt_path),'status':actual['closed']['status'],'command_rcs':[row['rc'] for row in actual['commands']],
  'all6_original_commands_raw_ownedwait':commands,'actual_R4_stderr_sha256':sha(stderr),'actual_R4_stdout_sha256':sha(stdout),'first_failure':'Original INFO[:3] compared to(3,5,9); actual full tuple was not captured','knownvectors_certs_MemoryBIO':'NOT_REACHED','not_promoted_RUNTIME_PASS':True},
 'historical_controls':tests,'historical_distinct':19,'historical_executions':20,'historical_guard':'NOT_SUPPLIED','old61_or_current19_groups_rerun':False,
 'new_namespace':str(actor.ROOT),'new_namespace_absent':True,'future_one_outer_runtime_make':{'argv':['/usr/bin/make','-f',str(actor.ROOT/'Runtime.mk'),'runtime'],'recipe':recipe,'fixed_children':2},
 'budget':{'unit':1800,'cleanup_reserve':300,'prepare':600,'runtime':180},'new_targetPython_compile_unpack_COMSOL':0,'semantic_acceptance':'CHANGES_REQUIRED_PENDING_FINAL_RECEIPT'}
(O/'CLOSURE_VERIFICATION.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({'sources':65,'parent61_exact':True,'new4':4,'AST':65,'primary':1187,'actualsameFD':len(set(checked)),'raw_distinct':19,'raw_execution':20,'actual_old_R4_tree':14215,'new_native':0,'semantic_result':'CHANGES_REQUIRED'}))
