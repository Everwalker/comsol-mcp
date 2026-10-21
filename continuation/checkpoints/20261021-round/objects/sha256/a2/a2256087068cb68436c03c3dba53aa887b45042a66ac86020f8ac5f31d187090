"""Hold one sandboxed original PID until its parent observes actual birth.

Then exec the exact byte-bound compiler stage in the same PID/session. No
source acquisition, process spawning, signals, GUI or engine operations.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import time

def read(path, expected_sha=None):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        first=os.fstat(fd)
        if not stat.S_ISREG(first.st_mode) or first.st_uid!=os.getuid() or first.st_nlink!=1 or first.st_mode&0o022 or first.st_size>1048576:
            raise RuntimeError('stage input ordinary owner/hash envelope refused')
        raw=os.read(fd,first.st_size+1);last=os.fstat(fd);named=Path(path).lstat()
        key=lambda x:(x.st_dev,x.st_ino,x.st_uid,x.st_mode,x.st_size,x.st_mtime_ns,x.st_ctime_ns)
        if key(first)!=key(last) or key(first)!=key(named) or len(raw)!=first.st_size or (expected_sha and hashlib.sha256(raw).hexdigest()!=expected_sha):
            raise RuntimeError('stage input first-record/hash drift')
        return json.loads(raw)
    finally:os.close(fd)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--stage',required=True);parser.add_argument('--sha',required=True);parser.add_argument('--go',required=True)
    args=parser.parse_args();stage=read(args.stage,args.sha)
    go=Path(args.go);deadline=time.monotonic()+30
    while not go.exists():
        if time.monotonic()>deadline:raise TimeoutError('parent did not establish actual original PID/birth before exec')
        time.sleep(0.01)
    permission=read(go)
    if permission.get('pid')!=os.getpid() or permission.get('stage_sha')!=args.sha or permission.get('nonce')!=stage['nonce'] or os.getppid()!=stage['parent_pid']:
        raise RuntimeError('same original parent/PID/stage/nonce proof differs')
    os.chdir(stage['cwd'])
    os.execve(stage['argv'][0],stage['argv'],stage['environment'])

if __name__=='__main__':main()
