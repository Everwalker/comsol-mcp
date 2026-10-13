import os,sys,json,hashlib,datetime,stat
from pathlib import Path
W=Path(__file__).parent;REPO=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2/repository');E=REPO/'docs/full_project_continuation_v2/evidence/intel-builder-execution-r1';sys.path.insert(0,str(E))
import openssl359_cpython_successor_r4 as actor
from intel_crypto_successor_r1 import checkpoint_bytes
x=actor.layout();root=x['root']
def row(p):
 r=actor.record(p,maximum=134217728);checkpoint_bytes(p,r);return r
def save(p,v):
 with p.open('xb') as f:f.write((json.dumps(v,indent=2,sort_keys=True)+'\n').encode());f.flush();os.fsync(f.fileno())
commands=[json.loads(p.read_bytes()) for p in sorted(x['logs'].glob('*.result.json'))];commands.sort(key=lambda r:r['started_unix'])
assert len(commands)==6 and [r['rc'] for r in commands]==[0,0,0,0,0,2]
assert all(r['owned_process_terminal'] and r['owned_group_absent'] and not r['diagnostic_errors'] for r in commands)
absence=json.loads((W/'OWNED_PID_GROUP_ABSENCE.json').read_bytes());assert absence['all_groups_absent'] and absence['all_pids_absent']
closed=json.loads((root/'CLOSED.json').read_bytes());assert closed['status']=='TERMINAL_COMMAND_FAIL' and closed['old_roots_unchanged'] and closed['within_cap'] and not closed['errors']
source=root/'cpython-input/Python-3.12.13/Modules/_ssl.c';header=root/'fat/include/openssl/opensslv.h'
text=source.read_text();h=header.read_text();assert '*fix = libver & 0xFF' in text and '*patch = libver & 0xFF' in text and '(OPENSSL_VERSION_PATCH<<4)' in h
versionnum=(3<<28)|(5<<20)|(9<<4);v=versionnum;values=[v&15];v>>=4
for i in range(4):values.append(v&255);v>>=8
expected=tuple(reversed(values));assert expected==(3,5,0,9,0)
facts={'status':'READONLY_ACTUAL_INSTALLED_BINARY_AND_OFFICIAL_SOURCE_FACTS','installed_binaries':actor.actual_binaries(x),'installed_main_pin':row(x['install']/'bin/python3.12'),'source_ssl':row(source),'openssl_header':row(header),'frozen_runtime_profile':row(E/'openssl359_runtime_profile_r1.py'),'source_lines':{'CPython_parser':[6093,6106],'runtime_library_version_tuple':[6120,6127],'OpenSSL_macro':[114,118]},'source_derived_number':hex(versionnum),'source_derived_CP_tuple':expected,'original_profile_wrong_assertion':'ssl.OPENSSL_VERSION_INFO[:3] != (3,5,9)','actual_runtime_tuple':'NOT_CAPTURED_BY_FAILED_ORIGINAL_PROFILE','SSL_hashlib_loaded_before_assertion':'actual original profile top-level imports completed; no claim of later vectors/cert/MemoryBIO','runtime_acceptance':'FAILED_ORIGINAL_PROFILE_VERSION_ASSERTION; no successor runtime dispatch'}
save(W/'ACTUAL_INSTALLED_BINARY_AND_VERSION_LAYOUT_FACTS.json',facts)
refs=[]
for folder in [W,root,x['logs']]:
 for p in folder.iterdir():
  if p.is_file() and not p.is_symlink():refs.append(row(p))
for name in ['OPENSSL359_CPYTHON_SOURCE_FREEZE_R4.json','ROOT_OPENSSL359_CPYTHON_SOURCE_REVIEW_R4.json','ROOT_OPENSSL359_CPYTHON_R4_ONCE_DISPATCH_BINDING.json']:refs.append(row(E/name))
refs.append(row(REPO.parent/'local-preservation/openssl359-cpython-r4-independent-source-review/REVIEW_RECEIPT.json'))
packet={'schema':'comsol-mcp.openssl359-cpython-r4-actual-result/1','created_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'status':'TERMINAL_COMMAND_FAIL_RUNTIME_ASSERTION','actual_tool_session':78048,'wrapper_pid':3509,'actor_pid':3514,'actual_wait_rc':1,'wrapper_terminal':json.loads((W/'terminal.json').read_bytes()),'scope':'5 actual CP commands PASS, sixth runtime make rc2; TLS/hashlib runtime acceptance incomplete. No OpenSSL/lipo rebuild or namespace replay.','root':str(root),'complete_new_tree':row(W/'COMPLETE_OPENSSL359_CP_R4_TREE.json'),'new_root_identity':json.loads((W/'COMPLETE_OPENSSL359_CP_R4_ROOT_IDENTITY.json').read_bytes()),'closed':closed,'commands':commands,'actual_request_env_birth_group_wait_raw':'all six original requests/spawn/birth/group/results/stdout/stderr pinned in refs; no fake stdio','owner_absence':absence,'whole_old_tree_preservation':row(W/'POST_EXECUTION_OLD_TREES_VERIFIED.json'),'original43_source_freeze':'actual source_gate PASS post execution by original capture; exact bytes/owner checks retained','required_build_outputs_firstaccepted':row(root/'REQUIRED_BUILD_OUTPUTS_VERIFIED.json'),'installed_binary_facts':row(W/'ACTUAL_INSTALLED_BINARY_AND_VERSION_LAYOUT_FACTS.json'),'first_failure':{'stderr':row(x['logs']/commands[-1]['stderr']),'cause':'original profile wrong OpenSSL version tuple assertion; actual tuple NOT_CAPTURED, source derivation explicit','original_EXCEPTION_kept':row(root/'FAIL.json'),'no_native_retry':True},'actual_effects':{'network':0,'systeminstall':0,'COMSOL':0,'pure_Intel_runtime':0,'OpenSSL_or_lipo_rebuild':0,'native_commands':6},'acceptance':{'installed_dualCPU_min11_systemdylib_bytes':'READONLY_PARSER_PASS_ACTUAL','ARM_imports_ssl_hashlib':'completed imports before original failing assertion only','ARM_vectors_cert_MemoryBIO':'NOT_REACHED','Intel_hardware':'NOT_RUN','_lzma':'REQUIRED_OPEN','full_runtime_three_arch_final_delivery':'OPEN'},'fences':'all original permanent guard/ACTIVE/failure/root/raw retained','source_disposition':'all frozen61 untouched; architecture decision requested for independent runtime-only corrected profile candidate, no new native command','refs':refs}
save(E/'OPENSSL359_CPYTHON_EXECUTION_RESULT_R4.json',packet)
print(json.dumps({'receipt':row(E/'OPENSSL359_CPYTHON_EXECUTION_RESULT_R4.json'),'facts':row(W/'ACTUAL_INSTALLED_BINARY_AND_VERSION_LAYOUT_FACTS.json'),'tree':row(W/'COMPLETE_OPENSSL359_CP_R4_TREE.json'),'commands_rc':[r['rc'] for r in commands],'records':packet['new_root_identity']['records'],'elapsed':packet['wrapper_terminal']['elapsed_including_wait_and_flush_s']},indent=2))
