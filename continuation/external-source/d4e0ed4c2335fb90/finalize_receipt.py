from pathlib import Path
import sys,os,json,hashlib,datetime,xml.etree.ElementTree as ET
B=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2');C=B/'local-preservation/w23-saved-mph-science-readback-20261013-candidate20-r10';O=Path(__file__).parent;sys.path.insert(0,str(C))
from tools import run_native_w23_saved_mph_diagnostic as runner
def ref(p):
 p=Path(p);raw=p.read_bytes();return {'path':str(p),'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
def save(path,data):
 raw=(json.dumps(data,indent=2,ensure_ascii=False)+'\n').encode();fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 try:
  view=memoryview(raw)
  while view:view=view[os.write(fd,view):]
  os.fsync(fd)
 finally:os.close(fd)
P=C/'root-review-packet/ROOT_REVIEW_PACKET_ACTIVITY018_R10.json';p=json.loads(P.read_bytes());F=Path(p['freeze']['path']);f=json.loads(F.read_bytes());closure=json.loads((O/'CLOSURE_VERIFICATION.json').read_bytes())
assert ref(P)['sha256']=='9b8942f35b75df58274fc04b14641e22e7578351c7e856a67cb01c0677ec33ea' and ref(F)['sha256']=='76c4a936da5f0d90354ce9f7046fc55e55c44392cbfbf8aa3ffa8cc031a70d89'
assert runner._source_files()==f['source_files']
for row in f['source_files'].values():
 actual=ref(row['path']);assert (actual['bytes'],actual['sha256'])==(row['bytes'],row['sha256'])
assert runner._validate_freeze(F,ref(F)['sha256'])==f
raw=ET.parse(O/'ind-w23-r10-preflight-001.junit.xml');cases=list(raw.iter('testcase'));assert len(cases)==1 and not any(list(c.iter(t)) for c in cases for t in ('failure','error','skipped'))
run=json.loads((O/'ind-w23-r10-preflight-001.receipt.json').read_bytes());assert (run['exit_code'],run['guard_active'],run['guard_denied'])==(0,1,0)
old017=json.loads(Path(p['old017_terminal']['path']).read_bytes());r9=json.loads(Path(p['r9_CHANGES_REQUIRED_reproduction']['path']).read_bytes())
assert old017['tool_result']['exit_code']==2 and old017['native_server_worker_pids']==[] and old017['native_calls']==[] and old017['Study_solver_submissions']==0
assert (r9['actual31_count'],r9['actual_failures'],r9['apparent_passes_shadowed'])==(31,3,28)
post={'packet':ref(P),'freeze':ref(F),'source404_rehash_unchanged':True,'actual_production_offline_material_revalidated':True,'work_evidence_approval_absent':all(not Path(f[key]).exists() for key in ('work_path','execution_evidence_path','approval_path_expected'))}
assert post['work_evidence_approval_absent'];save(O/'POST_REVIEW_EXACT_CLOSURE.json',post)
evidence=[ref(path) for path in sorted(O.iterdir()) if path.is_file() and path.name not in ('REVIEW_RECEIPT.json','RECEIPT_SHA256.json')]
receipt={
 'schema':'comsol-mcp.independent-activity018-r10-whole-source-software-review/1','created_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'reviewer':'independent /root/windows_r6_independent_source_review','result':'PASS_SCOPED_SOURCE_SOFTWARE','findings':[],
 'exact_packet':ref(P),'exact_freeze':ref(F),'candidate':str(C),
 'whole_closure':{'sources':404,'AST':374,'parent403_same':401,'modified':2,'added':1,'three_frozen_deltas_fully_read_and_exact_independently_regenerated':True,'packet_and_embedded_sameFD_unique_refs':1546,'source404_prepost_exact':True,'class_material':{'fresh_actual_sdk19_major':55,'all19_original_byte_exact':True,'fresh_compiler_not_run_by_reviewer':True},'actual_client_manifest_and25_Jar_content_fingerprints':closure['actual_preflight']},
 'rootcause_scoped_closed':[
  {'id':'ACTIVITY017-RELATIVE-CLASS-MATERIAL-PREBIRTH','scope':'Actual prepare now consumes science._check_offline_receipts on the actual proposed freeze parent before publishing freeze/review packet; actual _validate_freeze additionally runs the same real classpath/class material gate. Metadata-only successor cannot reuse successful previous path evidence. Original source manifests still exact404 and source-map/file-byte semantics unchanged. Fresh SDK classes remain persistent under own freeze.parent/offline_gates/compile with exact original19 bytes and major55.'},
  {'id':'R9-PREBIRTH-MISSING-GATE-SHADOWED-NEGATIVES','scope':'Actual31 affected original prebirth+CLI fixtures copy the persistent real SDK class tree and successfully call actual _validate_freeze before target mutation; old activity/revision mutations derive current frozen token and assert work differs. All28 negatives bind exact target reason and actual0handoff; no missing complete offline gate or compiler-output refusal accepted in place of target cause. Three filesystem cases explicitly use private fixture revision UUID and three original symlinks are rejected historical negative artifacts, not accepted material.'},
  {'id':'R9-466-OVERBROAD-INHERITANCE','scope':'33 actual rerun controls are original31 affected (29 directprebirth and2CLI) plus2 new real materialization controls; current raw33PASS/0F/E/S,22DESELECT is a separate selection boundary. Original435 unaffected cases inherited only with exact IDs, five complete test sources byte-identical and modified test first22case region byte-identical. Original466 raw retained and not re-labelled as a new rerun.'}
 ],
 'production_design_preserved':[
  'Only runner changes are Activity018/candidate20-r10 constants, source-list/newtest test-orchestration seeds, actual offline class-material calls in prepare and _validate_freeze. Full three deltas reviewed against original r8 and independently regenerated. Other401 original sources byte-exact; science helper classpath gate and native owner/lifecycle/Store/Worker/ephemeral comparator sources unchanged.',
  'Actual preflight reads installed official client manifest/Jars and all19 class hashes at actual parent; no compile, Server, Worker, GUI, COMSOL call, target Python, OS proof or new public action performed by reviewer. Existing source-only prepare compiled via fixed SDK javac/javap and explicit orchestration seam, and its two real gates retained actual positive and metadata-copy failure receipts.',
  'Original eight production point slots, exact field/frequency/BMA requested units, ModelRef/revision/Worker identity and epochs, zero public Study/solver dispatch, sameoriginal ownership/UNKNOWN boundaries,1800 budget/120cleanup, fixed saved MPH input all conserved. Freeze limits/read_only_call_plan/input independently equal original parent. No physical ratio/scientific threshold relaxed.',
  'Original017 human once CLI exit2/no Server or Worker birth/no public Study or solver dispatch preserved. Original017 authorization remains consumed; r10 has no native approval/dispatch. Historical r9 genuine31 reproduction3FAIL/28shadowed plus original masked token no-op raw remain CHANGES_REQUIRED and untouched.'
 ],
 'raw_current_affected_controls':closure['affected_raw'],'raw_baselines':{'31_actual_pre_mutation_material19':True,'28_actual_target_reason_negative':closure['all28_actual_target_reason_negative'],'three_filesystem_case_private_fixture_revisions':'documented fixture-only identity; production candidate20-r10 gate not relaxed','direct_andCLI_positive':'only TEST_NATIVE_EFFECT_HANDOFF_NOT_EXECUTED sentinel, not native runtime'},
 'precise_inheritance':closure['unaffected435'],
 'fresh_independent_control':{'tests':1,'failures':0,'errors':0,'skips':0,'guard_ACTIVE':1,'guard_DENY':0,'actual_production_validator_no_mock':True,'real_official_client_manifest_Jars_readonly':True,'details':json.loads((O/'ACTUAL_PREFLIGHT_CLASS_POISON_AND_RESTORATION.json').read_bytes()),'historical33_or435_or19SDK_reexecuted_by_reviewer':0},
 'failure_preservation':{'original017':ref(p['old017_terminal']['path']),'r9_CHREQ':ref(p['r9_CHANGES_REQUIRED_reproduction']['path']),'r9_original_status':'CHANGES_REQUIRED_R9_PREBIRTH_FIXTURE_REGRESSION_INHERITANCE_OVERBROAD','r9_original_actual_tests':31,'r9_FAIL':3,'r9_shadowed_negative_not_accepted':28,'private_review_test_failures':0,'private_closure_readonly_errors_preserved':['PRIVATE_CLOSURE_ATTEMPT001_ERROR.json','PRIVATE_CLOSURE_ATTEMPT002_ERROR.json'],'private_corrections':'First audit assumed all historical raw refs regular; corrected exactly three deliberate negative symlinks and verified literal/known original target bytes separately, no generic following as accepted source. Second audit assumed all baseline revisions production literal; corrected exactly three pre-existing documented filesystem-case UUID fixture identities with original target cause/expected_work. No candidate change or test rerun.'},
 'not_accepted':{'Activity018_native':'NOT_RUN_NOT_AUTHORIZED','COMSOL_scientific_eight_slots':'NOT_ACCEPTED','internal_solver_execution_count':'UNKNOWN_NOT_INFERRED_FROM_READBACK','T037_hardquota_fullproduct_Goal':'OPEN','native_admission':'NOT_GRANTED_BY_INDEPENDENT_SOURCE_RECEIPT'},
 'private_evidence':evidence,'candidate_live_Git_CAS_PROGRESS_RESUME_oldfreeze_oldraw_writes':0,'new_native_COMSOL_Server_Worker_compiler_OS_probes_or_signals':0
}
save(O/'REVIEW_RECEIPT.json',receipt);item=ref(O/'REVIEW_RECEIPT.json');save(O/'RECEIPT_SHA256.json',item);print(json.dumps(item))
