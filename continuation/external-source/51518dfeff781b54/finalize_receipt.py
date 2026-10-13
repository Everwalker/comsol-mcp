from pathlib import Path
import sys,json,hashlib,os,datetime,xml.etree.ElementTree as ET
B=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2');R=B/'repository';E=R/'docs/full_project_continuation_v2/evidence/intel-builder-execution-r1';O=Path(__file__).parent;sys.path[:0]=[str(E),str(R)]
import liblzma_successor_r2 as actor
import liblzma_prepare_r2 as prep
def ref(p):
 p=Path(p);raw=p.read_bytes();return {'path':str(p),'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
def save(p,data):
 raw=(json.dumps(data,indent=2,ensure_ascii=False)+'\n').encode();fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 try:
  view=memoryview(raw)
  while view:view=view[os.write(fd,view):]
  os.fsync(fd)
 finally:os.close(fd)
F=E/'LIBLZMA_SOURCE_FREEZE_R2.json';f=json.loads(F.read_bytes());closure=json.loads((O/'CLOSURE_VERIFICATION.json').read_bytes());assert ref(F)['sha256']==closure['freeze_sha256']=='3d6cf9b36dad862d4a125b337dee62405db1021d4aa14ca33fbd348550cdf9ea'
checked=[]
for row in f['source_files']+f['primary_refs']:
 p=Path(row['path']);p=p if p.is_absolute() else R/p;assert actor.frozen_digest(p)=={'bytes':row['bytes'],'sha256':row['sha256']};checked.append(str(p))
actual_root=E/'ROOT_LIBLZMA_R1_ACTUAL_FAILURE_CHECK.json';source_root=E/'ROOT_LIBLZMA_SOURCE_REVIEW_R2.json'
assert ref(actual_root)['sha256']=='3e008ab67cc4156e07f5aa0c6068f2453cadfe5e5f1ce40d09950bc5c876ce2b' and ref(source_root)['sha256']=='7f970289dc01372283d6874c2d4f13e2db64149045c377836995d87a4426cab8'
actual_final=B/'local-preservation/liblzma-r1-independent-actual-failure-review/FINAL_REVIEW_RECEIPT.json';assert ref(actual_final)['sha256']=='e0a1ea8acc786efd8e5871e98404fd5ad03cc5b5ec8a7cb9d8de2d51d304cd19'
run=json.loads((O/'ind-liblzma-r2-narrow-001.receipt.json').read_bytes());assert (run['exit_code'],run['guard_active'],run['guard_denied'])==(0,1,0)
cases=list(ET.parse(O/'ind-liblzma-r2-narrow-001.junit.xml').iter('testcase'));assert len(cases)==2 and not any(list(c.iter(t)) for c in cases for t in ('failure','error','skipped'))
clrun=json.loads((O/'closure.receipt.json').read_bytes());assert (clrun['exit_code'],clrun['guard_active'],clrun['guard_denied'])==(0,1,0)
assert len(prep.verify_failed_r1())==5799 and not actor.layout()['root'].exists() and not actor.layout()['root'].is_symlink()
post={'freeze':ref(F),'source90_primary1425_sameFD_unchanged':True,'sameFD_unique':len(set(checked)),'original_R1_full5799_preserved':True,'new_R2_namespace_absent':True,'Root_source_and_actual_external_refs': [ref(source_root),ref(actual_root)]};save(O/'POST_REVIEW_EXACT_CLOSURE.json',post)
evidence=[ref(path) for path in sorted(O.iterdir()) if path.is_file() and path.name not in ('REVIEW_RECEIPT.json','RECEIPT_SHA256.json')]
receipt={
 'schema':'comsol-mcp.independent-liblzma-r2-whole-source-software-review/1','created_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'reviewer':'independent /root/windows_r6_independent_source_review','result':'PASS_SCOPED_SOURCE_SOFTWARE','findings':[],
 'exact_freeze':ref(F),'design':ref(E/'LIBLZMA_DESIGN_R2.json'),'external_Root_source_review':ref(source_root),'external_Root_actual_R1_failure_check':ref(actual_root),'Independent_R1_actual_readonly_final':ref(actual_final),'external_postfreeze_bindings_do_not_change_freeze':True,
 'whole_scope':{'source_AST':90,'all_original82_byte_exact':True,'seven_necessary_namespace_variants':True,'one_new_affected_test_fully_read':True,'seven_diff_full_read_and_independently_regenerated_exact':True,'primary_unique':1425,'prepost_actual_sameFD_unique_source_primary':1515,'new_namespace_absent':True},
 'ordering_and_reuse_scoped_closed':[
  'Original R1 actual complete lists704/11947792B/0links have identical entire kind/mode/bytes/SHA and15 raw order differences. OriginalR1 remainsVALIDATION_FAIL. R2 sorts both whole complete lists by complete relative_path string then compares every object/field; no mapping conversion, duplicate collapse, member subset or mode/hash relaxation.',
  'Original failed R1 entire5799/root25625234/0700/receipt/snapshotSHA is verified before and after copied input reuse. Original auth3 sameFD/scanned listing matches original pristine XZ704. Held original root/member UID/dev/inode/mode/nlink/size/SHA snapshot feeds existing copy_exact_tree; wrong first physical identity refuses before destination creation, even same semantic bytes.',
  'Fresh R2 xz-input and both archSource copies use existing complete original copier and full canonical content validation. No new XZ official extraction. Fresh original CP3.12.13 archive safe extraction remains future necessary CP-only preparation,0patch and pinned original SSL SHA. No old R1tree/source modification or run replay.',
  'Actor/profile/verifier/CPprepare/merge/acceptance pointers consistently target intel-liblzma-r2. Existing acceptance imports generic reviewed helper methods only; newly generated Merge.mk and Runtime.mk use r2 executable helper paths. Main preexisting owner absence, samePopen/StageUnknown retention,firstFAILUNKNOWN and sourceGate remain intact; new R1 owner absence occurs only in future authorized actor.',
  'Original13futurecommands,unit1800 incl cleanup300,prepare600,runtime180,min(stagecap,sameunitremaining) remain unchanged. Official static XZ target triplets/SDK/min11/jobs2/absoluteLIBLZMA flags, original359 fat/header/checkpoint and 5firstphysical/requiredmoduleERROR/preinstall original tuple gates remain strictly conserved.',
  'Original full provider tuple/number/text,vectors/TLSfloor/fourcert fields+malformed/fivepinnedfixturecopies/MemoryBIO1.2+.3/requiredmodules/_lzma real LZMA and tar.xz/finalfourbinary dualCPU/min11/systemdylib/no dynamicliblzma fallback are inherited byte-identical except precise namespace pointers. No new or loosened scientific units or target runtime claim.'
 ],
 'whole_input_preservation':{'original43':closure['original43'],'auth3_and_original704':closure['XZauth3_scan704_no_unpack'],'original_R1_actual':closure['original_R1'],'actual_old_R4_TLS_andallold':'read-only verified, full R4 14215/runtime17/359R229154/source5082/allpreviousroots retained','actual_R1_native_outcome':'VALIDATION_FAIL_COMPLETE_LIST_ORDER_ONLY_NOT_PROMOTED','actual_R1_CP_XZsafeextracts':[1,1],'actual_R1_outercompiler_runtime':0},
 'historical_controls':{'current_new_distinct':11,'current_new_actual_executions':11,'current_new_raw':closure['new_controls'],'guard':'NOT_SUPPLIED explicitly not credited as guard evidence','inherited_R1_software_distinct13_exec13_and_input_only11':closure['inherited_R1_software13_plus_input11_only'],'old82_controls_rerun':0},
 'fresh_independent_controls':{'tests':2,'failures':0,'errors':0,'skips':0,'guard_ACTIVE':1,'guard_DENY':0,'closure_guard_ACTIVE':1,'closure_guard_DENY':0,'closure_runner_label':'Historical runner label ind-liblzma-r1-closure reused; argv/source/hash/outputs are this new R2 review and guard log is fresh. Label is descriptive only, no evidence reuse.','names':[case.attrib['name'] for case in cases],'actual_complete_list':json.loads((O/'ACTUAL_COMPLETE_LIST_CANONICAL_SORT_NEGATIVES.json').read_bytes()),'actual_original704copy_and_samebytesnewinode':json.loads((O/'ACTUAL_PRISTINE_704_COPY_AND_FIRST_INODE_NEGATIVE.json').read_bytes()),'negative_exact_type':'acquire_exact_archives.Refusal','negative_exact_reason':'original source identity changed','negative_destination_created':False,'native':0,'old11_group_not_rerun':True},
 'failure_preservation':{'original_R1_actual':ref(E/'LIBLZMA_EXECUTION_RESULT_R1.json'),'original_R1_source_receipt':ref(B/'local-preservation/liblzma-r1-independent-source-review/REVIEW_RECEIPT.json'),'original_R1_first_AST_failure':ref(B/'local-preservation/liblzma-source-first-ast-failure-r1/FAILURE.json'),'private_R2_test_or_audit_failures':0,'actual_R1_private_key_order_assertion_FAIL':'Preserved in separate immutable actual review; no source consequence or target rerun'},
 'not_accepted':{'new_R2_native_unpack_build_install_targetPython':'NOT_RUN','new_ARM_liblzma_runtime':'REQUIRED_OPEN_ACTUAL_PENDING','Intel_HW':'NOT_RUN','hardquota':'OPEN','optional_gdbm_Tk':'Original IMPORT_FAILED/unresolved scope retained, no modulePASS','full_three_arch_product_T037_Goal':'OPEN','native_admission':'NOT_GRANTED_BY_INDEPENDENT_SOURCE_RECEIPT'},
 'all_private_evidence':evidence,'frozen_live_Git_CAS_PROGRESS_RESUME_oldsource_or_originaltree_writes':0,'new_COMSOL_OSprobe_signal_targetPython_nativecompile_or_archive_unpack':0
}
save(O/'REVIEW_RECEIPT.json',receipt);item=ref(O/'REVIEW_RECEIPT.json');save(O/'RECEIPT_SHA256.json',item);print(json.dumps(item))
