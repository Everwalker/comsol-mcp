"""Same original PID, numeric deny-network gate for the closed app lifecycle captures."""
import argparse,os,stat,hashlib
from pathlib import Path
# Standalone -I/-S stage: no repository import path or wheel code is admitted.
source=Path(__file__).with_name('w26_dependency_stage.py')
fd=os.open(source,os.O_RDONLY|os.O_NOFOLLOW)
try:
    first=os.fstat(fd);raw=os.read(fd,1048577);last=os.fstat(fd);named=source.lstat()
    key=lambda v:(v.st_dev,v.st_ino,v.st_uid,v.st_mode,v.st_nlink,v.st_size,v.st_mtime_ns,v.st_ctime_ns)
    if not stat.S_ISREG(first.st_mode) or first.st_uid!=os.getuid() or first.st_nlink!=1 or first.st_mode&0o022 or len(raw)!=first.st_size or key(first)!=key(last) or key(first)!=key(named) or hashlib.sha256(raw).hexdigest()!='4ecd63c0749bad8b98cb3a062f11c98f0d658664d335a6d30a1e9df8565e4880':raise RuntimeError('bound existing numeric denial/bootstrap source differs')
finally:os.close(fd)
definitions={'__name__':'w26_app_readonly_stage','__file__':str(source)}
exec(compile(raw,str(source),'exec'),definitions)
read_context=definitions['read_context'];network_denial=definitions['network_denial']

def execute(path,sha,index,port):
    context,h=read_context(path,sha);root=Path(context['unit_root']);h['secure_dir'](root,context['root_identity'])
    if context['activity_kind']!='CURRENT_APPLICATION_LIFECYCLE' or type(index) is not int or index!=0 or len(context['commands'])!=1 or not 1<=port<=65535:
        raise RuntimeError('closed lifecycle stage/owned port refused')
    stage=context['commands'][index];probe=network_denial(port)
    first=h['publish'](root/'evidence'/('PROBE_'+stage['label']+'.json'),probe)
    if probe['status']!='PASS_ACTUAL_EPERM':raise RuntimeError('actual numeric network denial unproven; no build dispatch')
    h['publish'](root/'evidence'/('EXEC_'+stage['label']+'.json'),{'same_original_pid':os.getpid(),'uid':os.getuid(),'inherited_group':os.getpgrp(),'network_probe_first_record':first,'argv':stage['argv'],'extra_processes':0})
    os.chdir(context['outside']);os.execve(stage['argv'][0],stage['argv'],context['environment'])

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--context',required=True);p.add_argument('--sha',required=True);p.add_argument('--index',type=int,required=True);p.add_argument('--port',type=int,required=True);a=p.parse_args();execute(a.context,a.sha,a.index,a.port)
