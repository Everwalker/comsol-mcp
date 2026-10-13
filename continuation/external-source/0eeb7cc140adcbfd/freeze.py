import ast,datetime,difflib,hashlib,json,os,sys
from pathlib import Path
import xml.etree.ElementTree as ET
BASE=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2');REPO=BASE/'repository';E=REPO/'docs/full_project_continuation_v2/evidence/intel-builder-execution-r1';LOCAL=Path(__file__).parent
sys.path.insert(0,str(E));import openssl359_cpython_successor_r4 as prior
from intel_crypto_successor_r1 import checkpoint_bytes
import openssl359_runtime_successor_r2 as actor
T=actor.PREFIX.parent;W=T/'comsol-openssl359-runtime-r2-producer-controls-zloaxubs';IND=BASE/'local-preservation/openssl359-runtime-r1-independent-source-review'
def full(p):
 p=Path(p);return p if p.is_absolute() else REPO/p
def row(p):
 p=full(p);r=prior.frozen_digest(p);return dict(path=str(p.relative_to(REPO)) if p.is_relative_to(REPO) else str(p),**r)
def save(p,value):
 with p.open('xb') as f:f.write((json.dumps(value,sort_keys=True,indent=2)+'\n').encode());f.flush();os.fsync(f.fileno())
parent=E/'OPENSSL359_RUNTIME_SOURCE_FREEZE_R1.json';old=json.loads(parent.read_bytes());designold=json.loads((E/'OPENSSL359_RUNTIME_DESIGN_R1.json').read_bytes())
for r in old['source_files']+old['primary_refs']:
 a=row(r['path']);assert a['sha256']==r['sha256'] and a['bytes']==r['bytes'],r['path']
names=['openssl359_runtime_profile_r3.py','openssl359_runtime_successor_r2.py','openssl359_runtime_verify_r2.py','test_openssl359_runtime_producer_r2.py'];new=[row(E/n) for n in names];sources=old['source_files']+new
for r in sources:ast.parse(full(r['path']).read_bytes())
for r in json.loads((W/'sources.json').read_bytes()):assert row(r['path'])['sha256']==r['sha256']
ind=json.loads((IND/'REVIEW_RECEIPT.json').read_bytes());assert ind['status']=='CHANGES_REQUIRED'
assert not actor.ROOT.exists();inputs=actor.verify_inputs();save(LOCAL/'ACTUAL_READONLY_INPUTS_R2.json',inputs)
diffs=[]
for oldname,newname in [('openssl359_runtime_profile_r2.py',names[0]),('openssl359_runtime_successor_r1.py',names[1]),('openssl359_runtime_verify_r1.py',names[2])]:
 diff=''.join(difflib.unified_diff((E/oldname).read_text().splitlines(True),(E/newname).read_text().splitlines(True),fromfile=oldname,tofile=newname));p=LOCAL/(newname+'.diff');p.open('x').write(diff);diffs.append(row(p))
junit=ET.fromstring((W/'raw.junit.xml').read_bytes());ss=[junit] if junit.tag=='testsuite' else list(junit.iter('testsuite'));counts={k:sum(int(s.attrib.get(k,0)) for s in ss) for k in ('tests','failures','errors','skipped')};assert counts=={'tests':3,'failures':0,'errors':0,'skipped':0}
design=dict(designold);design.update(schema='comsol-mcp.openssl359-runtime-only-source-design/2',created_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),parent65_byte_exact=True,AST69=True,only_new4=new,obsolete_parent_AST65='preserved old R1 CHANGES_REQUIRED due actual producer regression',parent_CHREQ=row(IND/'REVIEW_RECEIPT.json'),parent_actual_main_failure=row(IND/'ACTUAL_PRODUCER_MAIN_IMPORTLIB_LOCAL_RESIDUAL.json'),finding='OPENSSL359-RUNTIME-PRODUCER-IMPORTLIB-LOCAL-03',repair='Only move importlib.util import to module scope so producer main uses the original global importlib module. Original provider gate/vector/TLS/required import/cert/MemoryBIO/output and consumer retained. Fresh R2 namespace and source/freeze/review pointers only.',diffs=diffs,new_namespace=str(actor.ROOT),new_namespace_absent=True,commands=[{'argv':['/usr/bin/make','-f',str(actor.ROOT/'Runtime.mk'),'runtime'],'cwd':str(actor.ROOT),'recipe':actor.recipe(),'wait_cap_s':180,'children':'Original selected ARM fixed R4 installed CP profile then fixed host immutable-profile/binary/input verifier'}],software_controls=[{'root':str(W),'actual_counts':counts,'producer_main':True,'host_actual':'knownvector + SSLContext default floor + real required imports','public_boundary_spies':'identity/provider/cert/BIO only; targetruntime0; cannot accept target TLS','guard':'NOT_SUPPLIED'}],distinct_controls=3,actual_executions=3,inherited_controls_preserved=old['controls'],native_source_unpack_compile_target_runtime_now=0)
DP=E/'OPENSSL359_RUNTIME_DESIGN_R2.json';save(DP,design)
refs={str(full(r['path'])):r for r in old['primary_refs']}
def add(p):
 r=row(p);refs[str(full(r['path']))]=r
for p in [parent,E/'OPENSSL359_RUNTIME_DESIGN_R1.json',DP,Path(__file__),Path('/private/tmp/create_openssl359_runtime_r2_sources.py'),Path('/private/tmp/run_openssl359_runtime_r2_producer_controls.py')]:add(p)
for folder in [W,LOCAL,IND]:
 for p in folder.iterdir():
  if p.is_file() and not p.is_symlink():add(p)
freeze=dict(old);freeze.update(schema='comsol-mcp.openssl359-runtime-source-freeze/2',created_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),source_files=sources,primary_refs=list(refs.values()),source_count=69,parent65_byte_exact=True,parent_freeze=row(parent),current_design=row(DP),AST=69,controls=design['software_controls'],inherited_controls=old['controls'],distinct_controls=3,actual_executions=3,new_namespace_absent=True,admission='Root and Independent whole exactfreeze R2 review required before unique fresh runtime-only one-make; R1 never dispatched and cannot be reused')
FP=E/'OPENSSL359_RUNTIME_SOURCE_FREEZE_R2.json';save(FP,freeze);print(json.dumps({'freeze':row(FP),'design':row(DP),'sources':len(sources),'refs':len(refs),'counts':counts,'prior_review':row(IND/'REVIEW_RECEIPT.json')},indent=2))
