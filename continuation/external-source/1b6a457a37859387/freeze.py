import ast,datetime,difflib,json,os,sys
from pathlib import Path
import xml.etree.ElementTree as ET
BASE=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2');REPO=BASE/'repository';E=REPO/'docs/full_project_continuation_v2/evidence/intel-builder-execution-r1';LOCAL=Path(__file__).parent;sys.path.insert(0,str(E))
import liblzma_successor_r2 as actor
import liblzma_prepare_r2 as prepare
import openssl359_cpython_successor_r4 as prior
T=actor.PREFIX.parent;W=T/'comsol-liblzma-r2-pristine-reuse-controls-hgnvvj6n';C=T/'comsol-liblzma-r1-failure-capture-u3nxv3ir';RUN=T/'comsol-liblzma-r1-execution-_k151oo6'
def full(p):
 p=Path(p);return p if p.is_absolute() else REPO/p
def row(p):
 p=full(p);return dict(path=str(p.relative_to(REPO)) if p.is_relative_to(REPO) else str(p),**prior.frozen_digest(p))
def save(p,v):
 with p.open('xb') as f:f.write((json.dumps(v,sort_keys=True,indent=2)+'\n').encode());f.flush();os.fsync(f.fileno())
parent=E/'LIBLZMA_SOURCE_FREEZE_R1.json';old=json.loads(parent.read_bytes());previous=json.loads((E/'LIBLZMA_DESIGN_R1.json').read_bytes())
for r in old['source_files']+old['primary_refs']:
 a=row(r['path']);assert a['bytes']==r['bytes'] and a['sha256']==r['sha256'],r['path']
stems=['liblzma_successor','liblzma_prepare','liblzma_cpython_checkpoint','liblzma_merge','liblzma_runtime_profile','liblzma_verify','liblzma_acceptance'];names=[n+'_r2.py' for n in stems]+['test_liblzma_pristine_reuse_r2.py'];new=[row(E/n) for n in names];sources=old['source_files']+new;assert len(sources)==90
for r in sources:ast.parse(full(r['path']).read_bytes())
for r in json.loads((W/'sources.json').read_bytes()):assert row(r['path'])['sha256']==r['sha256']
failed=prepare.verify_failed_r1();tls=prepare.verify_tls_runtime();auth=prepare.fixed.verify_inputs_and_scan();assert len(failed)==5799
save(LOCAL/'ACTUAL_FULL_INPUT_AND_FAILED_ROOT_READONLY_CHECK.json',{'original_failed5799_unchanged':True,'fixed_failed_snapshot':prepare.FAILED_PIN,'original_XZ704':auth['inventory'],'accepted_TLS':tls,'failed_root_identity':json.loads((C/'COMPLETE_FAILED_ROOT_IDENTITY.json').read_bytes())})
x=actor.layout();assert not x['root'].exists();plan,env=actor.command_plan(x);assert len(plan)==13
junit=ET.fromstring((W/'raw.junit.xml').read_bytes());ss=[junit] if junit.tag=='testsuite' else list(junit.iter('testsuite'));counts={k:sum(int(s.attrib.get(k,0)) for s in ss) for k in ('tests','failures','errors','skipped')};assert counts=={'tests':11,'failures':0,'errors':0,'skipped':0}
diffs=[]
for stem in stems:
 p=LOCAL/(stem+'_r2.py.diff');value=''.join(difflib.unified_diff((E/(stem+'_r1.py')).read_text().splitlines(True),(E/(stem+'_r2.py')).read_text().splitlines(True),fromfile=stem+'_r1.py',tofile=stem+'_r2.py'));p.open('x').write(value);diffs.append(row(p))
design=dict(previous);design.update(schema='comsol-mcp.liblzma-static-universal2-source-design/2',created_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),parent82_byte_exact=True,sources_AST=90,extensions={'seven_fixed_namespace_source_variants':7,'one_affected_control_source':1},original_R1_first_failure=row(E/'LIBLZMA_EXECUTION_RESULT_R1.json'),original_failed5799_snapshot=row(prepare.FAILED_SNAPSHOT),original_failed_root_identity=json.loads((C/'COMPLETE_FAILED_ROOT_IDENTITY.json').read_bytes()),whole704_order_diagnosis=row(C/'WHOLE_704_ORDER_ONLY_FIRST_FAILURE_DIAGNOSIS.json'),sort_repair='Only canonical sort each full projected/expected list by complete relative_path string, then full list equality; no mapping collapse, duplicate deletion, member subset or mode/hash waiver.',pristine_reuse='Original R1 xz-input complete704 official extracted content first matches unchanged Auth3+whole original listing; original5799 failedtree/rootidentity/receipt/snapshot sameFD pinned before/after; existing copy_exact_tree first member UID/dev/inode/mode/nlink/size/SHA records copies complete XZ704 into fresh R2 input, then exact copies to two archSource trees. No repeated XZ safeunpack. CP remains fresh official same archive0patch safeextract; no second input source modification.',original_R1_unpack_effects={'XZ_official_safeextract':1,'CP_official_prerequisite_safeextract':1,'native_outer_commands':0,'actual_build':0,'actual_targetruntime':0},new_root=str(x['root']),new_root_absent=True,plan=[{'label':label,'argv':argv,'cwd':str(cwd),'environment':actor.stage_environment(label,env),'stage_cap_s':actor.CAPS[label]} for label,argv,cwd in plan],new_native_targetruntime_unpack=0,affected_controls=[{'root':str(W),'counts':counts,'production':'actual tree_record Path ordering vs whole relative string positive, bytes/mode/memberadd/membermissing/duplicateextra/duplicatereplacement/kind negatives, actual original704 pinned production copier positive, changed first-source inode copier rejection, all seven namespace/pointer/plan controls','guard':'NOT_SUPPLIED','actual_native_official_unpack_targetruntime':0}],new_distinct_controls=11,new_actual_executions=11,inherited_R1_controls=old['controls'],inherited_R1_controls_rerun=0,diffs=diffs,whole_preservation='Original failed R1 full5799/rootID/originalSHA plus all previous TLS17/R414215/43/oldtrees before/after, immutable originalfail/raw/guards remain. Actual copy source read-only; same original owner/retainer no replay. Fixed fresh R2 namespace. Root/Independent exactwhole review before native.',admission='Root and Independent whole R2 exactfreeze review plus actual R1 failure/source preservation before unique fresh13command unit')
design['archive_contract']=dict(previous['archive_contract'],production_extraction='No new XZ extraction; sameFD Auth3 + original unchanged official704 listing matches pinned R1 extractedinput all members/fields, strict original copier copies to fresh R2 input/twoarch sources. Original R1 raw archive and normalized modes separately retained. CP only still fresh original safeextract.')
DP=E/'LIBLZMA_DESIGN_R2.json';save(DP,design)
refs={str(full(r['path'])):r for r in old['primary_refs']}
def add(p):
 r=row(p);refs[str(full(r['path']))]=r
for p in [parent,E/'LIBLZMA_DESIGN_R1.json',DP,Path(__file__),E/'LIBLZMA_EXECUTION_RESULT_R1.json',E/'ROOT_LIBLZMA_SOURCE_REVIEW_R1.json',E/'ROOT_LIBLZMA_R1_ONCE_DISPATCH_BINDING.json',Path('/private/tmp/create_liblzma_r2_source_variants.py'),Path('/private/tmp/run_liblzma_pristine_reuse_controls_r2.py'),Path('/private/tmp/prepare_liblzma_failure_capture_r1.py')]:add(p)
for folder in [W,C,RUN,LOCAL,BASE/'local-preservation/liblzma-r1-independent-source-review']:
 for p in folder.iterdir():
  if p.is_file() and not p.is_symlink():add(p)
freeze=dict(old);freeze.update(schema='comsol-mcp.liblzma-source-freeze/2',created_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),source_files=sources,primary_refs=list(refs.values()),source_count=90,AST=90,parent82_byte_exact=True,parent_freeze=row(parent),current_design=row(DP),new_variants_helpers_tests=8,controls=design['affected_controls'],inherited_R1_controls=old['controls'],inherited_controls_rerun=0,new_distinct_controls=11,new_actual_executions=11,new_native_commands=0,new_official_source_unpack=0,new_root_absent=True,admission=design['admission']);FP=E/'LIBLZMA_SOURCE_FREEZE_R2.json';save(FP,freeze);print(json.dumps({'freeze':row(FP),'design':row(DP),'sources':len(sources),'refs':len(refs),'new_controls':counts,'failed5799_unchanged':True,'new_native_unpack_targetruntime':0},indent=2))
