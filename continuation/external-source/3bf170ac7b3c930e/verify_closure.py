from pathlib import Path
import sys,json,hashlib,ast,difflib,xml.etree.ElementTree as ET
B=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2');R=B/'repository';E=R/'docs/full_project_continuation_v2/evidence/intel-builder-execution-r1';O=Path(__file__).parent
sys.path[:0]=[str(E),str(R)]
import liblzma_successor_r2 as actor
import liblzma_prepare_r2 as prep
import liblzma_inputs_r1 as inputs
import liblzma_cpython_checkpoint_r2 as cp_inputs
PIN='3d6cf9b36dad862d4a125b337dee62405db1021d4aa14ca33fbd348550cdf9ea'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def resolve(v):
 p=Path(v);return p if p.is_absolute() else R/p
F=E/'LIBLZMA_SOURCE_FREEZE_R2.json';assert sha(F)==PIN
f=json.loads(F.read_bytes());parent=json.loads(resolve(f['parent_freeze']['path']).read_bytes())
assert sha(resolve(f['parent_freeze']['path']))==f['parent_freeze']['sha256']=='2e66519156b4764ec4488bb5b2f680a09c28c202484ea2096c232a382354a43b'
current={row['path']:row for row in f['source_files']};old={row['path']:row for row in parent['source_files']}
assert len(current)==90 and len(old)==82 and all(current[k]==row for k,row in old.items())
new=[k for k in current if k not in old];assert len(new)==8
checked=[]
for row in f['source_files']+f['primary_refs']+[f['current_design']]:
 p=resolve(row['path']);assert actor.frozen_digest(p)=={'bytes':row['bytes'],'sha256':row['sha256']};checked.append(str(p))
assert len(f['primary_refs'])==1425 and len({row['path'] for row in f['primary_refs']})==1425
assert sha(resolve(f['current_design']['path']))=='9ef0e99627773f382545c7d96b9729541322892139390b2a0b2d3dd968a2d8e9'
for row in f['source_files']:ast.parse(resolve(row['path']).read_text(),filename=row['path'])
design=json.loads(resolve(f['current_design']['path']).read_bytes());diffs=[]
pairs=[(stem+'_r2.py',stem+'_r1.py') for stem in ('liblzma_successor','liblzma_prepare','liblzma_cpython_checkpoint','liblzma_merge','liblzma_runtime_profile','liblzma_verify','liblzma_acceptance')]
for after,before in pairs:
 delta=''.join(difflib.unified_diff((E/before).read_text().splitlines(True),(E/after).read_text().splitlines(True),fromfile=before,tofile=after))
 frozen=B/'local-preservation/liblzma-unit-source-freeze-r2'/(after+'.diff');assert delta==frozen.read_text()
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
original_r1=prep.verify_failed_r1();assert len(original_r1)==5799
actual_receipt=resolve(design['original_R1_first_failure']['path']);assert sha(actual_receipt)==design['original_R1_first_failure']['sha256']=='7721fb0c0ce144107a461465c7839583bacc4812b497c78a368367a9a93704da'
actual=json.loads(actual_receipt.read_bytes());assert actual['commands']==[] and actual['native_outer_commands']==0 and actual['status']=='VALIDATION_FAIL_COMPLETE_LIST_ORDER_ONLY' and actual['actual_CP_prerequisite_safe_unpack']==actual['actual_XZ_safe_unpack']==1
prep.extracted_content_matches(prep.FAILED_R1/'xz-input',xz['inventory'])
current_controls=[];names=[]
for item in f['controls']:
 root=Path(item['root']);cases=list(ET.parse(root/'raw.junit.xml').iter('testcase'))
 assert len(cases)==item['counts']['tests'] and not any(list(c.iter(t)) for c in cases for t in ('failure','error','skipped'))
 names.extend(c.attrib['name'] for c in cases);current_controls.append({'root':str(root),'JUnit_sha256':sha(root/'raw.junit.xml'),'tests':len(cases),'guard':item['guard'],'rerun':False})
assert len(names)==len(set(names))==11
inherited=[]
for item in f['inherited_R1_controls']:
 root=Path(item['root']);cases=list(ET.parse(root/'raw.junit.xml').iter('testcase'));assert len(cases)==item['counts']['tests'] and not any(list(c.iter(t)) for c in cases for t in ('failure','error','skipped'))
 inherited.append({'root':str(root),'JUnit_sha256':sha(root/'raw.junit.xml'),'tests':len(cases),'guard':item['guard'],'rerun':False})
assert [row['tests'] for row in inherited]==[11,2,11]
assert not actor.layout()['root'].exists() and not actor.layout()['root'].is_symlink()
ind_actual=B/'local-preservation/liblzma-r1-independent-actual-failure-review/FINAL_REVIEW_RECEIPT.json';assert sha(ind_actual)=='e0a1ea8acc786efd8e5871e98404fd5ad03cc5b5ec8a7cb9d8de2d51d304cd19'
report={'status':'PASS_EXACT_FROZEN_R2_WHOLE_SOURCE_INPUT_RAW_CLOSURE_PENDING_FINAL_RECEIPT','freeze':str(F),'freeze_sha256':PIN,'source_count':90,'AST':90,'parent82_byte_exact':True,'new8':new,'seven_necessary_variants_one_new_test':True,'seven_exact_parent_diffs':diffs,'unique_primary_refs':1425,'actual_unique_sameFD_checks':len(set(checked)),'original43':archives,'XZauth3_scan704_no_unpack':{'inputs':xz['inputs'],'members':xz['inventory']['members'],'expanded_bytes':xz['inventory']['expanded_bytes'],'links':0},'original_TLS_runtime_and_R4_full_preservation':tls,'actual359_checkpoint_allold_preservation':checkpoint,'original_R1':{'actual_result_sha256':sha(actual_receipt),'original_status':actual['status'],'complete_records':len(original_r1),'actual_CP_XZ_safeextracts':[1,1],'build_runtime':0,'full704_canonical_projection_matches_actual':True,'Independent_actual_review':{'path':str(ind_actual),'sha256':sha(ind_actual),'scope':'READONLY_FAILURE_PRESERVATION_NOT_RUNTIME_PASS'}},'new_controls':current_controls,'new_distinct':11,'new_executions':11,'inherited_R1_software13_plus_input11_only':inherited,'historical_guard':'NOT_SUPPLIED','old82_controls_rerun':0,'future_exact_design_plan13':True,'budget':{'unit_s':1800,'cleanup_reserve_s':300,'prepare_s':600,'runtime_s':180},'new_namespace':str(actor.layout()['root']),'new_namespace_absent':True,'new_native_targetPython_unpack_COMSOL':0}
(O/'CLOSURE_VERIFICATION.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({k:report[k] for k in ('source_count','AST','parent82_byte_exact','unique_primary_refs','actual_unique_sameFD_checks','new_distinct','new_executions','new_namespace_absent','new_native_targetPython_unpack_COMSOL')}))
