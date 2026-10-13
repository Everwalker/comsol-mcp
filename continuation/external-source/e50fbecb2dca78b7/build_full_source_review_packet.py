from pathlib import Path
import json,os,hashlib,ast,difflib,stat,datetime
C=Path(__file__).resolve().parents[1];B=C.parents[1];P=C/'root-review-packet';old=B/'local-preservation/w23-saved-mph-science-readback-20261013-candidate20-r11';E=next(C.glob('docs/**/freeze.json')).parent
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
ref=lambda p:{'path':str(p),'bytes':p.stat().st_size,'sha256':sha(p)}
def save(name,obj):
 p=P/name;data=(json.dumps(obj,indent=2,sort_keys=True)+'\n').encode();fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
 return ref(p)
f=json.loads((E/'freeze.json').read_text());previous=json.loads(next(old.glob('docs/**/freeze.json')).read_text());source=f['source_files'];changed=[];same=[];astcount=0
for rel,row in source.items():
 p=Path(row['path']);assert sha(p)==row['sha256']
 if rel.endswith('.py'):ast.parse(p.read_bytes());astcount+=1
 before=previous['source_files'].get(rel)
 if before and before['sha256']==row['sha256']:same.append(rel)
 else:
  oldp=old/rel;diff=P/'source-diff'/ (rel+'.diff');diff.parent.mkdir(parents=True,exist_ok=True);a=oldp.read_text().splitlines(True) if oldp.exists() else [];diff.write_text(''.join(difflib.unified_diff(a,p.read_text().splitlines(True),fromfile='frozenr11/'+rel,tofile='r12/'+rel)));changed.append({'relative':rel,'kind':'MODIFIED' if before else 'ADDED','current':ref(p),'old':ref(oldp) if before else None,'diff':ref(diff)})
assert len(source)==409 and len(changed)==7 and len(same)==402
for rel in ['comsol_mcp/_ephemeral_result_ticket.py','comsol_mcp/_execution_service.py','comsol_mcp/_execution_contract.py','comsol_mcp/_java_worker.py','comsol_mcp/worker_java/PersistentComsolWorker.java','tools/w23_te_science_protocol.py','tools/java/NativeW23TEScienceFixture.java']:
 assert (old/rel).read_bytes()==(C/rel).read_bytes()
regular=[];links=[]
for top in [E,P]:
 for directory,dirs,files in os.walk(top,followlinks=False):
  for name in list(dirs)+files:
   p=Path(directory)/name;mode=p.lstat().st_mode
   if stat.S_ISLNK(mode):
    links.append({'path':str(p),'target':os.readlink(p),'followed':False})
    if name in dirs:dirs.remove(name)
   elif stat.S_ISREG(mode):regular.append(ref(p))
controls=E/'offline_gates/actual019_private_ticket_controls';hz=json.loads((controls/'new-hz-complete-private-scope.json').read_text());oldhz=json.loads((controls/'old-r11-hz-danger-reproduction.json').read_text());assert hz['consume'][-1]['actual_original_consume_result'] is True and oldhz['consume'][-1]['actual_original_consume_result'] is False
orig19=list(controls.glob('original19-fault-*.json'));assert len(orig19)==20 and all(json.loads(p.read_text())['injected'] for p in orig19)
slots=['new-hz-complete-private-scope','freq_GHz','fields_output_x8_321','fields_output_x8_641','fields_input_xminus10_321','fields_input_xminus10_641','mode_point_bmaInput','mode_point_bmaOutput'];assert all(json.loads((controls/(n+'.json')).read_text())['consume'][-1]['actual_original_consume_result'] is True for n in slots)
S=B/'repository/docs/full_project_continuation_v2/state/RESUME.json';assert sha(S)=='883cca696ccc02a7e02199f7bbf9928d0ece2d92355889ecdb34b671b42d70d1' and json.loads(S.read_text())['active_jobs']==[]
receipt={'schema':'ACTIVITY020_R12_FULL_SOURCE_SDK_SOFTWARE_REVIEW_PACKET/1','status':'READY_FOR_ROOT_AND_INDEPENDENT_SOURCE_REVIEW_NOT_NATIVE_ADMITTED','source_root':str(C),'source_count':len(source),'PythonAST':astcount,'whole_source':source,'source_changed_from_r11':changed,'unchanged402_r11_sources':same,'freeze':ref(E/'freeze.json'),'source_manifest':ref(E/'source_manifest.json'),'real_current_prepare_no_gate_injection':ref(P/'SOURCE_SDK_FULL_PREPARE_RECEIPT.json'),'SDK_prepare_tool_session':66035,'SDK_prepare_exit_code':0,'actual_full_current_tests':f['offline_preflight']['focused_tests'],'scope':'Original492 + new21 =513 actual current controls, no inherited test results. Original20 each injection fired (historical label original19 retained); new9 attached required-getter faults; eight point source scope consumePASS; old r11 actual019 danger reproduced.','current_strict_guard':f['offline_preflight']['focused_tests']['network_guard'],'actual19_newSDK_byteexact_original018':json.loads((E/'offline_gates/EXACT_19_ORIGINAL_CLASS_COMPARISON.json').read_text()),'actual_executable_preflight':ref(E/'executable_offline_material_check.json'),'original19_fault_captures':[ref(p) for p in orig19],'new_complete_private_scope_controls':[ref(p) for p in controls.glob('*.json')],'same8slot_limits_science_and_unknown_gates_unchanged':True,'production_delta':'Private defaultFalse emptyouter optional diagnostic flag in SolutionBinding/G3; scoped owned noselector original live independent count before generic binding; only strict attachedfreq success enables skip. Port flagFalse retains generic success+Storedcount. Java/Ticket/ExecutionService/SessionLedger unchanged.','recorded_vs_synthetic':'Hz all actual019 getter/data/unit/cleanup/snapshot replies preserved apart from documented source transport requestid/model identity/generation/handle rebasing. Other7 missing Interp unit/data/coordinate parts are explicitly synthetic source controls, not actual native/science evidence. Old source loaded from immutable r11 and bound below.','old_source_reproduction_dependency':ref(old/'comsol_mcp/_g3_results.py'),'whole_raw_regular_files':regular,'deliberate_negative_symlink_objects_not_followed':links,'debug_iterations_preserved':'First unguarded test-run failures/receiver rebinding diagnostics in root-review-packet/debug*. Production prepare513 allPASS guard active. Not relabeled as native.','root_source_repair_decision':ref(B/'local-preservation/w23-activity019-vs016-ticket-revision-source-diagnosis-20261013-001/ROOT_COMPLETE_SOURCE_REPAIR_DECISION.json'),'historical019_terminal_preserved':ref(B/'repository/docs/full_project_continuation_v2/evidence/w23_saved_mph_candidate20_r11/activity019-actual-terminal-001/PRESERVATION_RECEIPT.json'),'persistent_human_boundedreadback_scope':ref(B/'repository/docs/full_project_continuation_v2/evidence/w23_saved_mph_candidate20_r10/PERSISTENT_NATIVE_AUTHORIZATION_20261013.json'),'RESUME_unchanged_sha256':sha(S),'native':'NOT_RUN','new_native_CAS':'NOT_MODIFIED','new_root_approval':'NOT_CREATED','scientific_acceptance':'NOT_ACCEPTED','W24':'HOLD'}
print(json.dumps(save('ROOT_REVIEW_PACKET_ACTIVITY020_R12.json',receipt)))
