from pathlib import Path
import sys,json,hashlib,ast,difflib,xml.etree.ElementTree as ET
BASE=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2');REPO=BASE/'repository'
E=REPO/'docs/full_project_continuation_v2/evidence/intel-builder-execution-r1';OUT=Path(__file__).parent
sys.path[:0]=[str(E),str(REPO)]
import openssl359_cpython_successor_r4 as unit
import openssl359_cpython_checkpoint_r4 as checkpoint
PIN='ce38de67f4f2e0d07fbeef49357c7f3f1608bfc242288f369cae0d0296742e36'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def resolve(v):
 p=Path(v);return p if p.is_absolute() else REPO/p
fpath=E/'OPENSSL359_CPYTHON_SOURCE_FREEZE_R4.json';assert sha(fpath)==PIN
f=json.loads(fpath.read_text());parent=json.loads(resolve(f['parent_freeze']['path']).read_text())
assert sha(resolve(f['parent_freeze']['path']))==f['parent_freeze']['sha256']=='d0af72129702ada2c8eb14ac43cdb92c7edc63a796cdca685131b86ec6d17b34'
current={row['path']:row for row in f['source_files']};old={row['path']:row for row in parent['source_files']}
assert len(current)==61 and len(old)==56 and all(current[k]==v for k,v in old.items())
new=[k for k in current if k not in old];assert len(new)==5
checked=[]
for row in f['source_files']+f['primary_refs']+[f['current_design']]:
 p=resolve(row['path']);assert unit.frozen_digest(p)=={'bytes':row['bytes'],'sha256':row['sha256']};checked.append(str(p))
for row in f['source_files']:ast.parse(resolve(row['path']).read_text(),filename=row['path'])
assert len(f['primary_refs'])==1062 and sha(resolve(f['current_design']['path']))=='07b9765fd3f591e905ccc1af479a8db0cf912674989acd46c8bbfcf123363582'
diffs=[]
for name in new:
 path=resolve(name)
 if path.name=='test_openssl359_cpython_pin_r4.py':
  diffs.append({'path':str(path),'new_affected_tests':True,'source_sha256':sha(path)});continue
 previous=path.with_name(path.name.replace('_r4','_r3'));assert str(previous.relative_to(REPO)) in old
 delta=''.join(difflib.unified_diff(previous.read_text().splitlines(True),path.read_text().splitlines(True),fromfile=str(previous),tofile=str(path)))
 dest=OUT/(path.name+'.independent.diff');dest.write_text(delta)
 diffs.append({'path':str(path),'parent':str(previous),'source_sha256':sha(path),'delta':str(dest),'delta_sha256':sha(dest)})
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
 guards=[]
 for g in root.glob('*.jsonl'):
  rows=[json.loads(line) for line in g.read_text().splitlines()]
  active=sum(row.get('event')=='GUARD_ACTIVE_BEFORE_CHILD_ENTRYPOINT' for row in rows)
  denied=sum('DENIED' in row.get('event','') for row in rows)
  guards.append({'path':str(g),'sha256':sha(g),'ACTIVE':active,'DENY':denied})
 assert not guards
 tests.append({'root':str(root),'passed':len(cases),'case_names':selected,'JUnit_sha256':sha(root/'raw.junit.xml'),'guards':guards,'guard_scope':'No per-execution strict-guard log supplied for frozen current19; argv is plain pytest, not promoted to guarded/native execution'})
assert len(names)==19 and len(set(names))==19
inherited=[]
for item in f['inherited_tests']:
 root=Path(item['root']);cases=ET.parse(root/'raw.junit.xml').findall('.//testcase')
 assert len(cases)==item['actual_counts']['tests'] and not any(node.tag in {'failure','error','skipped'} for case in cases for node in case)
 inherited.append({'root':str(root),'executions':len(cases),'JUnit_sha256':sha(root/'raw.junit.xml')})
assert sum(item['executions'] for item in inherited)==13
plan,env=unit.command_plan(unit.layout());assert len(plan)==6
assert not unit.layout()['root'].exists() and not unit.layout()['root'].is_symlink()
root_disposition=E/'ROOT_OPENSSL359_CPYTHON_FINDING_DISPOSITION_R3.json'
assert sha(root_disposition)=='bf535101efd3a9dfab6e813c8cc4ec7aa8db7e3f31c288200f3d5713b5ccc819'
report={'status':'PASS_EXACT_SOURCE_INPUT_HISTORICAL_CONTROL_CLOSURE','freeze':str(fpath),'freeze_sha256':PIN,
 'source_count':61,'parent56_exact':True,'new5_sources':new,'AST':61,'primary_unique_refs':1062,
 'actual_unique_same_FD_checks':len(set(checked)),'original43_actual_guard':inputs,'new5_deltas':diffs,
 'actual_R2_complete_checkpoint':actual['r2'],'inherited_old_source_and_failed_tree_preservation':actual['old'],
 'actual11_original_command_closure':actual['commands'],'actual359_libraries':libraries,'all_objects':objects,
 'header_total_records':143,'header_regular_records':sum(row['kind']=='regular' for row in actual['headers']),
 'historical_affected_controls':tests,'distinct':19,'actual_executions':19,'old56_suite_rerun':False,
 'R3_inherited_only':{'distinct':12,'executions':13,'raw':inherited,'rerun':False},
 'six_CPP_plan':[{'label':label,'argv':argv,'cwd':str(cwd)} for label,argv,cwd in plan],
 'env':env,'new_root_absent':True,'unit_cap_s':1800,'cleanup_reserve_s':300,'prepare_cap_s':600,
 'Root_R3_disposition_external':{'path':str(root_disposition),'sha256':sha(root_disposition)},
 'new_official_unpack_compile_runtime_COMSOL':0,'acceptance':'PENDING_INDEPENDENT_SEMANTIC_RESULT'}
(OUT/'CLOSURE_VERIFICATION.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({'source':61,'parent56_exact':True,'new':5,'AST':61,'unique_refs':len(set(checked)),
 'raw_distinct':19,'raw_executions':19,'actual_checkpoint_records':29154,'objects':objects,'new_native':0}))
