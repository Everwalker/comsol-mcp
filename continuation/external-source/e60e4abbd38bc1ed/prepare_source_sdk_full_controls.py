from pathlib import Path
import json,hashlib,ast,sys,datetime
C=Path(__file__).resolve().parents[1];B=C.parents[1];sys.path.insert(0,str(C))
from tools import run_native_w23_saved_mph_diagnostic as runner
sha=runner.sha256_file;ref=lambda p:{'path':str(p),'bytes':p.stat().st_size,'sha256':sha(p)}
P=C/'root-review-packet';E=runner.EVIDENCE_ROOT/'native-run-20261013-020-saved-mph-zero-solve-candidate20-r12-final1'
D=B/'local-preservation/w23-activity019-vs016-ticket-revision-source-diagnosis-20261013-001/ROOT_COMPLETE_SOURCE_REPAIR_DECISION.json';assert sha(D)=='460e5df0d8f44505a84a0efc798e8a5f959f22c01bee57f387172461a62ece9d'
S=B/'repository/docs/full_project_continuation_v2/state/RESUME.json';before=S.read_bytes();assert sha(S)=='883cca696ccc02a7e02199f7bbf9928d0ece2d92355889ecdb34b671b42d70d1' and json.loads(before)['active_jobs']==[]
assert runner.ACTIVITY_ID=='w23-saved-mph-zero-solve-readback-20261013-020' and runner.CANDIDATE_REVISION=='candidate20-r12'
R7=B/'local-preservation/w23-saved-mph-science-readback-20261012-candidate20-r7';R10=B/'local-preservation/w23-saved-mph-science-readback-20261013-candidate20-r10';oldE=R10/'docs/full_project_execution/w23_overlap/evidence/native-run-20261013-018-saved-mph-zero-solve-candidate20-r10-final1';old=json.loads((oldE/'freeze.json').read_text())
assert (C/'comsol_mcp/_solution_binding.py').read_bytes()!=(R7/'comsol_mcp/_solution_binding.py').read_bytes()
for rel in ('tools/w23_te_science_protocol.py','comsol_mcp/_ephemeral_result_ticket.py','comsol_mcp/_java_worker.py','comsol_mcp/worker_java/PersistentComsolWorker.java','tools/java/NativeW23TEScienceFixture.java'):
 assert (C/rel).read_bytes()==(R10/rel).read_bytes(),rel
sources=runner._source_files();assert len(sources)==409
for rel,row in sources.items():
 if rel.endswith('.py'):ast.parse(Path(row['path']).read_bytes())
# Run the production preparation without gate injection: SDK compilation and
# all current affected controls execute now; no old 435/466 result is relabeled.
prepared=runner.prepare(E,work=Path('/private/tmp/comsol-mcp-w23-te-saved-mph-zero-solve-readback-20261013-020-candidate20-r12'),execution_evidence=E/'native-execute')
f=runner._validate_freeze(Path(prepared['freeze_path']),prepared['freeze_sha256']);material=runner.science._check_offline_receipts(Path(prepared['freeze_path']),f)
classes=[]
for label,record in f['offline_preflight']['compile']['compiled_sources'].items():
 previous=old['offline_preflight']['compile']['compiled_sources'][label];assert record['source_sha256']==previous['source_sha256'] and record['classes']==previous['classes']
 for rel,row in record['classes'].items():
  a=oldE/'offline_gates/compile'/label/'classes'/rel;n=E/'offline_gates/compile'/label/'classes'/rel;assert a.read_bytes()==n.read_bytes();classes.append({'label':label,'relative':rel,'original018':ref(a),'new020':ref(n),'byte_exact':True})
assert len(classes)==19
runner.write_json(E/'offline_gates/EXACT_19_ORIGINAL_CLASS_COMPARISON.json',classes)
assert S.read_bytes()==before
receipt={'schema':'ACTIVITY020_R12_SOURCE_SDK_FULL_CURRENT_CONTROLS_PREPARATION/1','status':'PASS_REAL_PRODUCTION_PREPARE_SDK_AND_SOFTWARE_ONLY_NO_NATIVE_ADMISSION','created_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'prepare_result':prepared,'real_production_prepare_with_no_gate_injection':True,'actual_production_preflight':material,'root_repair_disposition':ref(D),'source_count':len(sources),'exact19_class_material':ref(E/'offline_gates/EXACT_19_ORIGINAL_CLASS_COMPARISON.json'),'old018_not_replayed':True,'whole_current_affected_test_bundle_executed':True,'old435_or466_not_claimed_as_new_by_inheritance':True,'native':'NOT_RUN','science':'NOT_ACCEPTED','W24':'HOLD_NOT_DISPATCHED','CAS':'NOT_MODIFIED','RESUME_sha256':sha(S)}
runner.write_json(P/'SOURCE_SDK_FULL_PREPARE_RECEIPT.json',receipt);print(json.dumps(ref(P/'SOURCE_SDK_FULL_PREPARE_RECEIPT.json')))
