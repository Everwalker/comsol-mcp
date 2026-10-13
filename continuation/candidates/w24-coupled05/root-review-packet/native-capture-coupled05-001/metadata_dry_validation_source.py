from pathlib import Path
import json,os,sys,hashlib,subprocess,shutil,tempfile
B=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2');C=B/'local-preservation/w24-coupled-science-20261013-candidate05';R=C/'repository';O=C/'root-review-packet/native-capture-coupled05-001';P=O/'NATIVE_CAPTURE_INVOCATION_W24_COUPLED05_ACTIVITY001.json';p=json.loads(P.read_bytes());sys.path.insert(0,str(R));from tools import run_native_w24_cure_preflight as x
sha=lambda q:hashlib.sha256(q.read_bytes()).hexdigest();ref=lambda q:{'path':str(q),'sha256':sha(q),'bytes':q.stat().st_size};closure=json.loads((C/'root-review-packet/SOURCE_CLOSURE_COUPLED_SETUP05.json').read_bytes());before={i['path']:sha(Path(i['path'])) for i in closure['sources']};assert all(before[i['path']]==i['sha256'] for i in closure['sources']);actual=x._verify_offline_candidate_freeze(Path(p['candidate_freeze']['path']),p['candidate_freeze']['sha256']);assert actual['birth_budget_s']==1800 and actual['cleanup_reserve_s']==600
z=subprocess.run(['/bin/zsh','-n',p['capture_script']['path']],capture_output=True,text=True,timeout=5);assert z.returncode==0
(O/'zsh-syntax.stdout.txt').write_text(z.stdout);(O/'zsh-syntax.stderr.txt').write_text(z.stderr)
class HandoffNotExecuted(Exception):pass
calls=[];real=x._verify_offline_candidate_freeze
def verified_handoff(fp,h):
 v=real(fp,h);calls.append({'path':str(fp),'sha256':h,'actual_freeze_validated':True});raise HandoffNotExecuted('explicit stop before production work/evidence mkdir, runtime/OS/native paths NOT_EXECUTED')
x._verify_offline_candidate_freeze=verified_handoff;sys.argv=p['argv'][1:]
try:x.main()
except HandoffNotExecuted:pass
else:raise AssertionError('missing explicit pre-effect boundary')
assert len(calls)==1
assert all(not Path(p['paths'][k]).exists() and not Path(p['paths'][k]).is_symlink() for k in ['work','native_evidence','exclusive_capture_directory','approval_expected'])
# Negative controls exercise existing actual run guard, not a copied path helper.
from types import SimpleNamespace
scratch=Path(tempfile.mkdtemp(prefix='comsol-mcp-w24-cure-prebirth-software-',dir='/private/tmp'));badpaths=[('/private/tmp/outside-w24-work',ValueError),(str(scratch),FileExistsError)]
link=scratch/'link';link.symlink_to(scratch,target_is_directory=True);badpaths.append((str(link),FileExistsError));neg=[]
try:
 for work,error in badpaths:
  n=len(calls)
  try:x.run(SimpleNamespace(work=work,evidence=p['paths']['native_evidence'],candidate_freeze=p['candidate_freeze']['path'],expected_candidate_sha256=p['candidate_freeze']['sha256']))
  except error as e:neg.append({'work':work,'error':type(e).__name__,'verification_calls':len(calls)-n,'effects':'NOT_REACHED'})
  else:raise AssertionError('guard accepted invalid path')
  assert len(calls)==n
finally:link.unlink();scratch.rmdir()
sourceafter={s:sha(Path(s)) for s in before};assert before==sourceafter
paths=[]
for k in ['work','native_evidence','exclusive_capture_directory','approval_expected']:
 path=Path(p['paths'][k]);ancestors=[]
 for a in reversed(path.parents):
  if a.exists():
   st=a.lstat();assert not a.is_symlink();ancestors.append({'path':str(a),'uid':st.st_uid,'device':st.st_dev,'inode':st.st_ino})
 paths.append({'kind':k,'path':str(path),'absent':not path.exists(),'not_symlink':not path.is_symlink(),'existing_ordinary_ancestors':ancestors})
resume=B/'repository/docs/full_project_continuation_v2/state/RESUME.json';rs=json.loads(resume.read_bytes());assert rs['active_jobs']==[]
r={'status':'PASS_SOURCE_PRESERVING_METADATA_SOFTWARE_PRE_EFFECT_ONLY_NOT_NATIVE_ENTRY_ACCEPTANCE','production_freeze_validator':{'function':'tools.run_native_w24_cure_preflight._verify_offline_candidate_freeze','result':actual},'production_CLI_parse_and_actual_run_pre_effect_guard':{'argv':p['argv'],'actual_calls':calls,'explicit_software_boundary':'real production freeze validated then sentinel before mkdir; no later runtime/OS/birth evaluated','negative_controls':neg},'zsh_syntax':{'argv':['/bin/zsh','-n',p['capture_script']['path']],'returncode':z.returncode,'stdout':ref(O/'zsh-syntax.stdout.txt'),'stderr':ref(O/'zsh-syntax.stderr.txt')},'capture_contract':'exact shell text setopt noclobber and exclusive new mkdir; actual O_EXCL runtime NOT_RUN','approval_validation':'Root blueprint reference only; W24 has no internal approval validator. Approval expected absent. External actor/CAS/human/normal auto-review must all admit later.','paths':paths,'source_closure_count':len(before),'source_hashes_unchanged':before==sourceafter,'RESUME_readonly':ref(resume),'active_jobs':rs['active_jobs'],'native_CAS_PROGRESS':'NOT_RUN_NOT_CHANGED'}
q=O/'METADATA_DRY_VALIDATION_REFERENCE_ONLY.json';q.write_text(json.dumps(r,indent=2,default=str)+'\n');print(json.dumps({'receipt':ref(q),'source_count':len(before),'native':'NOT_RUN','CAS':'NOT_CHANGED'}))
