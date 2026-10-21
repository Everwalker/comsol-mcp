"""One Intel builder PID: original bootstrap, OS denial, then exact exec."""
import argparse, os, socket
from pathlib import Path
from tools.w26_dependency_stage import read_context, network_denial

def execute(path, sha, index, port, *, connector=socket.create_connection, execve=os.execve):
    context, helper = read_context(path, sha)
    if set(context) != {'schema','unit_root','root_identity','legacy_sources','commands'} or context['schema'] != 'w26-intel-derived-stage/1':
        raise RuntimeError('closed Intel stage context differs')
    if type(index) is not int or not 0 <= index < 8 or type(port) is not int or not 1 <= port <= 65535:
        raise RuntimeError('fixed eight-stage index/listener refused')
    if len(context['commands']) != 8: raise RuntimeError('eight literal commands required')
    root = Path(context['unit_root']); helper['secure_dir'](root, context['root_identity'])
    stage = context['commands'][index]
    if set(stage) != {'label','argv','cwd','environment','cap'} or not Path(stage['cwd']).is_relative_to(root/'work'):
        raise RuntimeError('Intel stage cwd/fields differ')
    probe = network_denial(port, connector)
    record = helper['publish'](root/'evidence'/('PROBE_'+str(index)+'.json'), probe)
    if probe['status'] != 'PASS_ACTUAL_EPERM': raise RuntimeError('actual same-PID OS network denial unproven')
    helper['publish'](root/'evidence'/('EXEC_'+str(index)+'.json'), {'pid':os.getpid(),'uid':os.getuid(),
        'pgid':os.getpgrp(),'probe':record,'argv':stage['argv'],'cwd':stage['cwd'],'extra_top_level_births':0})
    os.chdir(stage['cwd']); execve(stage['argv'][0], stage['argv'], stage['environment'])

if __name__ == '__main__':
    p=argparse.ArgumentParser();p.add_argument('--context',required=True);p.add_argument('--sha',required=True)
    p.add_argument('--index',required=True,type=int);p.add_argument('--port',required=True,type=int)
    a=p.parse_args();execute(a.context,a.sha,a.index,a.port)
