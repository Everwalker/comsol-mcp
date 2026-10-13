from pathlib import Path
import os,json,hashlib,sys,datetime,xml.etree.ElementTree as ET
B=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2');R=B/'repository';E=R/'docs/full_project_continuation_v2/evidence/intel-builder-execution-r1';O=Path(__file__).parent
sys.path[:0]=[str(E),str(R)]
import liblzma_successor_r1 as actor
def file_ref(p):
 p=Path(p);raw=p.read_bytes();return {'path':str(p),'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
def save(p,data):
 raw=(json.dumps(data,indent=2,ensure_ascii=False)+'\n').encode();fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 try:
  view=memoryview(raw)
  while view:view=view[os.write(fd,view):]
  os.fsync(fd)
 finally:os.close(fd)
fpath=E/'LIBLZMA_SOURCE_FREEZE_R1.json';f=json.loads(fpath.read_bytes());closure=json.loads((O/'CLOSURE_VERIFICATION.json').read_bytes())
assert file_ref(fpath)['sha256']==closure['freeze_sha256']=='2e66519156b4764ec4488bb5b2f680a09c28c202484ea2096c232a382354a43b'
checks=[]
for row in f['source_files']+f['primary_refs']:
 p=Path(row['path']);p=p if p.is_absolute() else R/p
 assert actor.frozen_digest(p)=={'bytes':row['bytes'],'sha256':row['sha256']};checks.append(str(p))
root_review=E/'ROOT_LIBLZMA_SOURCE_REVIEW_R1.json';assert file_ref(root_review)['sha256']=='92017cb767a151459de49c807c31f330388c1101438010d1be8d93f87dfd74f0'
junit=O/'ind-liblzma-r1-narrow-001.junit.xml';cases=list(ET.parse(junit).iter('testcase'));assert len(cases)==2 and not any(list(c.iter(t)) for c in cases for t in ('failure','error','skipped'))
run=json.loads((O/'ind-liblzma-r1-narrow-001.receipt.json').read_bytes());assert (run['exit_code'],run['guard_active'],run['guard_denied'])==(0,1,0)
crun=json.loads((O/'closure.receipt.json').read_bytes());assert (crun['exit_code'],crun['guard_active'],crun['guard_denied'])==(0,1,0)
assert not actor.layout()['root'].exists() and not actor.layout()['root'].is_symlink()
acq=json.loads((B/'local-preservation/liblzma-source-acquisition-20261012-r1/RECEIPT.json').read_bytes());verify_path=Path(acq['gpg_raw'][-1]['path']);gpg=json.loads(verify_path.read_bytes())
assert gpg['rc']==0 and '[GNUPG:] GOODSIG 38EE757D69184620' in gpg['stdout'] and '[GNUPG:] VALIDSIG 3690C240CE51B4670D30AD1C38EE757D69184620' in gpg['stdout']
private_doc_error={'status':'READONLY_REVIEW_PATH_LOOKUP_ERROR_CORRECTED','raw_exit_code':2,'guessed_path':'/private/var/folders/4d/6pphmzkx5szf8dyqx7zhqh1r0000gn/T/comsol-intel-inputs-r1-obneuyy4/source-inputs/Python-3.12.13/configure.ac','raw_stderr':'rg: /private/var/folders/4d/6pphmzkx5szf8dyqx7zhqh1r0000gn/T/comsol-intel-inputs-r1-obneuyy4/source-inputs/Python-3.12.13/configure.ac: No such file or directory (os error 2)\n','corrected_by':'actual tls_sources_r2 CP_RELATIVE and PREFIX/sources, official configure.ac5139–5151/7553–7554 then read','production_or_test_failure':False,'native_or_source_effects':0}
save(O/'PRIVATE_READONLY_DOC_LOOKUP_ERROR.json',private_doc_error)
post={'freeze':file_ref(fpath),'sameFD_source_primary_count':len(set(checks)),'source82_primary1340_unchanged':True,'new_namespace_absent':True,'old69_suite_rerun':0}
save(O/'POST_REVIEW_EXACT_CLOSURE.json',post)
evidence=[file_ref(p) for p in sorted(O.iterdir()) if p.is_file() and p.name not in ('REVIEW_RECEIPT.json','RECEIPT_SHA256.json')]
receipt={
 'schema':'comsol-mcp.independent-liblzma-whole-source-software-review/1','created_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'reviewer':'independent /root/windows_r6_independent_source_review','result':'PASS_SCOPED_SOURCE_SOFTWARE','findings':[],
 'exact_freeze':file_ref(fpath),'design':file_ref(E/'LIBLZMA_DESIGN_R1.json'),'external_Root_source_review':file_ref(root_review),'external_Root_binding_does_not_change_freeze':True,
 'whole_review':{'sources_AST':82,'parent69_byte_exact':True,'extensions':13,'inherited_authenticated_XZ_helper_test':2,'new_variants_helpers_tests_fully_read':11,'seven_frozen_diffs_independently_regenerated_exact':True,'new_helpers_fully_read':['liblzma_prepare_r1.py','liblzma_acceptance_r1.py','test_liblzma_producer_r1.py','test_liblzma_unit_r1.py'],'primary_unique':1340,'prepost_source_primary_sameFD_unique':1422,'new_namespace_absent':True},
 'source_design_acceptance':[
  'Original official CP3.12.13 _ssl.c fixed original SHA; fresh source zero patch. Five original fixed public certificate copies retain first checked identity/bytes/fixedSHA -> original checkpoint -> destination verification. Legacy R4 TLS failure remains immutable.',
  'Fixed original XZ5.8.4 archive/signature/publickey identities and owner/mode/nlink/hash verified. Original stored GPG rc0 GOOD/VALID fingerprint3690C240CE51B4670D30AD1C38EE757D69184620 read/hash, no new GPG/download. Full read-only tar listing704members/11947792B/0links; later existing reviewed extractor plus full content projection and exact arch copies.',
  '13 future outer commands exactly match design: six static-only XZ ARM/x86 configure/build/install, one observed make/lipo merge, six original CP stages. Explicit target triplets, SDK/min11/-arch, only src/liblzma recursive targets, jobs<=2, static liblzma absolute flag and pinned include. No target commands dispatched by this review.',
  'Original thin library full Mach-O objects exact CPU/min11; both headers full bytes equal; original immutable merge checkpoint and output liblzma fat slices must equal original thin bytes. Fat inputs and header first records verified before each CP/header/runtime stage. Original OpenSSL header subset retained byte/identity under newly added liblzma header peers.',
  'Five same original ownedDirFD physical first records Makefile/actual BUILDPYTHON/_ssl/_hashlib/_lzma rechecked at whole gate and original immutable checkpoint last before install. Official symbolic build views only fixed owned text/identity, never followed for bytes; helper [ERROR] _lzma blocks install even make rc0. No _failed, fresh baseline adoption, or path resolve accepted.',
  'Actual future producer main retains full provider tuple3,5,0,9,0 /0x30500090 /official text, known vectors/TLS floor, certificate fields/malformed SSLError, MemoryBIO1.2+1.3 and required zlib/sqlite3/ctypes/venv. _lzma now required, actual LZMA FORMAT_XZ and tarfile w:xz/r:xz fixed public payload roundtrips mandatory before profile; fixed _lzma installed path and both roundtrip flags consumed.',
  'Final main/_ssl/_hashlib/_lzma each exact dual CPU/min11/canonical system dylibs. _lzma rejects dynamic system or foreign liblzma. Source binary decoder acceptance alone does not prove ARM/Intel target execution or liblzma runtime.',
  'Original cp.run/Popen/StageUnknown samehandle retention and firstFAIL/UNKNOWN stop remain unchanged. Dynamic born unit1800 includes prepare600/stage verification/waits/runtime180/preservation/cleanup300; no next dispatch after exhausted budget. No issuer/native proof or takeover added.',
  'Original43 sameFD archive owner/inode/mode/bytes/hash, original CP source/cache and all prior failed TLS/OpenSSL trees and actual359R2 full29154, R4 full14215, runtimeR2 full17 preservation functions verified read-only. Original runtime actual ARM TLS/hashlib PASS stays separate from this new source unit.'
 ],
 'fresh_independent_controls':{'tests':2,'failures':0,'errors':0,'skipped':0,'guard_active':1,'guard_denied':0,'closure_guard_active':1,'closure_guard_denied':0,'test_names':[c.attrib['name'] for c in cases],'host_main':json.loads((O/'ACTUAL_PRODUCER_MAIN_HOST_AND_NEGATIVES.json').read_bytes()),'production_preinstall_and_decoder':json.loads((O/'ACTUAL_PREINSTALL_FIRST_RECORD_AND_BINARY_NEGATIVES.json').read_bytes()),'scope':'Production main/consumer/gate and real host LZMA/tar.xz/knownvector/import/TLS floor; provider/prefix/cert/BIO/install are explicit public SDK boundary spies; structural _lzma fixture copies original _ssl bytes and is not a target liblzma binary'},
 'historical_raw':{'new_distinct_controls':13,'new_actual_executions':13,'new_raw':closure['historical_affected_controls'],'inherited_input_only':11,'inherited_raw':closure['inherited_input11'],'historical_guard':'NOT_SUPPLIED; not credited as guarded evidence','new13_old69_or_input11_rerun':0},
 'failure_preservation':{'original_first_AST_failure':file_ref(B/'local-preservation/liblzma-source-first-ast-failure-r1/FAILURE.json'),'original_R4_runtime_FAIL':file_ref(E/'OPENSSL359_CPYTHON_EXECUTION_RESULT_R4.json'),'original_R1_source_CHREQ':file_ref(B/'local-preservation/openssl359-runtime-r1-independent-source-review/REVIEW_RECEIPT.json'),'all_immutable_raw_failures_same_hash_in_closure':True,'private_review_test_failures':0,'private_readonly_doc_lookup_error':file_ref(O/'PRIVATE_READONLY_DOC_LOOKUP_ERROR.json')},
 'not_run_or_open':{'new_target_ARM_liblzma_runtime':'NOT_RUN_REQUIRED_OPEN','new_XZ_or_CP_unpack_nativecompile_install_targetPython':0,'Intel_hardware':'NOT_RUN','hardquota':'OPEN_NOT_SOURCE_RSS_PROOF','optional_gdbm_Tk':'Original IMPORT_FAILED and unresolved scope retained; no module PASS','full_runtime_three_arch_product':'OPEN','Windows_T037':'OPEN','wholeGoal':'OPEN','native_admission':'NOT_GRANTED_BY_THIS_RECEIPT'},
 'all_private_evidence':evidence,'frozen_source_Git_CAS_PROGRESS_RESUME_oldreview_writes':0
}
save(O/'REVIEW_RECEIPT.json',receipt);ref=file_ref(O/'REVIEW_RECEIPT.json');save(O/'RECEIPT_SHA256.json',ref);print(json.dumps(ref))
