from pathlib import Path
import json,hashlib,ast,sys,datetime
C=Path(__file__).resolve().parents[1];B=C.parents[1];sys.path.insert(0,str(C))
from tools import run_native_w23_saved_mph_diagnostic as runner
sha=runner.sha256_file;ref=lambda p:{'path':str(p),'bytes':p.stat().st_size,'sha256':sha(p)}
P=C/'root-review-packet';E=runner.EVIDENCE_ROOT/'native-run-20261013-021-saved-mph-zero-solve-candidate20-r13-final1'
D=B/'repository/docs/full_project_continuation_v2/evidence/w23_saved_mph_candidate20_r12/ROOT_ACTUAL_ACTIVITY020_FREQ_PASS_FIELD_UNKNOWN_CHECK_AND_DISPOSITION.json';assert sha(D)=='2a5029dcc68fe3ce7707e892ef1f3eac7dabc9b883eb44a3dad4761005cff751'
S=B/'repository/docs/full_project_continuation_v2/state/RESUME.json';before=S.read_bytes();assert sha(S)=='62667e19ba665ffaf47575cf88b4a94c41ff6c390152f8b6eb8433bed69665ce' and json.loads(before)['active_jobs']==[]
assert runner.ACTIVITY_ID=='w23-saved-mph-zero-solve-readback-20261013-021' and runner.CANDIDATE_REVISION=='candidate20-r13'
R7=B/'local-preservation/w23-saved-mph-science-readback-20261012-candidate20-r7';R10=B/'local-preservation/w23-saved-mph-science-readback-20261013-candidate20-r10';oldE=R10/'docs/full_project_execution/w23_overlap/evidence/native-run-20261013-018-saved-mph-zero-solve-candidate20-r10-final1';old=json.loads((oldE/'freeze.json').read_text())
assert (C/'comsol_mcp/_solution_binding.py').read_bytes()!=(R7/'comsol_mcp/_solution_binding.py').read_bytes()
for rel in ('tools/w23_te_science_protocol.py','comsol_mcp/_ephemeral_result_ticket.py','comsol_mcp/_java_worker.py','comsol_mcp/worker_java/PersistentComsolWorker.java','comsol_mcp/_g3_results.py'):
 assert (C/rel).read_bytes()==(B/'local-preservation/w23-saved-mph-science-readback-20261013-candidate20-r12'/rel).read_bytes(),rel
sources=runner._source_files();assert len(sources)==411
for rel,row in sources.items():
 if rel.endswith('.py'):ast.parse(Path(row['path']).read_bytes())
# Run the production preparation without gate injection: SDK compilation and
# all current affected controls execute now; no old 435/466 result is relabeled.
prepared=runner.prepare(E,work=Path('/private/tmp/comsol-mcp-w23-te-saved-mph-zero-solve-readback-20261013-021-candidate20-r13'),execution_evidence=E/'native-execute')
f=runner._validate_freeze(Path(prepared['freeze_path']),prepared['freeze_sha256']);material=runner.science._check_offline_receipts(Path(prepared['freeze_path']),f)
classes=[]
for label,record in f['offline_preflight']['compile']['compiled_sources'].items():
 previous=old['offline_preflight']['compile']['compiled_sources'][label];assert label!='persistent_worker' or (record['source_sha256']==previous['source_sha256'] and record['classes']==previous['classes'])
 for rel,row in record['classes'].items():
  a=oldE/'offline_gates/compile'/label/'classes'/rel;n=E/'offline_gates/compile'/label/'classes'/rel;assert label!='persistent_worker' or a.read_bytes()==n.read_bytes();classes.append({'label':label,'relative':rel,'original018':ref(a),'new021':ref(n),'byte_exact':a.read_bytes()==n.read_bytes(),'new_java_source_sha256':record['source_sha256'],'unchanged_worker_required':label=='persistent_worker'})
assert len(classes)==19
runner.write_json(E/'offline_gates/EXACT_19_ORIGINAL_CLASS_COMPARISON.json',classes)
assert S.read_bytes()==before
receipt={'schema':'ACTIVITY021_R13_SOURCE_SDK_FULL_CURRENT_CONTROLS_PREPARATION/1','status':'PASS_REAL_PRODUCTION_PREPARE_SDK_AND_SOFTWARE_ONLY_NO_NATIVE_ADMISSION','created_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'prepare_result':prepared,'real_production_prepare_with_no_gate_injection':True,'actual_production_preflight':material,'root_repair_disposition':ref(D),'source_count':len(sources),'exact19_class_material':ref(E/'offline_gates/EXACT_19_ORIGINAL_CLASS_COMPARISON.json'),'old018_019_020_not_replayed':True,'whole_current_affected_test_bundle_executed':True,'old435_or466_not_claimed_as_new_by_inheritance':True,'native':'NOT_RUN','science':'NOT_ACCEPTED','W24':'HOLD_NOT_DISPATCHED','CAS':'NOT_MODIFIED','RESUME_sha256':sha(S)}
runner.write_json(P/'SOURCE_SDK_FULL_PREPARE_RECEIPT.json',receipt);print(json.dumps(ref(P/'SOURCE_SDK_FULL_PREPARE_RECEIPT.json')))
