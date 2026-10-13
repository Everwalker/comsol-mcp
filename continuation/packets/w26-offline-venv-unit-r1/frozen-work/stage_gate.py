"""Under sandbox, prove original loopback network denial then exec one fixed command."""
import argparse,errno,json,os,socket,sys,hashlib
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent));from common import raw_file,publish,Refusal

def network_denial(port,connector=socket.create_connection):
 try:
  with connector(('127.0.0.1',port),timeout=1):return {'status':'FAIL_CONNECTED_TO_HELD_PARENT_LISTENER'}
 except OSError as exc:
  return {'status':'PASS_ACTUAL_EPERM' if exc.errno in (errno.EPERM,errno.EACCES) else 'UNKNOWN_NETWORK_RULE_NOT_PROVEN','errno':exc.errno,'type':type(exc).__name__,'detail':str(exc)}

def execute(plan_path,plan_sha,label,port,execve=os.execve,connector=socket.create_connection):
 raw,record=raw_file(plan_path,2097152)
 if record['sha256']!=plan_sha:raise Refusal('original accepted command plan changed')
 p=json.loads(raw);matches=[q for q in p['commands'] if q['label']==label]
 if len(matches)!=1:raise Refusal('stage not exactly named')
 cmd=matches[0];root=Path(p['root']);output=root/'evidence'/('PROBE_'+label+'.json')
 probe=network_denial(port,connector);publish(output,probe)
 if probe['status']!='PASS_ACTUAL_EPERM':raise Refusal('kernel task network denial unproven; command not dispatched')
 publish(root/'evidence'/('EXEC_'+label+'.json'),{'command':cmd['argv'],'actual_stagegate_pid':os.getpid(),'uid':os.getuid(),'network_probe_first_record':raw_file(output)[1],'command_plan_first_record':record,'inherited_group':os.getpgrp(),'declared_extra_processes':0})
 execve(cmd['argv'][0],cmd['argv'],dict(os.environ))
def main():
 p=argparse.ArgumentParser();p.add_argument('--plan',type=Path,required=True);p.add_argument('--plan-sha',required=True);p.add_argument('--label',required=True);p.add_argument('--port',type=int,required=True);a=p.parse_args()
 execute(a.plan,a.plan_sha,a.label,a.port)
if __name__=='__main__':main()
