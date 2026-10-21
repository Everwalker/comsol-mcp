#!/usr/bin/env python3
"""P0 task-owned observer around ordinary production serve, no injected backend."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import threading

sys.path.insert(0,str(Path(__file__).resolve().parent))
from p0_original_controller_hold import P0OriginalControllerOwner, canonical, pairs, file_signature, private_fd


def write(path, value):
    path.write_text(json.dumps(value,sort_keys=True,indent=2,allow_nan=False)+"\n")


def load_config(path):
    fd,root_inode=private_fd(path.parent)
    try:
        before=os.stat(path.name,dir_fd=fd,follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode) or before.st_uid!=os.getuid() or stat.S_IMODE(before.st_mode)!=0o600 or before.st_nlink!=1 or not 0<before.st_size<=32768:
            raise ValueError("private ordinary source-bound P0 config required")
        with os.fdopen(os.open(path.name,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=fd),"rb") as stream:
            opened=os.fstat(stream.fileno());raw=stream.read(32769);after=os.fstat(stream.fileno())
        current=os.stat(path.name,dir_fd=fd,follow_symlinks=False)
        root_now=path.parent.lstat()
        if ((root_now.st_dev,root_now.st_ino)!=root_inode or not stat.S_ISDIR(root_now.st_mode)
                or root_now.st_uid!=os.getuid() or stat.S_IMODE(root_now.st_mode)!=0o700
                or any(file_signature(st)!=file_signature(before) for st in (opened,after,current))
                or not 0<len(raw)<=32768 or len(raw)!=before.st_size):
            raise ValueError("config/root changed during complete pinned read")
    finally:os.close(fd)
    config=json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _:(_ for _ in ()).throw(ValueError("nonfinite")))
    if set(config)!={"binding","source","freeze","home"}:raise ValueError("closed config required")
    binding=config["binding"]
    if not isinstance(binding,dict) or set(binding)!={"activity_id","namespace","source_sha256","harness_sha256","entry_sha256","holder_sha256","owner_nonce"}:raise ValueError("closed owner binding required")
    if any(not isinstance(v,str) or not v for v in binding.values()):raise ValueError("string owner binding required")
    from uuid import UUID
    if str(UUID(binding["owner_nonce"]))!=binding["owner_nonce"]:raise ValueError("canonical nonce required")
    root=Path(binding["namespace"])
    if path!=root/"P0_OWNER_CONFIG.json" or binding["activity_id"]!=root.name or config["home"]!=str(root/"home/control-private"):raise ValueError("fresh config path differs")
    tool=Path(__file__).resolve().parent
    for name,field in (("run_v3_public_mcp_smoke.py","harness_sha256"),("p0_owned_control_entry.py","entry_sha256"),("p0_original_controller_hold.py","holder_sha256")):
        if hashlib.sha256((tool/name).read_bytes()).hexdigest()!=binding[field]:raise ValueError("task source differs")
    source=Path(config["source"]).resolve(strict=True);freeze=json.loads(Path(config["freeze"]).read_text())
    files={p.relative_to(source).as_posix() for p in (source/"comsol_mcp").rglob("*") if p.is_file() and "__pycache__" not in p.parts}
    if files!=set(freeze["files"]):raise ValueError("production source files differ")
    for relative,sha in freeze["files"].items():
        target=source/relative
        if target.is_symlink() or hashlib.sha256(target.read_bytes()).hexdigest()!=sha:raise ValueError("production source differs")
    sha=hashlib.sha256(canonical(freeze["files"]).encode()).hexdigest()
    if sha!=binding["source_sha256"]:raise ValueError("production identity differs")
    return config


def main(argv=None):
    parser=argparse.ArgumentParser();parser.add_argument("--config",type=Path,required=True);args=parser.parse_args(argv)
    config=load_config(args.config)
    owner=P0OriginalControllerOwner(config["binding"]["namespace"],config["binding"],write)
    done=threading.Event()
    def listen():
        try:
            from comsol_mcp import _control_daemon
            if Path(_control_daemon.__file__).resolve() != Path(config["source"])/"comsol_mcp/_control_daemon.py":
                raise RuntimeError("actual production controller import differs from pinned source")
            _control_daemon.serve(config["home"],_owner_observer=lambda daemon:owner.bind(daemon,guard_birth=True))
        except BaseException as exc:owner.cause(exc);owner.diagnostic("production_listener",exc)
        finally:done.set()
    thread=threading.Thread(target=listen,name="p0-production-control-listener",daemon=True);thread.start()
    owner.watch(done)
    return 1  # All hold completions remain failed/UNKNOWN evidence.

if __name__=="__main__":raise SystemExit(main())
