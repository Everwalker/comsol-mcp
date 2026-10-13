from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,shutil,xml.etree.ElementTree as ET
BASE=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2');REPO=BASE/'repository'
E=REPO/'docs/full_project_continuation_v2/evidence/intel-builder-execution-r1';OUT=Path(__file__).parent
PIN='d0af72129702ada2c8eb14ac43cdb92c7edc63a796cdca685131b86ec6d17b34'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def ref(p):return {'path':str(p),'sha256':sha(p)}
def resolve(value):
 p=Path(value);return p if p.is_absolute() else REPO/p
def save(name,value):
 p=OUT/name
 with p.open('x') as stream:json.dump(value,stream,indent=2,ensure_ascii=False);stream.write('\n')
 return ref(p)
fpath=E/'OPENSSL359_CPYTHON_SOURCE_FREEZE_R3.json';assert sha(fpath)==PIN
f=json.loads(fpath.read_text());post={}
for row in f['source_files']:
 p=resolve(row['path']);assert sha(p)==row['sha256'];post[row['path']]=sha(p)
assert len(post)==56
postref=save('SOURCE_AFTER_REVIEW.json',{'freeze_sha256':PIN,'sources':post,'all56_exact_frozen_bytes':True})
origin=Path('/private/tmp/openssl359-cpython-r3-independent001');target=OUT/'public-synthetic-production-fixtures'
shutil.copytree(origin,target);artifacts=[]
for p in origin.rglob('*'):
 if not p.is_file():continue
 destination=target/p.relative_to(origin);assert sha(p)==sha(destination)
 artifacts.append({'origin':str(p),'preserved':str(destination),'sha256':sha(p),'origin_is_synthetic_symlink':p.is_symlink()})
fixture_ref=save('PUBLIC_SYNTHETIC_RAW_FIXTURE_PRESERVATION.json',{
 'files':artifacts,'scope':'Public synthetic minimal inert MachO-format files and metadata fixtures only; all altered/refused states preserved',
 'user_data_real_359_newroot_native_unpacked_or_launched':0})
closure=json.loads((OUT/'CLOSURE_VERIFICATION.json').read_text())
assert closure['source_count']==56 and closure['parent51_exact'] and closure['primary_unique_refs']==1016
assert closure['distinct']==12 and closure['actual_executions']==13
finding={
 'id':'OPENSSL359-CP-WHOLE-GATE-REGULAR-PIN-02','priority':'P2','status':'OPEN_REPRODUCED_SOURCE_SOFTWARE',
 'title':'Whole build gate accepts first file records after current normal regular members changed',
 'source':str(E/'openssl359_build_gate_r3.py'),
 'regular_leaf':'Reads and checks own FD/current name then closes each file FD; first record is not rechecked after later reads',
 'whole_gate':'Final aggregate checks cover opened directories and initial symbolic views, not Makefile/python/_ssl/_hashlib normal regular members',
 'downstream_source':str(E/'openssl359_cpython_successor_r3.py'),
 'handoff':'cp-build stores accepted required outputs. Subsequent cp-install checks copied source/libs/headers/cert inputs without matching the first accepted build regular member records.',
 'actual_production_reproduction':'Actual required_build_outputs and real tls_binary_r1.fat_binary, with explicit public synthetic make-boundary metadata and inert valid MachO-format files. All4 normal members, each new-inode or same-inode/same-size content mutation after last_hashlib read, returned successful gate with first accepted SHA not equal to current named bytes. Directories and original official symbolic views remain identical.',
 'cases':8,
 'impact':'The pre-install required-output decision can refer to stale regular bytes or inode while allowing the next fixed install command to consume changed current build members. This is an actual source/software wrong-current-build acceptance, not a claimed native compromise.',
 'required_repair':'Keep the existing original DirFD and first accepted member records. After all member reads and before successful whole-gate return, recheck each fixed normal regular member against its first identity/UID/nlink/mode/size/SHA using the original NOFOLLOW/checked_regular protocol. Reject same-inode content drift as well as replacement inode. Preserve that accepted binding for the existing pre-install handoff where needed; do not resample new acceptance or add authority/general-follow/OS-proof machinery.',
 'acceptance':'Makefile/python/_ssl/_hashlib late new-inode and same-size same-inode byte swaps all refuse instead of returning stale success. Normal official same-version views still pass. Existing beforeinstall helper ERROR, foreign/_failed/physical symlink, directory and first-link end-of-gate rejection remain. Do not rely only on mtime/ctime to bind fixed bytes.',
 'raw_evidence':ref(OUT/'ACTUAL_CURRENT_REGULAR_MEMBER_WHOLE_GATE_RESIDUAL.json')}
semanticref=save('SEMANTIC_REVIEW.json',{
 'scope':'Complete exact CP-only OpenSSL359 R3 source/software unit, all5new source concentrated review and unchanged51 lineage',
 'findings':[finding],'source5':closure['new5_sources'],
 'scoped_closed':[
  'Fixed owned build root/Modules/view DirFD and original checked_regular leaf validation, no generic symlink follow',
  'Exact normal physical basenames only; official symbolic view first identity/literal before/after physical read and at whole gate end',
  'Foreign absolute/traversal/_failed literal and physical symlink refusal remain; original late first-link identity swap rejected',
  'Actual existing R2 three physical dualCPU/min11/system-dylib parser positive, read-only bytes only, not new installed runtime',
  'Original R2 failure/29154complete tree/11terminal+2lipo linkage and fullfat2221objects/143headers verified by real checkpoint producer',
  'New fresh CP-only namespace absent, no OpenSSL/lipo rebuild; exact six original configure/header2/build/install/runtime commands',
  '1800unit including300cleanup/600prepare; original actor/group ownership, firstFAIL/UNKNOWN and same original handle observation retained',
  'All original43 archives and old sources/failures/roots preserved; accepted fat/header copies identity/byte checked each stage',
  'Fresh official CPython3.12.13 _ssl.c0patch and repaired firstpin fivecertificate copies reused',
  'Actual make rc0 helper requiredERROR remains before physical read/install; strict official BUILDPYTHON parser retained',
  'Future actual359 provider/knownvector/import/cert/MemoryBIO and installed binary/fulltree checks retained; actual runtime remains NOT_RUN'],
 'inherited_findings':'Original OPENSSL359-PINNED-CERT-COPY-01 stays scoped CLOSED via unchanged R2 copier. R2 native symlink VALIDATION_FAIL remains immutable.',
 'scope_boundaries':'No new OS authority/issuer, no COMSOL/science/hardware/fullGoal acceptance; only regular-output stability residual blocks this successor source unit.'})
cases=ET.parse(OUT/'openssl359-cpython-r3-independent001.junit.xml').findall('.//testcase')
assert len(cases)==2 and not any(node.tag in {'failure','error','skipped'} for case in cases for node in case)
run=json.loads((OUT/'openssl359-cpython-r3-independent001.receipt.json').read_text())
assert run['exit_code']==0 and run['guard_active']==1 and run['guard_denied']==0
prior_actual=BASE/'local-preservation/openssl359-r2-independent-actual-readback/REVIEW_RECEIPT.json'
assert sha(prior_actual)=='cadd721dbb893e5620750248eb6f28172471fa2d37f2534c994a7483104833d4'
receipt=save('REVIEW_RECEIPT.json',{
 'schema':'comsol-mcp.independent-scoped-source-review/1','status':'CHANGES_REQUIRED',
 'reviewer_role':'Independent whole frozen OpenSSL359 CP-only R3 source/software Reviewer, not final wholeGoal Reviewer',
 'created_utc':datetime.now(timezone.utc).isoformat(),'exact_freeze':ref(fpath),
 'exact_design':ref(resolve(f['current_design']['path'])),'source_count':56,'AST':56,
 'parent51_byte_exact':True,'new5_all_reviewed':True,'primary_unique_same_FD_refs':1016,
 'total_actual_unique_file_checks':closure['actual_unique_same_FD_checks'],
 'closure':ref(OUT/'CLOSURE_VERIFICATION.json'),'semantic_review':semanticref,'findings':[finding],
 'historical_current_controls':{'distinct':12,'actual_executions':13,'failure':0,'error':0,'skip':0,
  'scope':'Actual R2 readonly byte gate plus public synthetic controls; original readonly positive repeated once in final2',
  'old51_suites_rerun':False,'rerun_current12_group':False},
 'fresh_independent':{'unique_tests_passed':2,'guard_ACTIVE':1,'guard_DENY':0,
  'actual_R2_three_physical_files_positive':'PASS_READONLY_BYTES_NOT_RUNTIME',
  'existing_wrong_failed_literal_and_whole_firstlink_rejections':'PASS_SCOPED_SOURCE_SOFTWARE',
  'regular_member_residual_cases':8,
  'run':ref(OUT/'openssl359-cpython-r3-independent001.receipt.json'),
  'JUnit':ref(OUT/'openssl359-cpython-r3-independent001.junit.xml'),
  'stdout':ref(OUT/'openssl359-cpython-r3-independent001.stdout.bin'),
  'stderr':ref(OUT/'openssl359-cpython-r3-independent001.stderr.bin'),
  'guard':ref(OUT/'openssl359-cpython-r3-independent001.guard.jsonl'),
  'script':ref(OUT/'test_independent_r3.py'),
  'positive':ref(OUT/'ACTUAL_R2_READONLY_POSITIVE_AND_EXISTING_GATES.json'),
  'residual':ref(OUT/'ACTUAL_CURRENT_REGULAR_MEMBER_WHOLE_GATE_RESIDUAL.json')},
 'prior_R2_actual_input_review_external_basis':ref(prior_actual),
 'R2_original_actual_result':ref(E/'OPENSSL359_EXECUTION_RESULT_R2.json'),
 'R2_original_Root_actual_preservation':ref(E/'ROOT_OPENSSL359_R2_ACTUAL_FAILURE_PRESERVATION_CHECK.json'),
 'public_synthetic_states_preserved':fixture_ref,'source_after':postref,'all56_source_bytes_unchanged':True,
 'scope_limits':{'new_official_archive_unpack_or_compiler':0,'COMSOL_GUI_system_changes':0,
  'new_R3_target_runtime':'NOT_RUN','R2_continue_replay':'NOT_AUTHORIZED',
  'Intel_hardware':'NOT_RUN','_lzma':'REQUIRED_OPEN','complete_runtime_threearch_delivery':'OPEN',
  'wholeGoal_unified_acceptance':'NOT_ACCEPTED'},
 'writes':'Only new private review folder and public synthetic /private/tmp fixtures. No frozen/live source, old receipts/raw, realR3root, native authority, CAS, Git, PROGRESS or RESUME changes.',
 'consolidated_conclusion':'One substantive wrong-current-regular-output acceptance P2. Other listed gates scoped closed. Repair and independent successor review required before native admission.'})
print(json.dumps(receipt))
