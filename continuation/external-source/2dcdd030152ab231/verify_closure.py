from pathlib import Path
import sys,json,hashlib,ast,xml.etree.ElementTree as ET
BASE=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2');REPO=BASE/'repository'
E=REPO/'docs/full_project_continuation_v2/evidence/intel-builder-execution-r1';OUT=Path(__file__).parent
sys.path[:0]=[str(E),str(REPO)]
import openssl359_cpython_successor_r3 as unit
import openssl359_cpython_checkpoint_r3 as checkpoint
PIN='d0af72129702ada2c8eb14ac43cdb92c7edc63a796cdca685131b86ec6d17b34'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def resolve(v):
 p=Path(v);return p if p.is_absolute() else REPO/p
fpath=E/'OPENSSL359_CPYTHON_SOURCE_FREEZE_R3.json';assert sha(fpath)==PIN
f=json.loads(fpath.read_text());parent=json.loads((E/'OPENSSL359_SOURCE_FREEZE_R2.json').read_text())
current={row['path']:row for row in f['source_files']};old={row['path']:row for row in parent['source_files']}
assert len(current)==56 and len(old)==51 and all(current[k]==v for k,v in old.items())
new=[k for k in current if k not in old];assert len(new)==5
checked=[]
for row in f['source_files']+f['primary_refs']+[f['current_design']]:
 p=resolve(row['path']);assert unit.frozen_digest(p)=={'bytes':row['bytes'],'sha256':row['sha256']};checked.append(str(p))
for row in f['source_files']:ast.parse(resolve(row['path']).read_text(),filename=row['path'])
assert len(f['primary_refs'])==1016
inputs=unit.verify_original_archives(f)
actual=checkpoint.verify_checkpoint();assert actual['r2']['records']==29154 and len(actual['commands'])==11
assert len(actual['headers'])==143
libraries=[];objects=0
for item in actual['artifacts']:
 slices={arch:part['object_count'] for arch,part in item['slices']['slices'].items()}
 objects+=sum(slices.values());libraries.append({'name':item['name'],'file':item['original'],'objects':slices})
assert objects==2221 and len(libraries)==2
tests=[];names=[]
for item in f['tests']:
 root=Path(item['root']);cases=ET.parse(root/'raw.junit.xml').findall('.//testcase')
 assert len(cases)==item['actual_counts']['tests'] and not any(node.tag in {'failure','error','skipped'} for case in cases for node in case)
 selected=[case.attrib.get('name') for case in cases];names+=selected
 tests.append({'root':str(root),'passed':len(cases),'case_names':selected,'JUnit_sha256':sha(root/'raw.junit.xml')})
assert len(names)==13 and len(set(names))==12
plan,env=unit.command_plan(unit.layout());assert len(plan)==6
assert not unit.layout()['root'].exists() and not unit.layout()['root'].is_symlink()
report={'status':'PASS_EXACT_SOURCE_INPUT_HISTORICAL_CONTROL_CLOSURE','freeze':str(fpath),'freeze_sha256':PIN,
 'source_count':56,'parent51_exact':True,'new5_sources':new,'AST':56,'primary_unique_refs':1016,
 'actual_unique_same_FD_checks':len(set(checked)),'original43_actual_guard':inputs,
 'actual_R2_complete_checkpoint':actual['r2'],'inherited_old_source_and_failed_tree_preservation':actual['old'],
 'actual11_original_command_closure':actual['commands'],'actual359_libraries':libraries,'all_objects':objects,
 'header_total_records':143,'header_regular_records':sum(row['kind']=='regular' for row in actual['headers']),
 'historical_affected_controls':tests,'distinct':12,'actual_executions':13,'old51_suite_rerun':False,
 'six_CPP_plan':[{'label':label,'argv':argv,'cwd':str(cwd)} for label,argv,cwd in plan],
 'env':env,'new_root_absent':True,'unit_cap_s':1800,'cleanup_reserve_s':300,'prepare_cap_s':600,
 'new_official_unpack_compile_runtime_COMSOL':0,'acceptance':'PENDING_INDEPENDENT_SEMANTIC_RESULT'}
(OUT/'CLOSURE_VERIFICATION.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({'source':56,'parent51_exact':True,'new':5,'AST':56,'unique_refs':len(set(checked)),
 'raw_distinct':12,'raw_executions':13,'actual_checkpoint_records':29154,'objects':objects,'new_native':0}))
