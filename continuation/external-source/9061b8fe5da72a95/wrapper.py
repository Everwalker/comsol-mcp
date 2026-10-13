import os,json,subprocess,sys,time,hashlib
from pathlib import Path
root=Path(__file__).resolve().parent
entry=Path("/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2/repository/docs/full_project_continuation_v2/evidence/intel-builder-execution-r1/openssl359_successor_r2.py")
argv=["/private/tmp/comsol-v2-runtime-20261011-new/comsol-mcp-recovery-runtime-20261004-001/python312/bin/python3","-B","-u",str(entry),"--freeze-sha","dfbfb3d47fd331f10e87ac981a90c2adc966507ae8d11c134a1d38e60f1dd71f","--review-sha","d983bb8cf84127df761e3d91e77cf0db67cff45008f0dbd06438b378d97316b9"]
def durable(name,payload):
 fd=os.open(root/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 try:
  data=memoryview((json.dumps(payload,indent=2,sort_keys=True)+"\n").encode())
  while data:data=data[os.write(fd,data):]
  os.fsync(fd)
 finally:os.close(fd)
sys.path.insert(0,str(entry.parent))
from openssl359_successor_r2 import frozen_digest,source_gate
pins=[
 (entry.parent/'OPENSSL359_SOURCE_FREEZE_R2.json','dfbfb3d47fd331f10e87ac981a90c2adc966507ae8d11c134a1d38e60f1dd71f'),
 (entry.parent/'ROOT_OPENSSL359_SOURCE_REVIEW_R2.json','d983bb8cf84127df761e3d91e77cf0db67cff45008f0dbd06438b378d97316b9'),
 (Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2/local-preservation/openssl359-r2-independent-source-review/REVIEW_RECEIPT.json'),'a8d93e7a5fea6f3b0f7c4803cd70281ec389a3f414496cf2fd4f0f79e817cda4'),
 (entry.parent/'ROOT_OPENSSL359_R2_ONCE_DISPATCH_BINDING.json','686f96561bb66ff08ca4ee1d20b32fbd18441290cc184326f6ea87569e467458')]
verified=[]
for path,expected in pins:
 actual=frozen_digest(path)
 if actual['sha256']!=expected:raise SystemExit('exact once admission pin changed')
 verified.append(dict(path=str(path),**actual))
source_gate(pins[0][1],pins[1][1])
durable('START_INPUT_PINS.json',{'verified':verified,'whole_source_gate':'PASS_ACTUAL_BEFORE_POPEN','network':0,'systeminstall':0,'purex86execution':0,'COMSOL':0})
durable("argv.json",{"argv":argv,"cwd":str(entry.parent),"entry_sha256":hashlib.sha256(entry.read_bytes()).hexdigest(),"wrapper_pid":os.getpid(),"uid":os.getuid()})
prior=subprocess.run(["/bin/ps","-p","96619,96621,96666,5246,5251,5255,7650,65836,65855,65926,73840,73844,73848,42995,43014,43126,43159,52153,53372,53374,53375,53376,8257,8275,59819,59907","-o","pid=,ppid=,uid=,lstart=,command="],capture_output=True,text=True,timeout=10)
durable("PRIOR_OWNED_PID_ABSENCE.json",{"argv":prior.args,"rc":prior.returncode,"stdout":prior.stdout,"stderr":prior.stderr})
if prior.returncode!=1 or prior.stdout.strip() or prior.stderr.strip():raise SystemExit("prior owned PID absence unresolved; no launch")
logs=[os.open(root/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600) for name in ("stdout","stderr")]
start=time.monotonic();process=None;rc=None;errors=[]
try:
 process=subprocess.Popen(argv,cwd=entry.parent,env=dict(os.environ,PYTHONDONTWRITEBYTECODE="1"),stdout=logs[0],stderr=logs[1],start_new_session=True)
 try:
  durable("spawn.json",{"actual_pid":process.pid,"argv":argv,"uid":os.getuid(),"actual_pgid":os.getpgid(process.pid),"actual_sid":os.getsid(process.pid),"started_unix":time.time()})
  ps=subprocess.run(["/bin/ps","-p",str(process.pid),"-o","pid=,ppid=,uid=,lstart=,command="],capture_output=True,text=True,timeout=10)
  durable("birth.json",{"argv":ps.args,"rc":ps.returncode,"stdout":ps.stdout,"stderr":ps.stderr})
 except BaseException as exc:errors.append(repr(exc));print("OWNER_DIAGNOSTIC_FAULT retaining same handle",repr(exc),flush=True)
 print(json.dumps({"root":str(root),"wrapper_pid":os.getpid(),"owner_pid":process.pid,"status":"RUNNING"}),flush=True)
 while True:
  try:rc=process.wait(timeout=10);break
  except subprocess.TimeoutExpired:continue
  except BaseException as exc:errors.append(repr(exc));print("SAME_OWNER_WAIT_ERROR",repr(exc),flush=True)
finally:
 for fd in logs:
  for operation in (os.fsync,os.close):
   try:operation(fd)
   except BaseException as exc:errors.append(repr(exc))
 terminal={"actual_pid":process.pid if process else None,"actual_wait_rc":rc if process and process.returncode is not None else None,"elapsed_including_wait_and_flush_s":time.monotonic()-start,"diagnostic_errors":errors,"same_handle_observed":True}
 try:durable("terminal.json",terminal)
 except BaseException as exc:print("TERMINAL_WRITE_ERROR",repr(exc),repr(terminal),flush=True)
 print(json.dumps(terminal),flush=True)
sys.exit(rc if not errors else 1)
