"""Same-PID network denial precondition for the eight current dependency commands."""
import argparse,ast,errno,hashlib,json,os,socket,stat,sys
from pathlib import Path

def common(context):
    record=context['legacy_sources']['common']
    fd=os.open(record['path'],os.O_RDONLY|os.O_NOFOLLOW)
    try:
        first=os.fstat(fd);raw=os.read(fd,1048577);last=os.fstat(fd);named=Path(record['path']).lstat()
        key=lambda x:(x.st_dev,x.st_ino,x.st_uid,x.st_mode,x.st_nlink,x.st_size,x.st_mtime_ns,x.st_ctime_ns)
        if not stat.S_ISREG(first.st_mode) or first.st_uid!=os.getuid() or first.st_nlink!=1 or first.st_mode&0o022 or len(raw)>1048576 or key(first)!=key(last) or key(first)!=key(named) or hashlib.sha256(raw).hexdigest()!=record['sha256']:
            raise RuntimeError('exact common source input changed')
    finally:os.close(fd)
    namespace={'__name__':'w26_readonly_common','__file__':record['path']}
    exec(compile(raw,record['path'],'exec'),namespace)
    namespace['raw_file'](Path(record['path']),expected=record)
    return namespace

def read_context(path,sha):
    # Bootstrap only the existing strict common definitions, then verify context.
    common_path=Path(__file__).parents[1]/'continuation/packets/w26-offline-venv-unit-r1/frozen-work/common.py'
    fd=os.open(common_path,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        first=os.fstat(fd);raw=os.read(fd,1048577);last=os.fstat(fd);named=common_path.lstat()
        key=lambda s:(s.st_dev,s.st_ino,s.st_uid,s.st_mode,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
        if not stat.S_ISREG(first.st_mode) or first.st_uid!=os.getuid() or first.st_nlink!=1 or first.st_mode&0o022 or len(raw)!=first.st_size or len(raw)>1048576 or key(first)!=key(last) or key(first)!=key(named):raise RuntimeError('common bootstrap sameFD identity refused')
    finally:os.close(fd)
    if hashlib.sha256(raw).hexdigest()!='94a5b742551ee9ef21224ee1abd3d415f73a1fff1447559d600242f6a7bf0c8f':raise RuntimeError('original common definition hash differs')
    namespace={'__name__':'w26_readonly_common','__file__':str(common_path)};exec(compile(raw,str(common_path),'exec'),namespace)
    data,record=namespace['raw_file'](Path(path),maximum=4194304)
    if record['sha256']!=sha:raise RuntimeError('current source-bound stage context changed')
    context=json.loads(data);checked=common(context)
    return context,checked

def network_denial(port,connector=socket.create_connection):
    try:
        with connector(('127.0.0.1',port),timeout=1):return {'status':'FAIL_CONNECTED_TO_HELD_PARENT_LISTENER'}
    except OSError as exc:
        return {'status':'PASS_ACTUAL_EPERM' if exc.errno in (errno.EPERM,errno.EACCES) else 'UNKNOWN_NETWORK_RULE_NOT_PROVEN',
                'errno':exc.errno,'type':type(exc).__name__,'detail':str(exc)}

def execute(path,sha,index,port,*,connector=socket.create_connection,execve=os.execve):
    context,helper=read_context(path,sha);root=Path(context['unit_root']);helper['secure_dir'](root,context['root_identity'])
    if not isinstance(index,int) or not 0<=index<8 or not 1<=port<=65535:raise RuntimeError('current fixed stage index/owned port refused')
    stage=context['commands'][index];probe=network_denial(port,connector)
    probe_record=helper['publish'](root/'evidence'/('PROBE_'+stage['label']+'.json'),probe)
    if probe['status']!='PASS_ACTUAL_EPERM':raise RuntimeError('numeric OS network denial unproven; effect not dispatched')
    helper['publish'](root/'evidence'/('EXEC_'+stage['label']+'.json'),{'same_original_pid':os.getpid(),'uid':os.getuid(),
        'inherited_group':os.getpgrp(),'network_probe_first_record':probe_record,'argv':stage['argv'],'extra_processes':0})
    os.chdir(context['outside']);execve(stage['argv'][0],stage['argv'],context['environment'])

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--context',required=True);parser.add_argument('--sha',required=True)
    parser.add_argument('--index',type=int,required=True);parser.add_argument('--port',type=int,required=True);args=parser.parse_args()
    execute(args.context,args.sha,args.index,args.port)
