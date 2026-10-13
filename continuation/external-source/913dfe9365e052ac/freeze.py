import ast,datetime,difflib,hashlib,importlib,json,os,stat,sys
from pathlib import Path
import xml.etree.ElementTree as ET
BASE=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2')
REPO=BASE/'repository';E=REPO/'docs/full_project_continuation_v2/evidence/intel-builder-execution-r1'
W=Path('/private/var/folders/4d/6pphmzkx5szf8dyqx7zhqh1r0000gn/T/comsol-openssl359-cpython-r4-firstpin-controls-s61wp2kp')
LOCAL=Path(__file__).parent
def full(p):
 p=Path(p);return p if p.is_absolute() else REPO/p
def raw(p):
 fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW)
 try:
  before=os.fstat(fd);assert stat.S_ISREG(before.st_mode)
  value=bytearray()
  while block:=os.read(fd,1048576):value.extend(block)
  after=os.fstat(fd);named=os.stat(p,follow_symlinks=False)
  fields=lambda s:(s.st_dev,s.st_ino,s.st_uid,s.st_mode,s.st_nlink,s.st_size)
  assert fields(before)==fields(after)==fields(named) and len(value)==before.st_size
  return bytes(value)
 finally:os.close(fd)
def row(p):
 p=full(p);v=raw(p)
 return {'path':str(p.relative_to(REPO)) if p.is_relative_to(REPO) else str(p),'bytes':len(v),'sha256':hashlib.sha256(v).hexdigest()}
def write(p,value):
 with p.open('xb') as out:out.write((json.dumps(value,sort_keys=True,indent=2)+'\n').encode());out.flush();os.fsync(out.fileno())
oldpath=E/'OPENSSL359_CPYTHON_SOURCE_FREEZE_R3.json';old=json.loads(raw(oldpath))
for r in old['source_files']+old['primary_refs']:
 actual=row(r['path']);assert actual['bytes']==r['bytes'] and actual['sha256']==r['sha256'],r['path']
newnames=['openssl359_build_gate_r4.py','openssl359_cpython_checkpoint_r4.py','openssl359_cpython_successor_r4.py','openssl359_cpython_verify_r4.py','test_openssl359_cpython_pin_r4.py']
newrows=[row(E/n) for n in newnames]
sources=old['source_files']+newrows
for r in sources:ast.parse(raw(full(r['path'])),filename=r['path'])
saved=json.loads(raw(W/'sources.json'))
for r in saved:
 assert row(r['path'])['sha256']==r['sha256']
diffs=[]
for new,prior in zip(newnames[:4],['openssl359_build_gate_r3.py','openssl359_cpython_checkpoint_r3.py','openssl359_cpython_successor_r3.py','openssl359_cpython_verify_r3.py']):
 p=LOCAL/(new+'.diff');value=''.join(difflib.unified_diff(raw(E/prior).decode().splitlines(True),raw(E/new).decode().splitlines(True),fromfile=prior,tofile=new))
 with p.open('xb') as out:out.write(value.encode());out.flush();os.fsync(out.fileno())
 diffs.append(row(p))
sys.path.insert(0,str(E));unit=importlib.import_module('openssl359_cpython_successor_r4');x=unit.layout();plan,env=unit.command_plan(x)
assert not x['root'].exists() and len(plan)==6 and unit.UNIT_CAP==1800 and unit.RESERVE==300 and unit.CAPS['prepare']==600
junit=ET.fromstring(raw(W/'raw.junit.xml'));suites=[junit] if junit.tag=='testsuite' else list(junit.iter('testsuite'))
counts={k:sum(int(s.attrib.get(k,0)) for s in suites) for k in ('tests','failures','errors','skipped')};assert counts=={'tests':19,'failures':0,'errors':0,'skipped':0}
ind=BASE/'local-preservation/openssl359-cpython-r3-independent-source-review'
receipt=json.loads(raw(ind/'REVIEW_RECEIPT.json'));assert receipt['status']=='CHANGES_REQUIRED'
design={'schema':'comsol-mcp.openssl359-cpython-source-design/4','created_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'parent_freeze':row(oldpath),'R3_review_preservation':row(ind/'REVIEW_RECEIPT.json'),'R3_actual_residual':row(ind/'ACTUAL_CURRENT_REGULAR_MEMBER_WHOLE_GATE_RESIDUAL.json'),'Root_R3_pending':row(E/'ROOT_OPENSSL359_CPYTHON_SOURCE_CHECK_R3_PENDING.json'),'finding':'OPENSSL359-CP-WHOLE-GATE-REGULAR-PIN-02','repair':'After all first reads/fat parsing, same owned dirFD fixed leaves are reread against FIRST full identity/UID/nlink/mode/size/SHA records. Original report and checkpoint SHA/identity remain immutable actor bytes. After source/copied-input checks, last preinstall consumer matches all four original members, directories and exact views; no gate rerun/new baseline adoption. Drift prevents original cp.run install dispatch.','diffs':diffs,'old56_byte_exact':True,'new5_AST':True,'source_count':61,'plans':[(a,b,str(c)) for a,b,c in plan],'environment':env,'new_root':str(x['root']),'new_root_absent':True,'unit_cap_s':1800,'cleanup_reserve_s':300,'prepare_cap_s':600,'native_commands_after_admission':6,'native_commands_now':0,'official_unpack_now':0,'OpenSSL_lipo_rebuild':0,'CPython_source':'official original 3.12.13 exact archive, zero patch','actual_R2_prerequisite':json.loads(raw(E/'OPENSSL359_CPYTHON_DESIGN_R3.json'))['actual_prerequisite'],'controls':{'root':str(W),'actual_counts':counts,'wholegate':8,'afteraccepted_preinstall_install0':8,'positive_actual_R2_and_checkpoint':1,'metadata_firstrecord_replacement':1,'unchanged_plan_budget':1,'actual_fat_parser_unmocked':True,'native_or_target_runtime_acceptance':False},'inherited_controls':old['tests'],'old_suites_rerun':0,'ownership':'Original reviewed Popen/request/birth/group/wait/raw/retainer unchanged; fd/close/validation errors propagate to original firstFAIL/UNKNOWN handler; permanent fresh guard; no replay.','SBOM_license':'No dependency/source/version/license change from R3; source gate helper only. Original CP3.12.13 and authenticated OpenSSL3.5.9 licenses/whole historical failures preserved.','scope':{'TLS_runtime':'NOT_RUN','Intel_hardware':'NOT_RUN','_lzma':'REQUIRED_OPEN','final_three_arch_delivery':'OPEN'},'admission':'Root and same Independent whole exactfreeze review required before unique fresh six-command native unit'}
designpath=E/'OPENSSL359_CPYTHON_DESIGN_R4.json';write(designpath,design)
refs={str(full(r['path'])):r for r in old['primary_refs']}
def add(p):
 r=row(p);refs[str(full(r['path']))]=r
for p in [oldpath,E/'OPENSSL359_CPYTHON_DESIGN_R3.json',E/'ROOT_OPENSSL359_CPYTHON_SOURCE_CHECK_R3_PENDING.json',designpath,Path(__file__)]:add(p)
for folder in (W,ind,LOCAL):
 for p in folder.iterdir():
  if p.is_file() and not p.is_symlink():add(p)
freeze={k:v for k,v in old.items() if k not in ('source_files','primary_refs','current_design','tests','created_utc')}
freeze.update(schema='comsol-mcp.openssl359-cpython-source-freeze/4',created_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),source_files=sources,primary_refs=list(refs.values()),current_design=row(designpath),tests=[design['controls']],inherited_tests=old['tests'],parent_freeze=row(oldpath),AST_pass_including_inherited=61,old56_byte_exact=True,new_sources=5,distinct_affected_controls=19,actual_executions=19,old_suites_rerun=0,new_root_absent=True,admission=design['admission'])
write(E/'OPENSSL359_CPYTHON_SOURCE_FREEZE_R4.json',freeze)
print(json.dumps({'freeze':row(E/'OPENSSL359_CPYTHON_SOURCE_FREEZE_R4.json'),'design':row(designpath),'sources':len(sources),'primary_refs':len(refs),'counts':counts,'old56_exact':True,'native':0,'R3_review':row(ind/'REVIEW_RECEIPT.json')},indent=2))
