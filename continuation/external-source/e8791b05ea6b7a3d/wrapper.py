import os,json,subprocess,sys,time,hashlib
from pathlib import Path
root=Path(__file__).resolve().parent
entry=Path("/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2/repository/docs/full_project_continuation_v2/evidence/intel-builder-execution-r1/tls_successor_r2.py")
argv=["/private/tmp/comsol-v2-runtime-20261011-new/comsol-mcp-recovery-runtime-20261004-001/python312/bin/python3","-B","-u",str(entry),"--freeze-sha","3a4362e8ae7eaf5d70bf7eec8c05d0b9fa688f74c1d275807ad70fef39c45e91","--review-sha","6c94ce2f12d5ab6ba44ede49710cbb8a4c2e90c73468262b269695870576001a"]
def durable(name,payload):
 fd=os.open(root/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 try:
  data=memoryview((json.dumps(payload,indent=2,sort_keys=True)+"\n").encode())
  while data:data=data[os.write(fd,data):]
  os.fsync(fd)
 finally:os.close(fd)
durable("argv.json",{"argv":argv,"cwd":str(entry.parent),"entry_sha256":hashlib.sha256(entry.read_bytes()).hexdigest(),"wrapper_pid":os.getpid(),"uid":os.getuid()})
prior=subprocess.run(["/bin/ps","-p","8257,8275,59819,59907","-o","pid=,ppid=,uid=,lstart=,command="],capture_output=True,text=True,timeout=10)
durable("PRIOR_OWNED_PID_ABSENCE.json",{"argv":prior.args,"rc":prior.returncode,"stdout":prior.stdout,"stderr":prior.stderr})
if prior.returncode!=1 or prior.stdout.strip() or prior.stderr.strip():raise SystemExit("prior owned PID absence unresolved; no launch")
logs=[os.open(root/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600) for name in ("stdout","stderr")]
start=time.monotonic();process=None;errors=[]
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
