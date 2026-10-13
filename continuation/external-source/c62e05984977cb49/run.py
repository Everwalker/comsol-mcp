import json,os,pathlib,subprocess,sys,time,hashlib
p=pathlib.Path(__file__).parent;request=json.loads((p/'request.json').read_text());e=pathlib.Path(request['source_freeze']).parent;sys.path.insert(0,str(e))
from security_snapshot._host_scoped_provider import verify_private_path_security
verify_private_path_security(p,directory=True,expected_identity=request['security']['identity']);repo=e.parents[3]
freeze=pathlib.Path(request['source_freeze']);review=pathlib.Path(request['root_review'])
assert hashlib.sha256(freeze.read_bytes()).hexdigest()==request['source_freeze_sha256'];assert hashlib.sha256(review.read_bytes()).hexdigest()==request['root_review_sha256']
for row in json.loads(freeze.read_text())['source_and_context_files']:
 f=repo/row['path'];assert f.stat().st_size==row['bytes'];assert hashlib.sha256(f.read_bytes()).hexdigest()==row['sha256']
result={'argv':request['argv'],'started_unix':time.time(),'wrapper_pid':os.getpid(),'uid':os.getuid(),'wall_cap_s':request['wall_cap_s']}
stdout=os.open(p/'stdout.raw',os.O_WRONLY|os.O_APPEND|os.O_NOFOLLOW);stderr=os.open(p/'stderr.raw',os.O_WRONLY|os.O_APPEND|os.O_NOFOLLOW)
try:
 start=time.monotonic();result['before_child_Popen_monotonic']=start
 child=subprocess.Popen(request['argv'],stdout=stdout,stderr=stderr,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'));result['child_pid']=child.pid
 inventory=subprocess.run(['/bin/ps','-p',str(child.pid),'-o','pid=,ppid=,uid=,lstart=,command='],capture_output=True,text=True)
 result['initial_owned_process_inventory']={'argv':inventory.args,'rc':inventory.returncode,'stdout':inventory.stdout,'stderr':inventory.stderr}
 with (p/'owned_process_start.json').open('x') as f:json.dump(result,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
 print(json.dumps({'status':'STARTED','wrapper_prefix':str(p),'wrapper_pid':os.getpid(),'child_pid':child.pid}),flush=True)
 result['rc']=child.wait();result['after_complete_child_wait_monotonic']=time.monotonic()
finally:
 os.fsync(stdout);os.fsync(stderr);os.close(stdout);os.close(stderr)
result['after_wait_and_raw_log_flush_monotonic']=time.monotonic();result['elapsed_including_wait_and_log_flush_s']=result['after_wait_and_raw_log_flush_monotonic']-start
result['finished_unix']=time.time();result['within_wall_cap']=result['elapsed_including_wait_and_log_flush_s']<=request['wall_cap_s']
try:
 prefix=pathlib.Path(request['input_prefix']);guard=json.loads((prefix/'RESUME_ATTEMPT_R2_GUARD.json').read_text());source_result=json.loads((prefix/'evidence'/guard['attempt']/'resume.result.json').read_text());result['source_result']=source_result
 cleanup=source_result.get('cleanup_complete') is True and source_result.get('within_wall_cap') is True and source_result.get('status')!='UNKNOWN_CAP_OR_CLEANUP_NOT_CONFIRMED'
except Exception as exc:
 cleanup=False;result['source_result_error']=str(exc)
result['scope_cleanup_confirmed']=cleanup
if not result['within_wall_cap'] or not cleanup:result['status']='UNKNOWN_CAP_OR_CLEANUP_NOT_CONFIRMED'
else:result['status']='TERMINAL_PASS' if result['rc']==0 else 'TERMINAL_FAIL_RETAINED'
with (p/'result.json').open('x') as f:json.dump(result,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
print(json.dumps({'status':result['status'],'rc':result['rc'],'within_wall_cap':result['within_wall_cap'],'elapsed_s':result['elapsed_including_wait_and_log_flush_s'],'wrapper_prefix':str(p)}),flush=True)
sys.exit(result['rc'] if result['status']!='UNKNOWN_CAP_OR_CLEANUP_NOT_CONFIRMED' else 3)
