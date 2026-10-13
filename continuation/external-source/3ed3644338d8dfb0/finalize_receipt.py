from pathlib import Path
from datetime import datetime,timezone
import ast,hashlib,json,shutil,xml.etree.ElementTree as ET
BASE=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2')
REPO=BASE/'repository';E=REPO/'docs/full_project_continuation_v2/evidence/intel-builder-execution-r1'
OUT=Path(__file__).parent
PIN='f6430ea1eada788eb5d128e9b150ac0580ca0c8414c745eba84467bdee0b02a5'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def ref(p):return {'path':str(p),'sha256':sha(p)}
def resolve(v):
 p=Path(v);return p if p.is_absolute() else REPO/p
def save(name,data):
 p=OUT/name
 with p.open('x') as stream:json.dump(data,stream,ensure_ascii=False,indent=2);stream.write('\n')
 return ref(p)
fpath=E/'OPENSSL359_SOURCE_FREEZE_R1_1.json';assert sha(fpath)==PIN
f=json.loads(fpath.read_text());post={}
for row in f['source_files']:
 p=resolve(row['path']);assert sha(p)==row['sha256'];post[row['path']]=sha(p)
 assert len(p.read_bytes())==row['bytes']
assert len(post)==46
postref=save('SOURCE_AFTER_REVIEW.json',{'freeze_sha256':PIN,'sources':post,'all46_exact_frozen_bytes':True})
origin=Path('/private/tmp/openssl359-r1_1-independent001')
target=OUT/'public-synthetic-production-fixtures';shutil.copytree(origin,target)
artifacts=[]
for p in origin.rglob('*'):
 if not p.is_file():continue
 dest=target/p.relative_to(origin);assert sha(p)==sha(dest)
 artifacts.append({'origin':str(p),'preserved':str(dest),'sha256':sha(p),
  'origin_is_synthetic_symlink':p.is_symlink()})
fixture_ref=save('PUBLIC_SYNTHETIC_RAW_FIXTURE_PRESERVATION.json',{
 'files':artifacts,'scope':'Public synthetic bytes only; no user data, actual source unpack or native action',
 'five_source_copy_cases':'Original regular/symlink replacement destinations and real producer checkpoints preserved'})
acquisition=json.loads((E/'OPENSSL359_SOURCE_ACQUISITION_RECEIPT_R1.json').read_text())
authroot=Path(acquisition['authentication_original_receipt']['path']).parent
raw=(authroot/'gpg-r4-verify.stdout').read_text();tokens=acquisition['actual_validsig']
assert ' '.join(tokens) in raw and '[GNUPG:] GOODSIG ' in raw
assert tokens[2]=='C46ED3F2CBEFDA1FDAADA44264ED7B1DCCE71CB2'
assert tokens[-1]=='B146647E45A7B33947AB226B2A2C87D161692D40'
for label in ('gpg-r4-inspect','gpg-r4-import','gpg-r4-verify'):
 assert json.loads((authroot/(label+'.terminal.json')).read_text())['actual_rc']==0
authref=save('SOURCE_AUTHENTICATION_RAW_REVIEW.json',{
 'status':'PASS_HISTORICAL_ACTUAL_SOURCE_AUTHENTICATION_RECHECK',
 'receipt':ref(E/'OPENSSL359_SOURCE_ACQUISITION_RECEIPT_R1.json'),
 'actual_verify_stdout':ref(authroot/'gpg-r4-verify.stdout'),
 'actual_verify_terminal':ref(authroot/'gpg-r4-verify.terminal.json'),
 'actual_validsig':tokens,'archive_bytes':53279637,
 'archive_sha256':'603f5602e2eef00d77fbd429d34dcd5822bb301757a1bc9cdb24c670f1eb859a',
 'new_network_GPG_or_unpack':0,
 'initial_uncaptured_raw_scope':acquisition['initial_metadata_raw_stdio'],
 'original_refusals_preserved':acquisition['preserved_failures']})
direct=[];scanned=0
for folder in ['comsol_mcp','tools','scripts']:
 for p in (REPO/folder).rglob('*.py'):
  scanned+=1
  for node in ast.walk(ast.parse(p.read_text())):
   names=[v.name for v in node.names] if isinstance(node,ast.Import) else [node.module or ''] if isinstance(node,ast.ImportFrom) else []
   for name in names:
    if name in {'gdbm','_gdbm','tkinter','_tkinter','dbm.gnu'}:
     direct.append({'path':str(p),'line':node.lineno,'import':name})
runtime=E/'openssl359_runtime_profile_r1.py'
staticref=save('OPTIONAL_MODULE_STATIC_AUDIT_FOR_ROOT.json',{
 'status':'STATIC_AUDIT_ONLY_NO_PRODUCT_SCOPE_EXEMPTION','product_python_files_scanned':scanned,
 'direct_gdbm_tk_import_consumers':direct,
 'absence_of_direct_imports':'Does not establish optionality or waive original product/runtime acceptance',
 'new_runtime_consumer':ref(runtime),
 'runtime_source_behavior':'Records _lzma/_gdbm/_tkinter import outcomes, leaves _lzma requirement and gdbm/Tk requirements unresolved; does not claim complete runtime',
 'lzma_product_consumer':{'path':str(REPO/'tools/full_project_release.py'),'line':208,
  'source_sha256':sha(REPO/'tools/full_project_release.py'),'declared_format':'.tar.xz','status':'REQUIRED_OPEN'},
 'original_requirements_audited':[ref(REPO/'docs/comsol_mcp_design_v1/00_FULL_SPEC.md'),
  ref(REPO/'docs/full_project_execution/contract/00_FULL_SPEC.md'),ref(REPO/'pyproject.toml')],
 'decision_owner':'Root must retain unresolved original dependency/runtime criteria; no new source exemption introduced',
 'native_module_runtime':'NOT_RUN_NEW_359'})
finding={
 'id':'OPENSSL359-PINNED-CERT-COPY-01','priority':'P2','status':'OPEN_REPRODUCED_SOURCE_SOFTWARE',
 'title':'Certificate copier discards the first fixed-reader binding and copies post-validation replacement bytes',
 'source':str(E/'openssl359_sources_r1.py'),'four_fixture_source_line':92,'keycert_source_line':95,
 'actual_producer':'prepare_sources','actual_first_reader':'tls_cert_checks_r4.checked_fixture',
 'root_cause':'Four fixtures use a newly sampled file_record after fixed-byte checked_fixture acceptance, so '
  'a replacement regular inode is accepted with a new SHA instead of the first fixed identity/size/SHA. '
  'The fifth keycert uses plain Path.read_bytes after acceptance, which can follow a replacement symlink. '
  'The immutable first-reader result is discarded in both paths.',
 'reproduction':'All five actual production copy paths reproduced with public synthetic bytes: four '
  'post-validation regular inode replacements and keycert post-validation symlink to an external synthetic '
  'file. Original checked_fixture/file_record/checkpoint_bytes/copy_blob methods execute unchanged. '
  'Only earlier archive-auth/extraction phases are explicitly disclosed SDK boundary fixtures; no real tar unpack.',
 'actual_observation':'Actual prepare_sources completes, copies nonfixed bytes into runtime-certfixtures and '
  'publishes fixed fixture metadata despite destination SHA differing from accepted fixed PIN.',
 'impact':'Violates the accepted original same-FD pinned-copy protocol and can copy data outside the validated '
  'fixed input. Later runtime rejection does not undo this already completed copy. No user data was used or copied in review.',
 'required_repair':'Retain the first checked_fixture returned identity/bytes/SHA and use it with known fixed '
  'path/fresh mode/kind in the existing original checkpoint_bytes reader; copy only the accepted raw bytes '
  'with original copy_blob. Check all five actual destination bytes against the accepted fixed values. '
  'Do not resample a new acceptance row or reopen plain path after fixed validation.',
 'acceptance':'Normal five public fixtures copy exact accepted bytes. Regular inode/symlink/same-inode content '
  'replacement after first acceptance refuses before copying replacement bytes or publishing prepared success. '
  'Preserve failures and leave future SSL C API runtime decode protocol unchanged.',
 'raw_evidence':ref(OUT/'ACTUAL_PREPARE_SOURCE_PINNED_CERT_COPY_RESIDUAL.json')}
semanticref=save('SEMANTIC_REVIEW.json',{
 'scope':'Complete OpenSSL359 R1_1 compatibility source/software unit, all7new sources plus unchanged39 lineage',
 'open_findings':[finding],
 'scoped_closed':[
  'Original39 source byte equality and appended R1-to-R1_1 correction instead of overwritten historical claims',
  'All original43 exact manifest archives use actual production same-FD owner/nlink/inode/mode/size/SHA checks before each stage',
  'Authenticated new official OpenSSL3.5.9 source, raw canonical PGP signer chain and complete source inventory preserved',
  'Fresh OpenSSL359 namespace absent; original official CPython3.12.13 source no backport/no-deprecated/Brew fallback',
  '13 outer commands:6 official OpenSSL Configure/build_libs/install_dev + observed make/lipo merge +6 CPython stages',
  'Whole unit1800/cleanup300/prepare600, stage/unit monotonic remaining clipped before commands, original Popen/group ownership retained on UNKNOWN without replay',
  'Generated ARM/x86 headers, thin and fat archive/all-object equality, two-CPU/min11/system-dylib acceptance required',
  'Actual make rc0 helper required ERROR refused before install; official BUILDPYTHON declaration strict, exact fresh main/_ssl/_hashlib binaries required',
  'Actual new ARM provider359/known vectors/imports/official certificate fields and bounded MemoryBIO TLS1.2/1.3 required by future runtime source',
  'All old failures/source/cache/deps/three original roots/old OpenSSL4.0.2 fat/header/crypto provenance preserved independently'],
 'runtime_and_Hardware':'NOT_RUN_NEW_359; historical HOST OpenSSL3.6.4 MemoryBIO source-planning evidence is not new runtime acceptance',
 'Rust_crypto_old_402':'Original historical provenance only; not promoted to new359 compatibility',
 'optional_module_static_audit':staticref,
 'source7_names':json.loads((OUT/'CLOSURE_VERIFICATION.json').read_text())['new7_sources']})
cases=ET.parse(OUT/'openssl359-r1_1-independent001.junit.xml').findall('.//testcase')
assert len(cases)==2 and not any(node.tag in {'failure','error','skipped'} for case in cases for node in case)
run=json.loads((OUT/'openssl359-r1_1-independent001.receipt.json').read_text())
assert run['exit_code']==0 and run['guard_active']==1 and run['guard_denied']==0
receipt=save('REVIEW_RECEIPT.json',{
 'schema':'comsol-mcp.independent-scoped-source-review/1','status':'CHANGES_REQUIRED',
 'created_utc':datetime.now(timezone.utc).isoformat(),
 'reviewer_role':'Independent complete OpenSSL359 R1_1 source/software review, not wholeGoal unified Reviewer',
 'exact_freeze':ref(fpath),'repository':str(REPO),'source_count':46,'original39_byte_exact':True,'new_sources':7,
 'all7_new_sources_reviewed':True,'fresh_AST':46,'unique_primary_refs_same_FD_rechecked':551,
 'closure':ref(OUT/'CLOSURE_VERIFICATION.json'),'semantic_review':semanticref,'findings':[finding],
 'source_authentication_actual_historical_raw':authref,
 'current_old_failure_and_checkpoint_preservation':ref(OUT/'REMAINING_OLD_ROOTS_PRESERVATION.json'),
 'Root_original_R4_actual_FAILURE':ref(E/'ROOT_TLS_CPYTHON_R4_FAILURE_PRESERVATION_CHECK.json'),
 'original_TLS_R4_source_only_PASS':'Immutable independent receipt unchanged; later real install failure remains separate',
 'historical_new_controls':{'distinct':14,'actual_executions':15,'failure':0,'error':0,'skip':0,
  'rerun_old39_suites':False,'source_or_HOST_planning_only':True},
 'fresh_narrow':{'unique_tests_passed':2,'guard_ACTIVE':1,'guard_DENY':0,
  'run':ref(OUT/'openssl359-r1_1-independent001.receipt.json'),
  'JUnit':ref(OUT/'openssl359-r1_1-independent001.junit.xml'),
  'stdout':ref(OUT/'openssl359-r1_1-independent001.stdout.bin'),
  'stderr':ref(OUT/'openssl359-r1_1-independent001.stderr.bin'),
  'guard':ref(OUT/'openssl359-r1_1-independent001.guard.jsonl'),
  'script':ref(OUT/'test_independent_source_copy.py'),
  'positive_control':ref(OUT/'ACTUAL_R4_RC0_REQUIRED_ERROR_AND_MAKEFILE_GATE.json'),
  'residual_control':ref(OUT/'ACTUAL_PREPARE_SOURCE_PINNED_CERT_COPY_RESIDUAL.json')},
 'public_synthetic_raw_fixtures':fixture_ref,'source_after':postref,'all46_source_bytes_unchanged':True,
 'optional_module_static_audit':staticref,
 'scope_limits':{'new_archive_acquisition_or_unpack':0,'native_compile_or_install':0,'COMSOL_or_GUI':0,
  'network_or_GPG_commands_in_review':0,'new359_actual_runtime':'NOT_RUN','Intel_hardware':'NOT_RUN',
  '_lzma':'REQUIRED_OPEN','gdbm_Tk':'UNRESOLVED_NO_SCOPE_EXCLUSION','three_arch_delivery':'OPEN',
  'scientific_and_wholeGoal_acceptance':'NOT_ACCEPTED'},
 'writes':'Only this new private review folder and public synthetic /private/tmp fixtures; no old source/freeze/raw, '
  'actual new359 root, user data, native systems, CAS, PROGRESS, Git or previous receipt writes.',
 'consolidated_conclusion':'One substantive pinned-copy P2 across all five fixtures. Remaining listed source/design '
  'controls scoped closed; successor repair and independent review required before native admission.'})
print(json.dumps(receipt))
