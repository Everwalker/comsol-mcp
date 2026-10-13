import hashlib,json,os,subprocess,sys,tempfile,time
from pathlib import Path
HERE=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2/repository/docs/full_project_continuation_v2/evidence/intel-builder-execution-r1')
sys.path.insert(0,str(HERE))
from security_snapshot._host_scoped_provider import verify_private_path_security
PYTHON='/private/tmp/comsol-v2-runtime-20261011-new/comsol-mcp-recovery-runtime-20261004-001/python312/bin/python3'
PREFIX=Path('/private/var/folders/4d/6pphmzkx5szf8dyqx7zhqh1r0000gn/T/comsol-intel-inputs-r1-obneuyy4')
expected={'cpython_universal2_stages.py':'735fe8009e23f74db836ea058fb45c8feade776a471afca69c16dc6347fe1947','CPYTHON_STAGE_SOURCE_FREEZE_R4_3.json':'2e50f5f493011b3a855c03ca5bf31adc8964f337440959bf1e0ac4eb1d500cf7','ROOT_CPYTHON_STAGE_SOURCE_REVIEW_R4_3.json':'b6cd735a42a423244972aa548187434adcab95db9666b850c5de4e20453b7122'}
for name,sha in expected.items():
 assert hashlib.sha256((HERE/name).read_bytes()).hexdigest()==sha,(name,'changed')
parent=PREFIX.parent;verify_private_path_security(parent,directory=True);verify_private_path_security(PREFIX,directory=True)
wrapper=Path(tempfile.mkdtemp(prefix='comsol-cpython-r43-wrapper-',dir=parent));security=verify_private_path_security(wrapper,directory=True)
def save(name,data):
 with (wrapper/name).open('x') as f:json.dump(data,f,indent=2,sort_keys=True);f.write('\n');f.flush();os.fsync(f.fileno())
 fd=os.open(wrapper,os.O_RDONLY|os.O_DIRECTORY)
 try:os.fsync(fd)
 finally:os.close(fd)
def birth(pid):
 r=subprocess.run(['/bin/ps','-p',str(pid),'-o','pid=,ppid=,uid=,lstart=,command='],capture_output=True,text=True,env=dict(os.environ,LC_ALL='C'))
 return {'argv':r.args,'rc':r.returncode,'stdout':r.stdout,'stderr':r.stderr}
save('OWNER.json',{'pid':os.getpid(),'uid':os.getuid(),'argv':sys.argv,'security':security,'source_hashes':expected,'birth':birth(os.getpid())})
print(json.dumps({'wrapper':str(wrapper),'pid':os.getpid(),'status':'STARTED'}),flush=True)
started=time.monotonic();outcomes=[]
for stage in ('configure','headers','build','install'):
 argv=[PYTHON,'-B',str(HERE/'cpython_universal2_stages.py'),'--prefix',str(PREFIX),'--stage',stage,'--execute-stage']
 save(stage+'.request.json',{'argv':argv,'cwd':str(HERE),'source_hashes':expected,'started_unix':time.time(),'offline':True})
 with (wrapper/(stage+'.stdout')).open('xb') as out,(wrapper/(stage+'.stderr')).open('xb') as err:
  child=subprocess.Popen(argv,cwd=HERE,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'),stdout=out,stderr=err,start_new_session=True)
  save(stage+'.spawn.json',{'pid':child.pid,'uid':os.getuid(),'parent_pid':os.getpid(),'argv':argv,'birth':birth(child.pid)})
  print(json.dumps({'stage':stage,'owned_pid':child.pid,'status':'RUNNING'}),flush=True)
  while True:
   try:rc=child.wait(timeout=10);break
   except subprocess.TimeoutExpired: print(json.dumps({'stage':stage,'pid':child.pid,'status':'SAME_HANDLE_WAIT'}),flush=True)
  out.flush();os.fsync(out.fileno());err.flush();os.fsync(err.fileno())
 outcome={'stage':stage,'rc':rc,'pid':child.pid,'wait_complete':True,'raw_logs_flushed':True,'elapsed_s':time.monotonic()-started}
 save(stage+'.terminal.json',outcome);outcomes.append(outcome)
 print(json.dumps(outcome),flush=True)
 if rc:break
 receipt=PREFIX/'cpython-universal2-build'/(stage+'.PASS.json')
 assert receipt.is_file(),('stage has no PASS',stage)
save('RESULT.json',{'status':'STAGES_COMMANDS_COMPLETE' if len(outcomes)==4 and all(x['rc']==0 for x in outcomes) else 'TERMINAL_STAGE_FAILURE','stages':outcomes,'elapsed_s':time.monotonic()-started,'intel_runtime_executed':False,'wrapper':str(wrapper)})
sys.exit(0 if len(outcomes)==4 and all(x['rc']==0 for x in outcomes) else 1)
