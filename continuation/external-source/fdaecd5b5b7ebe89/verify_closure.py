from pathlib import Path
import sys,json,hashlib,ast,difflib,xml.etree.ElementTree as ET
B=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2');R=B/'repository';E=R/'docs/full_project_continuation_v2/evidence/intel-builder-execution-r1';O=Path(__file__).parent
sys.path[:0]=[str(E),str(R)]
import liblzma_successor_r1 as actor
import liblzma_prepare_r1 as prep
import liblzma_inputs_r1 as inputs
import liblzma_cpython_checkpoint_r1 as cp_inputs
PIN='2e66519156b4764ec4488bb5b2f680a09c28c202484ea2096c232a382354a43b'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def resolve(v):
 p=Path(v);return p if p.is_absolute() else R/p
F=E/'LIBLZMA_SOURCE_FREEZE_R1.json';assert sha(F)==PIN
f=json.loads(F.read_bytes());parent=json.loads(resolve(f['parent_freeze']['path']).read_bytes())
assert sha(resolve(f['parent_freeze']['path']))==f['parent_freeze']['sha256']=='ba426e936583648f83fece2ae6a584295e9c3c0ade893d05897b073aede8a0da'
current={row['path']:row for row in f['source_files']};old={row['path']:row for row in parent['source_files']}
assert len(current)==82 and len(old)==69 and all(current[k]==row for k,row in old.items())
new=[k for k in current if k not in old];assert len(new)==13
checked=[]
for row in f['source_files']+f['primary_refs']+[f['current_design']]:
 p=resolve(row['path']);assert actor.frozen_digest(p)=={'bytes':row['bytes'],'sha256':row['sha256']};checked.append(str(p))
assert len(f['primary_refs'])==1340 and len({row['path'] for row in f['primary_refs']})==1340
assert sha(resolve(f['current_design']['path']))=='c10cd80d959aac6e982e4016a2183c23882d991ff2b9a863edf51d198799053e'
for row in f['source_files']:ast.parse(resolve(row['path']).read_text(),filename=row['path'])
design=json.loads(resolve(f['current_design']['path']).read_bytes());diffs=[]
pairs=[('liblzma_successor_r1.py','openssl359_cpython_successor_r4.py'),('liblzma_build_gate_r1.py','openssl359_build_gate_r4.py'),('liblzma_cpython_checkpoint_r1.py','openssl359_cpython_checkpoint_r4.py'),('liblzma_runtime_profile_r1.py','openssl359_runtime_profile_r3.py'),('liblzma_verify_r1.py','openssl359_runtime_verify_r2.py'),('liblzma_merge_r1.py','openssl359_merge_r2.py'),('liblzma_cert_checks_r1.py','tls_cert_checks_r4.py')]
for after,before in pairs:
 delta=''.join(difflib.unified_diff((E/before).read_text().splitlines(True),(E/after).read_text().splitlines(True),fromfile=before,tofile=after))
 frozen=B/'local-preservation/liblzma-unit-source-freeze-r1'/(after+'.diff');assert delta==frozen.read_text()
 dest=O/(after+'.independent.diff');dest.write_text(delta);diffs.append({'source':str(E/after),'parent':str(E/before),'delta':str(dest),'sha256':sha(dest),'matches_frozen_diff':True})
plan,env=actor.command_plan(actor.layout())
assert len(plan)==13 and len({actor.stage_done_record_name(label).casefold() for label,_,_ in plan})==13
for expected,(label,argv,cwd) in zip(design['plan'],plan,strict=True):
 assert expected['label']==label and expected['argv']==argv and expected['cwd']==str(cwd) and expected['stage_cap_s']==actor.CAPS[label]
 assert expected['environment']==actor.stage_environment(label,env)
assert actor.UNIT_CAP==1800 and actor.RESERVE==300 and actor.CAPS['prepare']==600 and actor.CAPS['runtime']==180
archives=actor.verify_original_archives(f)
xz=inputs.verify_inputs_and_scan();assert xz['inventory']['members']==704 and xz['inventory']['expanded_bytes']==11947792 and xz['inventory']['links']==0 and not xz['actual_unpack']
assert xz['inputs']==design['authenticated_input']
tls=prep.verify_tls_runtime();checkpoint=cp_inputs.preservation_verified_summary()
assert tls==design['actual_prerequisite']
acq=resolve(design['official_XZ_acquisition']['path']);assert sha(acq)==design['official_XZ_acquisition']['sha256'];acquisition=json.loads(acq.read_bytes())
counts=[];names=[];inherited=[]
for index,item in enumerate(f['controls']):
 root=Path(item['root']);cases=list(ET.parse(root/'raw.junit.xml').iter('testcase'))
 assert len(cases)==item['counts']['tests'] and not any(list(c.iter(t)) for c in cases for t in ('failure','error','skipped'))
 record={'root':str(root),'JUnit_sha256':sha(root/'raw.junit.xml'),'executions':len(cases),'guard':item['guard'],'rerun':False}
 if index<2:counts.append(record);names.extend(c.attrib['name'] for c in cases)
 else:inherited.append(record)
assert len(names)==13 and len(set(names))==13 and sum(x['executions'] for x in inherited)==11
assert not actor.layout()['root'].exists() and not actor.layout()['root'].is_symlink()
report={'status':'PASS_EXACT_FROZEN_SOURCE_INPUT_RAW_CLOSURE_PENDING_SEMANTIC_RECEIPT','freeze':str(F),'freeze_sha256':PIN,'source_count':82,'AST':82,'parent69_byte_exact':True,'new13':new,'new11_variants_plus2_inherited_helpers':True,'seven_exact_parent_diffs':diffs,'unique_primary_refs':1340,'actual_unique_sameFD_checks':len(set(checked)),'original43':archives,'actual_XZ_readonly_scan':xz,'actual_XZ_acquisition':{'path':str(acq),'sha256':sha(acq),'receipt':acquisition},'original_TLS_runtime_and_R4_full_preservation':tls,'actual359_checkpoint_and_all_old_preservation':checkpoint,'new_distinct_controls':13,'new_actual_executions':13,'historical_affected_controls':counts,'inherited_input11':inherited,'guard_historical':'NOT_SUPPLIED','old69_tests_rerun':0,'future_exact_plan_matches_design':True,'future_outer_commands':13,'budget':{'unit_s':1800,'cleanup_reserve_s':300,'prepare_s':600,'runtime_s':180},'new_namespace':str(actor.layout()['root']),'new_namespace_absent':True,'new_native_targetPython_unpack_COMSOL':0}
(O/'CLOSURE_VERIFICATION.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({k:report[k] for k in ['source_count','AST','parent69_byte_exact','unique_primary_refs','actual_unique_sameFD_checks','new_distinct_controls','new_actual_executions','new_namespace_absent','new_native_targetPython_unpack_COMSOL']}))
