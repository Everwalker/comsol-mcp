from pathlib import Path
import ast,hashlib,json,difflib,struct,xml.etree.ElementTree as ET
ROOT=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2');OUT=Path(__file__).resolve().parent
CANDIDATE=ROOT/'local-preservation/w24-coupled-science-20261013-candidate05';PACKET=CANDIDATE/'root-review-packet/ROOT_REVIEW_PACKET_W24_COUPLED_SETUP05.json'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
assert sha(PACKET)=='72ede2dba6e776cfb32d292a396408e9042d8a9d8431a180dcea98033e074d60'
packet=json.loads(PACKET.read_text());F=Path(packet['freeze']['path']);assert sha(F)=='343a7a1dec2aeafee5758b0d2ce5182a28ab69ad1c1538840c55e133ab2b2afc'
freeze=json.loads(F.read_text());refs={};snapshot_drift=[]
def verify_refs(obj):
 if isinstance(obj,dict):
  if isinstance(obj.get('path'),str) and isinstance(obj.get('sha256'),str):
   p=Path(obj['path']);assert p.is_file(),str(p)
   if p==ROOT/'repository/docs/full_project_continuation_v2/state/RESUME.json' and sha(p)!=obj['sha256']:
    snapshot_drift.append({'path':str(p),'historical_snapshot_sha256':obj['sha256'],'current_readonly_sha256':sha(p)})
   else:
    assert sha(p)==obj['sha256'],str(p)
    if 'bytes' in obj:assert p.stat().st_size==obj['bytes'],str(p)
    refs[str(p)]=obj['sha256']
  for value in obj.values():verify_refs(value)
 elif isinstance(obj,list):
  for value in obj:verify_refs(value)
verify_refs(packet);verify_refs(freeze)
binding=CANDIDATE/'root-review-packet/FINAL_GUARDED_VALIDATOR_AND_HISTORY_BINDING_COUPLED05.json'
assert sha(binding)=='42cc89bcc06661dfb715133da0578c7b175962b7e521edc64b4e4768a5f0f2de';verify_refs(json.loads(binding.read_text()))
validator=CANDIDATE/'root-review-packet/PRODUCTION_FREEZE_VALIDATOR_RECEIPT_GUARDED.json'
assert sha(validator)=='b9004a1e06e377506350823fbc040cc7891d8334c14da4124e47e123962b5abd';verify_refs(json.loads(validator.read_text()))
closure=json.loads(Path(packet['source_closure']['path']).read_text());verify_refs(closure)
parent=json.loads(Path(closure['parent']['path']).read_text());verify_refs(parent)
P=Path(parent['parent_freeze']['path']);assert sha(P)==parent['parent_freeze']['sha256'];verify_refs(json.loads(P.read_text()))
rows=closure['sources'];assert len(rows)==201
source_counts={k:sum(x['status']==k for x in rows) for k in ['modified','added','unchanged']};assert source_counts=={'modified':5,'added':1,'unchanged':195}
assert len(parent['sources'])==200
diffs=closure['exact_diffs'];assert len(diffs)==6;by_path={x['relative_path']:x for x in rows}
for diff in diffs:
 row=by_path[diff['relative_path']];p=Path(row['path']);par=row.get('parent')
 a=Path(par['path']).read_text().splitlines(keepends=True) if par else []
 # Actual byte parents and frozen diff labels are coupled04 -> coupled05.
 expected=''.join(difflib.unified_diff(a,p.read_text().splitlines(keepends=True),fromfile='candidate04/'+row['relative_path'] if par else '/dev/null',tofile='candidate05/'+row['relative_path']))
 assert expected==Path(diff['path']).read_text(),str(p)
py=[x for x in rows if Path(x['path']).suffix=='.py'];assert len(py)==181
for row in py:ast.parse(Path(row['path']).read_text(),filename=row['path'])
runtime={v['path']:v for v in freeze['source_closure'].values()};assert len(runtime)==176
test=CANDIDATE/'offline-r6-affected-final';before=json.loads((test/'source_hashes_before.json').read_text());after=json.loads((test/'source_hashes_after.json').read_text())
assert before==after and len(before)==201
for rel,h in before.items():assert sha(Path(closure['source_root'])/rel)==h,rel
cs=list(ET.parse(test/'junit.xml').iter('testcase'));assert len(cs)==200
assert not any(list(x.iter(k)) for x in cs for k in ['failure','error','skipped'])
guard=[json.loads(x) for x in (test/'network-guard.jsonl').read_text().splitlines()]
assert sum(x['event']=='GUARD_ACTIVE_BEFORE_CHILD_ENTRYPOINT' for x in guard)==6 and not any('DENIED' in x['event'] for x in guard)
classes=freeze['offline_java_compile']['classes'];assert len(classes)==6
for row in classes:
 raw=Path(row['path']).read_bytes();assert raw[:4]==b'\xca\xfe\xba\xbe' and struct.unpack('>H',raw[6:8])[0]==55
for row in rows:
 if row['relative_path'].endswith('.java'):assert row['status']=='unchanged'
for compiled in freeze['offline_java_compile']['receipts']:
 r=json.loads(Path(compiled['receipt']['path']).read_text());assert r['exit_code']==0
 if 'source_sha256' in r:assert r['source_sha256']==sha(Path(closure['source_root'])/'tools/java/W24CureCouponFixture.java')
 for x in r.get('sources',[]):assert sha(Path(x['path']))==x['sha256']
assert sha(ROOT/'local-preservation/w24-coupled02-independent-source-review/REVIEW_RECEIPT.json')=='a86d71184ea6d46cb2d58cb3ec5cdc90a4ae55129cc80e0bae3dca66738534cc'
assert sha(ROOT/'local-preservation/r11-independent-source-review/REVIEW_RECEIPT.json')=='fe36990e646ffd71b2167c31b562d0e1e3f703c8b2601055d916fd1237f692f6'
assert sha(ROOT/'local-preservation/w24-coupled03-independent-source-review/REVIEW_RECEIPT.json')=='aeb97b5726bb985253bb5a107f620574c500669266ff9ccaec88ce96d91d31d0'
assert sha(ROOT/'local-preservation/w24-coupled04-independent-source-review/REVIEW_RECEIPT.json')=='66aefff240d9eb7d9068b63c751114d68c21c0c9cc2894a441edd74eec4ce4ff'
report={'status':'PASS_FROZEN_CLOSURE_RAW_TESTS_AST_COMPILE_SCOPE_ONLY','packet':str(PACKET),'packet_sha256':sha(PACKET),'freeze':str(F),'freeze_sha256':sha(F),
 'candidate':str(CANDIDATE),'source_root':closure['source_root'],'verified_byte_references':len(refs),'source_inputs':201,'source_counts':source_counts,
 'source_differences':6,'parent_source_inputs':200,'fresh_AST_checked':181,'production_validator_source_closure':176,'runtime_prefix_entries':117,'exact_diff_header_historical_labels':False,
 'raw_final_tests':{'passed':200,'failures':0,'errors':0,'skipped':0,'guard_active':6,'guard_denied':0,'source_before_after_equal':True},
 'actual_parent_class_artifacts':6,'major_version':55,'Java_source_deltas':0,'native_run':'NOT_RUN','readonly_historical_snapshot_drift':snapshot_drift,
 'final_binding_sha256':sha(binding),'guarded_production_validator_sha256':sha(validator),'old_reviews_unchanged':True,'coupled04_parent_receipt_sha256':sha(ROOT/'local-preservation/w24-coupled04-independent-source-review/REVIEW_RECEIPT.json')}
(OUT/'CLOSURE_VERIFICATION.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
