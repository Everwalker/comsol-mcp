import ast,datetime,difflib,hashlib,importlib,json,os,stat,sys
from pathlib import Path
import xml.etree.ElementTree as ET
BASE=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2');REPO=BASE/'repository';E=REPO/'docs/full_project_continuation_v2/evidence/intel-builder-execution-r1';LOCAL=Path(__file__).parent
T=Path('/private/var/folders/4d/6pphmzkx5szf8dyqx7zhqh1r0000gn/T');W=T/'comsol-openssl359-cpython-r4-execution-5h135itv';TESTS=[T/'comsol-openssl359-runtime-source-controls-5nkftm_v',T/'comsol-openssl359-runtime-source-controls-91os4a9i']
def full(p):
 p=Path(p);return p if p.is_absolute() else REPO/p
def raw(p):
 fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW)
 try:
  s=os.fstat(fd);assert stat.S_ISREG(s.st_mode);b=bytearray()
  while x:=os.read(fd,1048576):b.extend(x)
  field=lambda s:(s.st_dev,s.st_ino,s.st_uid,s.st_mode,s.st_nlink,s.st_size)
  assert field(s)==field(os.fstat(fd))==field(os.stat(p,follow_symlinks=False)) and len(b)==s.st_size
  return bytes(b)
 finally:os.close(fd)
def row(p):
 p=full(p);b=raw(p);return {'path':str(p.relative_to(REPO)) if p.is_relative_to(REPO) else str(p),'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()}
def save(p,v):
 with p.open('xb') as f:f.write((json.dumps(v,sort_keys=True,indent=2)+'\n').encode());f.flush();os.fsync(f.fileno())
parent=E/'OPENSSL359_CPYTHON_SOURCE_FREEZE_R4.json';old=json.loads(raw(parent))
for r in old['source_files']+old['primary_refs']:
 a=row(r['path']);assert a['sha256']==r['sha256'] and a['bytes']==r['bytes']
names=['openssl359_runtime_profile_r2.py','openssl359_runtime_successor_r1.py','openssl359_runtime_verify_r1.py','test_openssl359_runtime_r1.py'];new=[row(E/n) for n in names];sources=old['source_files']+new
for r in sources:ast.parse(raw(full(r['path'])))
sys.path.insert(0,str(E));actor=importlib.import_module('openssl359_runtime_successor_r1');assert not actor.ROOT.exists();inputs=actor.verify_inputs();save(LOCAL/'ACTUAL_READONLY_FULL_PREREQUISITE_CHECK.json',inputs)
diff=''.join(difflib.unified_diff(raw(E/'openssl359_runtime_profile_r1.py').decode().splitlines(True),raw(E/names[0]).decode().splitlines(True),fromfile='original-immutable-profile-r1',tofile=names[0]));(LOCAL/'PROFILE_DIFF.txt').open('x').write(diff)
controls=[]
for folder in TESTS:
 suite=ET.fromstring(raw(folder/'raw.junit.xml'));ss=[suite] if suite.tag=='testsuite' else list(suite.iter('testsuite'));counts={k:sum(int(s.attrib.get(k,0)) for s in ss) for k in ('tests','errors','failures','skipped')};assert counts['failures']==counts['errors']==0
 controls.append({'root':str(folder),'counts':counts,'guard':'NOT_SUPPLIED; software inputs only, independent guarded review pending','target_runtime_or_compile':0})
assert controls[0]['counts']['tests']==18 and controls[1]['counts']['tests']==2
sourcepins={r['relative_path']:r for r in json.loads(raw(actor.SNAPSHOT)) if r['kind']=='regular' and (r['relative_path'].startswith('runtime-certfixtures/') or r['relative_path']=='cpython-input/Python-3.12.13/Modules/_ssl.c')};assert len(sourcepins)==6
profileinputpins={p:dict(r,path=str(actor.INSTALLED/p)) for p,r in sourcepins.items()}
priors={str(full(r['path'])):r for r in old['primary_refs']}
def add(p):
 r=row(p);priors[str(full(r['path']))]=r
for p in [parent,E/'OPENSSL359_CPYTHON_DESIGN_R4.json',E/'ROOT_OPENSSL359_CPYTHON_SOURCE_REVIEW_R4.json',E/'ROOT_OPENSSL359_CPYTHON_R4_ONCE_DISPATCH_BINDING.json',E/'OPENSSL359_CPYTHON_EXECUTION_RESULT_R4.json',Path(__file__),Path('/private/tmp/create_openssl359_runtime_only_r1.py'),Path('/private/tmp/run_openssl359_runtime_source_controls_r1.py'),Path('/private/tmp/run_openssl359_runtime_source_controls_r2.py')]:add(p)
for folder in [W,LOCAL,*TESTS,actor.INSTALLED,actor.INSTALLED/'logs']:
 for p in folder.iterdir():
  if p.is_file() and not p.is_symlink():add(p)
for row_ in profileinputpins.values():add(row_['path'])
for name in ['REVIEW_RECEIPT.json','CLOSURE_VERIFICATION.json','SEMANTIC_REVIEW.json']:
 add(BASE/'local-preservation/openssl359-cpython-r4-independent-source-review'/name)
design={'schema':'comsol-mcp.openssl359-runtime-only-source-design/1','created_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'parent61_byte_exact':True,'AST65':True,'only_new4':new,'original_actual_R4_failure':row(E/'OPENSSL359_CPYTHON_EXECUTION_RESULT_R4.json'),'R4_whole14215_snapshot':row(actor.SNAPSHOT),'R4_root_identity':json.loads(raw(W/'COMPLETE_OPENSSL359_CP_R4_ROOT_IDENTITY.json')),'R4_readonly_first_inputs':inputs,'fixed_profile_fixture_source_pins':profileinputpins,'fixed_r4_installed_prefix':str(actor.INSTALLED/'cpython'),'new_namespace':str(actor.ROOT),'new_namespace_absent':True,'provider_exact':{'INFO':[3,5,0,9,0],'NUMBER':0x30500090,'TEXT':'OpenSSL 3.5.9 29 Sep 2026','first_read_diagnostics':'actual three fields printed before exact gate; refusal details include same first values; success stores same values'},'changes':'Correct version gate; separate fixed installed/input R4 root from fresh output namespace. All original vector/defaultTLS/module/cert/MemoryBIO/protocol gates retained. No CP/OpenSSL source patch, no rebuild. Binary verifier consumes first exact new profile record, preserves hash through installed byte/tree reads, and accepts only original fixed installed3fat binaries.','commands':[{'argv':['/usr/bin/make','-f',str(actor.ROOT/'Runtime.mk'),'runtime'],'cwd':str(actor.ROOT),'recipe':actor.recipe(),'wait_cap_s':180,'children':'first actual selected ARM installed CP -I -B profile, second fixed host Python binary/profile/full-input verifier; original make Popen/birth/group protocol'}],'budgets':{'unit':1800,'cleanup_reserve':300,'prepare':600,'runtime_including_verification':180},'first_FAIL_UNKNOWN':'original cp.run/samehandle/retainer/fences; no retry or replay. First failures and fd close errors propagate original diagnostics. Whole R4+original43+alloldtree preservation independent bool never status-derived.','software_controls':controls,'distinct_controls':19,'actual_executions':20,'source_control_failure':0,'R4_5PASS_not_replayed':True,'native_source_unpack_compile_target_runtime_now':0,'scope':{'TLS_hashlib_runtime':'NOT_RUN_NEW; R4 failure preserved','_lzma':'REQUIRED_OPEN','gdbm_tk':'original diagnostic retained; Root product finite-scope interpretation inherited, no new dependency gate','Intel_hardware':'NOT_RUN','full_three_arch_runtime_release':'OPEN'},'SBOM_license':'same authenticated OpenSSL3.5.9/original CP3.12.13; no source/dependency/license changes; original4.0 crypto evidence separate','admission':'Root and Independent whole exactfreeze required before unique one-make runtime-only actual execution'}
DP=E/'OPENSSL359_RUNTIME_DESIGN_R1.json';save(DP,design);add(DP)
freeze={'schema':'comsol-mcp.openssl359-runtime-source-freeze/1','created_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'source_files':sources,'primary_refs':list(priors.values()),'original_archive_inputs':old['original_archive_inputs'],'current_design':row(DP),'parent_freeze':row(parent),'source_count':65,'parent61_byte_exact':True,'AST':65,'controls':controls,'distinct_controls':19,'actual_executions':20,'native_commands_now':0,'native_commands_after_admission':1,'new_namespace_absent':True,'unit_cap_s':1800,'cleanup_reserve_s':300,'runtime_wait_s':180,'prepare_s':600,'acceptance':design['scope'],'admission':design['admission']};FP=E/'OPENSSL359_RUNTIME_SOURCE_FREEZE_R1.json';save(FP,freeze);print(json.dumps({'freeze':row(FP),'design':row(DP),'sources':len(sources),'primary_refs':len(priors),'controls':controls},indent=2))
