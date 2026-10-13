from pathlib import Path
import datetime, difflib, hashlib, json, os, shlex, stat, subprocess, sys
C=Path(__file__).resolve().parents[2]
B=C.parents[1]
P=Path(__file__).resolve().parent
sys.path.insert(0,str(C))
from tools import run_native_w23_saved_mph_diagnostic as runner
E=C/'docs/full_project_execution/w23_overlap/evidence/native-run-20261013-018-saved-mph-zero-solve-candidate20-r10-final1'
F=E/'freeze.json'
sha=runner.sha256_file
ref=lambda p:{'path':str(p),'bytes':p.stat().st_size,'sha256':sha(p)}
def write(path,obj):
 data=(json.dumps(obj,indent=2,sort_keys=True)+'\n').encode()
 fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 try:os.write(fd,data);os.fsync(fd)
 finally:os.close(fd)
 return ref(path)
assert sha(F)=='76c4a936da5f0d90354ce9f7046fc55e55c44392cbfbf8aa3ffa8cc031a70d89'
f=runner._validate_freeze(F,sha(F))
assert f['activity_id']==runner.ACTIVITY_ID and runner.CANDIDATE_REVISION=='candidate20-r10'
packet=json.loads((E/'ACTIVITY018_REVIEW_PACKET.json').read_text())
work=Path(f['work_path']);native=Path(f['execution_evidence_path']);approval=Path(f['approval_path_expected']);capture=E/'native-launch-capture-activity018-001'
stdout=capture/'launcher.stdout.bin';stderr=capture/'launcher.stderr.bin'
cli=[native.with_name(native.name+'.cli.stdout.bin'),native.with_name(native.name+'.cli.stderr.bin')]
paths=[work,native,capture,approval,*cli]
absent=[]
for p in paths:
 assert not p.exists() and not p.is_symlink(),str(p)
 assert p.parent.is_dir() and not p.parent.is_symlink() and p.parent.resolve()==p.parent
 assert p.parent.stat().st_uid in ({0,os.getuid()} if p==work else {os.getuid()})
 absent.append({'path':str(p),'exists':False,'is_symlink':False,'ordinary_canonical_owner_ancestor':str(p.parent)})
runner._validate_fresh_execution_paths(f,work,native)
argv=packet['native_invocation']['argv']
assert argv==runner._native_argv(F,sha(F),approval,runner.SOURCE_MODEL,work,native)
invocation={key:packet['native_invocation'][key] for key in ('argv','cwd','environment')}
script=P/'launch-activity018-r10.zsh'
quote=shlex.quote
lines=['set -e','setopt noclobber']
for path in (work,native,capture):lines+=['test ! -e '+quote(str(path)),'test ! -L '+quote(str(path))]
lines+=['test -f '+quote(str(approval)),'test ! -L '+quote(str(approval)),
        'mkdir '+quote(str(capture)),'cd '+quote(str(C)),
        'exec '+shlex.join(argv)+' > '+quote(str(stdout))+' 2> '+quote(str(stderr))]
body='\n'.join(lines)+'\n'
original=B/'local-preservation/w23-saved-mph-science-readback-20261012-candidate20-r8-complete1/root-review-packet-final2/native-capture-supplement017-001/launch-activity017-r8.zsh'
assert sha(original)=='44d827cac5e0a18b1b7668fa8559acc066d063df97e7e283f0d64fdda9461001'
oldroot=original.parents[2]
oldmeta=json.loads((original.parent/'NATIVE_CAPTURE_INVOCATION_ACTIVITY017_R8.json').read_text())
oldE=Path(oldmeta['freeze']['path']).parent
translated=original.read_text().replace(str(oldE),str(E)).replace(str(oldroot),str(C)).replace('20261012-017-candidate20-r8','20261013-018-candidate20-r10').replace('bc73dcb4484ded268ecc24f2ccee187611c8e1f2602b526cae935591a290369f',sha(F)).replace('ROOT_APPROVAL_ACTIVITY017','ROOT_APPROVAL_ACTIVITY018').replace('native-launch-capture-activity017-001','native-launch-capture-activity018-001')
assert translated==body,'script must be original capture scheme with only frozen identity/path/hash substitutions'
script.write_text(body)
source=B/'repository/docs/full_project_continuation_v2/evidence/w23_saved_mph_candidate20_r10/ROOT_SOURCE_REVIEW_ACTIVITY018_R10.json'
assert sha(source)=='fb69b3ea55495f65b612561f4f1a44a9fbcb2f7b8f399e8fbf6424d59e1bbe7d'
root_review=ref(source)
expected={
 'status':'ROOT_APPROVED_FOR_EXACT_FROZEN_CANDIDATE','activity_id':runner.ACTIVITY_ID,
 'candidate_id':runner.CANDIDATE_ID,'candidate_revision':runner.CANDIDATE_REVISION,
 'freeze_sha256':sha(F),'source_manifest_sha256':f['source_manifest_sha256'],
 'native_argv_sha256':runner.sha256_json(argv),
 'max_owned_server_processes':1,'max_worker_sessions':1,'max_gui_processes':0,
 'max_study_run_calls':0,'max_solver_run_calls':0,'max_seconds_from_server_birth':1800,
 'cleanup_reserve_seconds':120,'retry_unknown_or_nonterminal_worker_request':False,
 'read_only_call_plan_sha256':runner.sha256_json(f['read_only_call_plan']),
 'exact_native_invocation':invocation}
runner._validate_approval(expected,f,sha(F),argv)
blue=write(P/'ROOT_APPROVAL_EXPECTED_FIELDS_REFERENCE_ONLY.json',{
 'status':'REFERENCE_ONLY_NOT_ROOT_APPROVAL_NOT_HUMAN_AUTHORIZED','expected_approval_fields':expected,
 'expected_path':str(approval),'mandatory_additional_binding':'Root exact source/reviewer/capture/script/argv/cwd/env/freshps-lsof/resumeCAS/directhumanonce, no standalone approval from this blueprint.'})
check=subprocess.run(['/bin/zsh','-n',str(script)],text=True,capture_output=True,check=False)
(P/'zsh-syntax.stdout.txt').write_text(check.stdout);(P/'zsh-syntax.stderr.txt').write_text(check.stderr)
assert check.returncode==0
material=runner.science._check_offline_receipts(F,f)
assert sum(row['class_count'] for row in material['compiled_class_outputs'].values())==19
assert all(Path(row['class_dir']).is_relative_to(E) for row in material['compiled_class_outputs'].values())
classes=[ref(p) for p in sorted((E/'offline_gates/compile').rglob('*.class'))]
assert len(classes)==19
state=B/'repository/docs/full_project_continuation_v2/state/RESUME.json';before=state.read_bytes()
assert hashlib.sha256(before).hexdigest()=='c06de4050f17ad282510bc382471721016381c2db0dabfd19e15d1beced10022'
assert json.loads(before)['active_jobs']==[]
dry=write(P/'ACTUAL_PRODUCTION_PREFLIGHT_REFERENCE_ONLY.json',{
 'status':'PASS_REAL_CURRENT_FREEZE_SOURCE_CLASS_RELATIVE_PATH_APPROVAL_REFERENCE_ARGV_FRESHPATHS_ZSH_SYNTAX_ONLY',
 'freeze':ref(F),'root_source_review':root_review,'source_closure_count':len(f['source_files']),
 'actual_production_compile_preflight':material,'actual19class_refs':classes,
 'approval_reference_four_arg_validator':'PASS','actual_fresh_execution_paths':'PASS',
 'new_paths':absent,'zsh_syntax':{'returncode':check.returncode,'stdout':ref(P/'zsh-syntax.stdout.txt'),'stderr':ref(P/'zsh-syntax.stderr.txt')},
 'production_server_worker_inventory':'NOT_RUN_THIS_METADATA_STAGE_REQUIRED_FRESH_BEFORE_LAUNCH',
 'native':'NOT_RUN','approval_issued':False,'CAS':'NOT_MODIFIED','RESUME_sha256':sha(state)})
reviewer_path=B/'local-preservation/w23-activity018-r10-independent-source-review/REVIEW_RECEIPT.json'
reviewer={'status':'FINAL_RECEIPT_PENDING_REQUIRED_BEFORE_ROOT_ADMISSION','expected_path':str(reviewer_path)}
if reviewer_path.is_file():
 reviewer=ref(reviewer_path)
meta=write(P/'NATIVE_CAPTURE_INVOCATION_ACTIVITY018_R10.json',{
 'schema':'W23_ACTIVITY018_R10_FIXED_NATIVE_CAPTURE_REFERENCE/1',
 'status':'PREPARED_SOURCE_METADATA_ONLY_NOT_NATIVE_ADMITTED_NOT_HUMAN_AUTHORIZED',
 'created_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
 'activity_id':runner.ACTIVITY_ID,'candidate_id':runner.CANDIDATE_ID,'candidate_revision':runner.CANDIDATE_REVISION,
 'freeze':ref(F),'source_manifest':ref(E/'source_manifest.json'),'root_source_review':root_review,
 'independent_source_review':reviewer,'full_source_sdk_software_packet':ref(C/'root-review-packet/ROOT_REVIEW_PACKET_ACTIVITY018_R10.json'),
 'actual_preflight_reference':dry,'script':ref(script),'exact_native_invocation':invocation,
 'launcher_tool_argv':['/bin/zsh',str(script)],'launcher_cwd':str(C),
 'capture_directory':str(capture),'capture_stdout_path':str(stdout),'capture_stderr_path':str(stderr),
 'execution_cli_capture_paths':[str(p) for p in cli],'approval_blueprint':blue,'root_approval_expected':str(approval),
 'new_paths_verified_absent':[str(p) for p in paths],'limits':f['limits'],
 'input':f['input'],'read_only_call_plan':f['read_only_call_plan'],
 'class_material':'Actual19freeze.parent/offline_gates/compile class outputs present and exact originalSHA; no metadata parent redirect.',
 'capture_scheme':'Original017 zsh noclobber/mkdir plus exactexec redirection, only reviewed fixed018 identity/path/hash substitutions. Actualstdout/stderr including zero bytes must be retained; runner also O_EXCL tees CLI streams.',
 'owner_and_once':'Same original tool handle and exact strong Server/Worker Popen ownership; at most1server1worker/0GUI;1800incl120cleanup. Original request/response ledger and rawstores retained; firstFAIL/UNKNOWN stops every later slot/activity includingW24; no replays/retries/duplicate keys.',
 'mandatory_before_launch':['DirecthumanexactActivity018once authorization;old017CLIonce consumed and not reusable','Root current reviewed source and independent frozenreceipt','Fresh production ps/lsof probes_ok/quiescent with source/input/material/path rehash','Root exactonce approval plus unique expectedSHA RESUME CAS reservation beforebirth','Normal originaltool approval,exactthisscript once,alloriginalhandle observations','FirstFAIL/UNKNOWN preserve actualraw/MPH/stores/logs/ledger,typedexactcleanup/freshabsence thenimmutableterminalCAS;no automaticW24'],
 'prior017':'ACTUAL_CLI_FAIL_PRE_SERVER_BIRTH;onceconsumed,fullpreservation andoldrejection unchanged;not retried.',
 'scientific_acceptance':'NOT_ACCEPTED_DIAGNOSTIC_READBACK_ONLY','native':'NOT_RUN','CAS':'NOT_MODIFIED','RESUME_sha256':sha(state),
 'W24':'Existing user authorization retained withfirstFAILhold;no dispatch bythisreference.'})
diff=P/'original017_to_fixed018_capture.diff'
diff.write_text(''.join(difflib.unified_diff(original.read_text().splitlines(True),body.splitlines(True),fromfile='original017capture',tofile='fixed018r10capture')))
assert state.read_bytes()==before
print(json.dumps({'capture_metadata':meta,'script':ref(script),'blueprint':blue,'actualpreflight':dry,'capture_diff':ref(diff),'approval':'NOT_CREATED','native':'NOT_RUN','CAS':'NOT_MODIFIED'}))
