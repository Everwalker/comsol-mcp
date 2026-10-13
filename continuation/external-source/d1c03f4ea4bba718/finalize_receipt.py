from pathlib import Path
from datetime import datetime,timezone
import os,json,hashlib,shutil,xml.etree.ElementTree as ET
B=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2');R=B/'repository';E=R/'docs/full_project_continuation_v2/evidence/intel-builder-execution-r1';O=Path(__file__).parent
PIN='02afca66d50f5e005dc2308eed0f69015937789b3f6995c1036c2badbe769d94'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def ref(p):return {'path':str(p),'bytes':p.stat().st_size,'sha256':sha(p)}
def resolve(value):
 p=Path(value);return p if p.is_absolute() else R/p
def save(name,value):
 path=O/name;fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 with os.fdopen(fd,'w') as stream:json.dump(value,stream,indent=2,ensure_ascii=False);stream.write('\n')
 return ref(path)
F=E/'OPENSSL359_RUNTIME_SOURCE_FREEZE_R1.json';assert sha(F)==PIN;f=json.loads(F.read_bytes());post={}
for row in f['source_files']:assert sha(resolve(row['path']))==row['sha256'];post[row['path']]=row['sha256']
assert len(post)==65
postref=save('SOURCE_AFTER_REVIEW.json',{'exact_freeze_sha256':PIN,'all65_source_bytes_unchanged':True,'source_files':post})
source=Path('/private/tmp/openssl359-runtime-r1-independent001');dest=O/'public-boundary-fixtures';shutil.copytree(source,dest,symlinks=True);artifacts=[]
for p in source.rglob('*'):
 q=dest/p.relative_to(source)
 if p.is_symlink():assert p.readlink()==q.readlink();artifacts.append({'origin':str(p),'preserved':str(q),'kind':'symlink','literal':str(p.readlink()),'followed':False})
 elif p.is_file():assert sha(p)==sha(q);artifacts.append({'origin':str(p),'preserved':str(q),'kind':'regular','sha256':sha(p),'bytes':p.stat().st_size})
fixture=save('PUBLIC_BOUNDARY_FIXTURE_PRESERVATION.json',{'files':artifacts,'scope':'Public identity/provider/module-file boundary fixtures; no new target Python or native profile producer output','native_effects':0})
closure=json.loads((O/'CLOSURE_VERIFICATION.json').read_bytes());assert closure['source_count']==65 and closure['primary_unique_refs']==1187 and closure['actual_unique_sameFD_checks']==1252
raw=json.loads((O/'ACTUAL_PRODUCER_MAIN_IMPORTLIB_LOCAL_RESIDUAL.json').read_bytes());assert len(raw['actual_caught_import_errors'])==7 and raw['runtime_profile_JSON']=='NOT_CREATED'
run=json.loads((O/'openssl359-runtime-r1-independent001.receipt.json').read_bytes());cases=list(ET.parse(O/'openssl359-runtime-r1-independent001.junit.xml').iter('testcase'));assert len(cases)==2 and not any(list(c.iter(t)) for c in cases for t in ('failure','error','skipped'))
assert run['exit_code']==0 and run['guard_active']==1 and run['guard_denied']==0
crun=json.loads((O/'openssl359-runtime-r1-independent-closure001.receipt.json').read_bytes());assert crun['rc']==0 and crun['guard_ACTIVE']==1 and crun['guard_DENY']==0
sourcefile=E/'openssl359_runtime_profile_r2.py'
finding={'id':'OPENSSL359-RUNTIME-PRODUCER-IMPORTLIB-LOCAL-03','priority':'P2','status':'OPEN_REPRODUCED_SOURCE_SOFTWARE',
 'title':'Runtime producer main shadows importlib before all required imports',
 'source':str(sourcefile),'lines':{'required_module_read':28,'required_gate':31,'local_import_binding':32},
 'code_fact':'Function main reads importlib.import_module before its later import importlib.util. Python classifies importlib as a local variable throughout this function. Every attempted import therefore raises UnboundLocalError before an import attempt is made.',
 'regression_origin':'Parent openssl359_runtime_profile_r1 uses module-level statements, so its later import does not cause unbound local access. The successor wraps this sequence in main without moving the import binding.',
 'actual_production_reproduction':'Actual frozen main executed through only explicit public identity/provider/module file boundary fixtures, with real host hashlib/_hashlib ABC knownvector and SSLContext floor. All7 module diagnostics became IMPORT_FAILED/UnboundLocalError; required zlib check terminated. Host zlib/sqlite3/ctypes/venv imports separately succeed. Certificate and MemoryBIO helpers were not reached, and output profile was never created.',
 'impact':'Even with the expected actual target provider and all required modules available, the new fixed runtime-only recipe cannot complete its producer or reach the mandatory certificate/MemoryBIO acceptance. The latent failure is in production control flow, not missing installed zlib or a fixture/runtime success.',
 'required_repair':'Bind importlib/util before first use at module scope, or use a separately named util import without shadowing importlib. Keep the original exact provider/identity/knownvector/module/certificate/MemoryBIO checks and failure/raw preservation. Add a narrow actual producer main control that reaches an explicit certificate-helper sentinel or completes public helper boundaries; validate_provider/validate_profile-only tests do not cover this path.',
 'acceptance':'Actual producer main with public fixed identity/provider seams reaches required imports correctly and progresses past their gate toward the unchanged certificate helper. Genuine required import failures still refuse. Exact provider negative controls, original profile first-record pin, immutable installed-R4 and one-command/budget/owner constraints remain unchanged.',
 'raw':ref(O/'ACTUAL_PRODUCER_MAIN_IMPORTLIB_LOCAL_RESIDUAL.json'),'native_runtime_scope':'NOT_RUN; boundary fixture provider fields are not actual target provider capture or runtime PASS'}
semantic=save('SEMANTIC_REVIEW.json',{'status':'CHANGES_REQUIRED','scope':'Whole frozen OpenSSL359 runtime-only R1 source/software unit, 4new source and parent diffs, inherited61 unchanged','findings':[finding],
 'scoped_closed':[
  'Provider comparison uses full expected(3,5,0,9,0),0x30500090 and exactofficialtext, independent source-derived expectation not mislabeled as target actual tuple',
  'Producer logs first provider fields before comparison and stores same fields on eventual success; refusal details retain them as diagnostic data',
  'Fixed installed original R4 prefix/executable/modules/source/cert fixtures distinguished from fresh runtime output namespace; no rebuild/replay of original5 passed commands',
  'Actual profile consumer first owned record survives later verification and refuses identical bytes at a replacement inode; current metadata not re-adopted',
  'Original mandatory knownvectors/defaultTLS/4required modules/certfields/malformed negative/MemoryBIO1.2+1.3/protocol capability checks remain source-present but cannot currently be reached due to sole main control-flow defect',
  'One original managed make runtime with exactly2 fixed recipe children, original wait180/unit1800includingcleanup300/prepare600; samePopen/request/birth/group/retainer/firstFAILUNKNOWN preserved',
  'Immutable originalR4 complete14215tree/actualreceipt6rc[0,0,0,0,0,2], alloldtrees and original43 sourceinputs verified; source/failure/build products not mutated',
  'Original61 source byteexact; all65AST and1187primary sameFD hashes; historical19distinct20controls remain software-only with guard NOT_SUPPLIED; no old groups rerun'],
 'test_scope':'Independent2 tests PASS because they positively reproduce the source defect and verify limited consumer gates; not candidate or runtime PASS.',
 'scope_limits':{'new_targetPython_runtime_native_compile_COMSOL':0,'actual_targetprovider_tuple':'NOT_CAPTURED_IN_R4_AND_NOT_RUN_NEW','R4_original_runtime':'FAIL_PRESERVED','Intel_hardware':'NOT_RUN','_lzma':'REQUIRED_OPEN','optional_gdbm_tk':'Original diagnostics plus finite Root product-scope interpretation only','three_arch_fullproduct_Goal':'OPEN_NOT_ACCEPTED'}})
prior=B/'local-preservation/openssl359-cpython-r4-independent-source-review/REVIEW_RECEIPT.json'
assert sha(prior)=='cb22a12d15efd3e015ce9fe443e5539e7b78141787a0440a0ca7338517b95ebc'
receipt=save('REVIEW_RECEIPT.json',{'schema':'comsol-mcp.independent-scoped-source-review/1','status':'CHANGES_REQUIRED','created_utc':datetime.now(timezone.utc).isoformat(),
 'reviewer_role':'Independent whole exact OpenSSL359 runtime-only R1 source/software Reviewer; not final wholeGoal Reviewer',
 'exact_freeze':ref(F),'exact_design':ref(resolve(f['current_design']['path'])),'source_count':65,'parent61_byte_exact':True,'new4_all_read':True,'AST':65,
 'primary_unique_sameFD_refs':1187,'total_actual_unique_sameFD_checks':1252,'closure':ref(O/'CLOSURE_VERIFICATION.json'),'semantic_review':semantic,'findings':[finding],
 'parent_R4_scoped_source_acceptance_unchanged':ref(prior),'actual_R4_original_failure':ref(E/'OPENSSL359_CPYTHON_EXECUTION_RESULT_R4.json'),
 'original_R4_runtime_facts':{'complete_tree_records':14215,'all_old_trees_unchanged':True,'original5_commands_PASS':True,'original_runtime6_rc':2,
  'actual_fulltuple':'NOT_CAPTURED','knownvector_certs_MemoryBIO':'NOT_REACHED','actual_targetTLS_runtime':'FAIL_PRESERVED','not_promoted_RUNTIME_PASS':True},
 'historical_controls':{'distinct':19,'actual_executions':20,'failure':0,'error':0,'skip':0,'guard':'NOT_SUPPLIED','scope':'Helper/profile validators and read-only artifact controls; actual main not covered','rerun':False},
 'fresh_independent':{'unique_tests_passed':2,'guard_ACTIVE':1,'guard_DENY':0,'candidate_result':'CHANGES_REQUIRED_REPRODUCED_P2',
  'actual_main_caught_UnboundLocalErrors':7,'actual_main_terminal':'RuntimeError existing product runtime capability regressed:zlib',
  'certificate_and_MemoryBIO':'NOT_REACHED','actual_profile_output':'NOT_CREATED',
  'identity_and_provider':'Explicit public boundary data, not target actual capture or new authority',
  'host_knownvector_TLSContext':'Actually run in existing host interpreter only, not selected R4 target359 runtime',
  'consumer_controls':'Fulltuple5components/number/text refuse, fixed first profile record samebytes newinode refuse',
  'run':ref(O/'openssl359-runtime-r1-independent001.receipt.json'),'JUnit':ref(O/'openssl359-runtime-r1-independent001.junit.xml'),
  'stdout':ref(O/'openssl359-runtime-r1-independent001.stdout.bin'),'stderr':ref(O/'openssl359-runtime-r1-independent001.stderr.bin'),'guard':ref(O/'openssl359-runtime-r1-independent001.guard.jsonl'),
  'script':ref(O/'test_independent_runtime_r1.py'),'main_raw':ref(O/'ACTUAL_PRODUCER_MAIN_IMPORTLIB_LOCAL_RESIDUAL.json'),'consumer_raw':ref(O/'ACTUAL_PROVIDER_AND_FIRST_RECORD_CONSUMER_SCOPED_CONTROLS.json')},
 'fresh_closure':{'guard_ACTIVE':1,'guard_DENY':0,'run':ref(O/'openssl359-runtime-r1-independent-closure001.receipt.json'),'guard':ref(O/'openssl359-runtime-r1-independent-closure001.guard.jsonl'),
  'stdout':ref(O/'openssl359-runtime-r1-independent-closure001.stdout.bin'),'stderr':ref(O/'openssl359-runtime-r1-independent-closure001.stderr.bin'),'script':ref(O/'verify_closure.py')},
 'first_public_synthetic_fixture_states':fixture,'source_after':postref,'all65_source_bytes_unchanged':True,'new_namespace_absent':True,
 'scope_limits':{'new_targetPython_native_runtime_compile_unpack_COMSOL_OSproof':0,'Intel_hardware':'NOT_RUN','_lzma':'REQUIRED_OPEN','fullruntime_threearch_delivery':'OPEN',
  'wholeGoal_final_acceptance':'NOT_ACCEPTED','native_admission':'NOT_GRANTED'},
 'writes':'Only new private independent review folder and public synthetic /private/tmp fixtures. No source/freeze/oldfailure/realruntime namespace/live/CAS/PROGRESS/RESUME/Git changes.',
 'consolidated_conclusion':'One substantive production main importlib local-shadowing P2 prevents required modules and later runtime checks. Other listed bindings/budget/owner/immutable inputs scoped closed. Source repair and successor independent review required before native runtime admission.'})
print(json.dumps(receipt))
