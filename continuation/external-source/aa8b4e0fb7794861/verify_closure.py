from pathlib import Path
import sys,json,hashlib,ast,xml.etree.ElementTree as ET
BASE=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2')
REPO=BASE/'repository';E=REPO/'docs/full_project_continuation_v2/evidence/intel-builder-execution-r1'
OUT=Path(__file__).parent
sys.path[:0]=[str(E),str(REPO)]
import openssl359_successor_r1 as unit
import openssl359_sources_r1 as sources
PIN='f6430ea1eada788eb5d128e9b150ac0580ca0c8414c745eba84467bdee0b02a5'
def path(v):
 p=Path(v);return p if p.is_absolute() else REPO/p
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
freeze=E/'OPENSSL359_SOURCE_FREEZE_R1_1.json';assert sha(freeze)==PIN
f=json.loads(freeze.read_text());old=json.loads((E/'TLS_CPYTHON_SOURCE_FREEZE_R4.json').read_text())
current={r['path']:r for r in f['source_files']};parent={r['path']:r for r in old['source_files']}
assert len(current)==46 and len(parent)==39
assert all(current[k]==v for k,v in parent.items())
new=[k for k in current if k not in parent];assert len(new)==7
checked=[]
for row in f['source_files']+f['primary_refs']+[f['preimage_R1_freeze'],f['current_design']]:
 p=path(row['path']);assert unit.frozen_digest(p)=={'bytes':row['bytes'],'sha256':row['sha256']};checked.append(str(p))
for row in f['source_files']:ast.parse(path(row['path']).read_text(),filename=row['path'])
assert len(f['primary_refs'])==551
archives=unit.verify_original_archives(f);auth=sources.authenticated_content()
assert archives['archives']==43 and auth['members']==6078 and auth['expanded_bytes']==131230833
preservation=sources.preservation_verified_summary()
assert preservation['r4']['old_failed_r4_unchanged'] and preservation['r4']['records']==14185
tests=[];names=[]
for item in f['tests']:
 root=Path(item['root']);cases=ET.parse(root/'raw.junit.xml').findall('.//testcase')
 assert len(cases)==item['actual_counts']['tests'] and not any(node.tag in {'failure','error','skipped'} for case in cases for node in case)
 cases_names=[case.attrib.get('name') for case in cases];names+=cases_names
 tests.append({'root':str(root),'passed':len(cases),'case_names':cases_names,
   'actual_result':json.loads((root/'result.json').read_text()),'JUnit_sha256':sha(root/'raw.junit.xml')})
assert len(names)==15 and len(set(names))==14
plan,env=unit.command_plan(unit.layout());assert len(plan)==13
assert not unit.layout()['root'].exists() and not unit.layout()['root'].is_symlink()
correction=json.loads((E/'OPENSSL359_METADATA_CORRECTION_R1_1.json').read_text())
report={'status':'PASS_EXACT_FROZEN_SOURCE_INPUT_AND_HISTORICAL_CONTROL_CLOSURE',
 'freeze':str(freeze),'freeze_sha256':PIN,'repository':str(REPO),'source_count':46,'parent39_byte_exact':True,
 'new7_sources':new,'AST':46,'primary_unique_refs':551,'actual_unique_same_FD_file_checks':len(set(checked)),
 'original43_actual_production_same_FD_guard':archives,'actual_source_authentication':
  {'source_sha256':sources.SOURCE_PIN['sha256'],'bytes':sources.SOURCE_PIN['bytes'],
   'members':auth['members'],'expanded_bytes':auth['expanded_bytes'],'links':auth['links'],
   'actual_input_only':'Historical authenticated source inputs rechecked, no new acquisition/unpack/compile'},
 'original_preservation':preservation,'historical_tests':tests,'distinct_new_controls':14,'actual_control_executions':15,
 'new_root_absent':True,'13_outer_plan':[{ 'label':label,'argv':argv,'cwd':str(cwd)} for label,argv,cwd in plan],
 'environment':env,'R1_original_correction_preserved':correction,'native_runtime_hardware':'NOT_RUN',
 'source_acceptance':'PENDING_SEMANTIC_AND_INDEPENDENT_SOFTWARE_REVIEW'}
(OUT/'CLOSURE_VERIFICATION.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({'closure':'PASS','source46':True,'same39':True,'refs':len(set(checked)),
 'original43':43,'raw_controls':15,'distinct':14,'new_root_absent':True,'actual_unpack_or_native':0}))
