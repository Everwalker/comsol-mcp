"""Offline exact-input materialization; no archive/source programs are executed."""
from __future__ import annotations
import base64
import csv
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import stat
import zipfile
from acquire_exact_archives import Refusal

SOURCE_INVENTORY_SHA='165b68c4dc1b131fa0896c816d98211dc10768787d8317524bbcf386166451b4'

def parts(name):
    p=PurePosixPath(name)
    if not name or '\\' in name or p.is_absolute() or any(v in ('','.', '..') for v in name.split('/')):
        raise Refusal('noncanonical owned relative path')
    return p.parts

def open_relative(rootfd, name, flags):
    names=parts(name);fd=os.dup(rootfd)
    try:
        for component in names[:-1]:
            child=os.open(component,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            os.close(fd);fd=child
        return os.open(names[-1],flags|os.O_NOFOLLOW,dir_fd=fd)
    finally:os.close(fd)

def exclusive_json(path, payload):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        raw=(json.dumps(payload,sort_keys=True,indent=2)+'\n').encode();view=memoryview(raw)
        while view: view=view[os.write(fd,view):]
        os.fsync(fd)
    finally:os.close(fd)

def copy_exact_tree(source, rows, destination, root_record=None):
    """Validate the complete subtree membership plus original inode/bytes before copying."""
    source=Path(source);destination=Path(destination)
    expected={r['relative_path']:r for r in rows}
    if len(expected)!=len(rows):raise Refusal('duplicate inventory rows')
    children={}
    for name in expected:
        names=parts(name);parent='/'.join(names[:-1]);children.setdefault(parent,set()).add(names[-1])
    rootfd=os.open(source,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        if root_record is not None:
            st=os.fstat(rootfd)
            if (st.st_uid!=os.getuid() or st.st_uid!=root_record["uid"] or st.st_ino!=root_record["inode"] or st.st_dev!=root_record["device"] or stat.S_IMODE(st.st_mode)!=int(root_record["mode"],8)):
                raise Refusal("original subtree root identity changed")
        def verify_listing(name,fd):
            if set(os.listdir(fd))!=children.get(name,set()):raise Refusal('source subtree contains unreviewed/missing entry')
        verify_listing('',rootfd)
        # Validate every original inode and regular byte image before creating the destination.
        for name,row in sorted(expected.items()):
            fd=open_relative(rootfd,name,os.O_RDONLY|(os.O_DIRECTORY if row['kind']=='directory' else 0))
            try:
                st=os.fstat(fd)
                if (st.st_uid!=os.getuid() or st.st_uid!=row['uid'] or st.st_dev!=row['device'] or st.st_ino!=row['inode']
                    or stat.S_IMODE(st.st_mode)!=int(row['mode'],8)):raise Refusal('original source identity changed')
                if row['kind']=='directory':
                    if not stat.S_ISDIR(st.st_mode):raise Refusal('source directory mismatch')
                    verify_listing(name,fd)
                elif row['kind']=='regular':
                    if not stat.S_ISREG(st.st_mode) or st.st_nlink!=1 or st.st_size!=row['bytes']:raise Refusal('source regular size/link mismatch')
                    h=hashlib.sha256()
                    while raw:=os.read(fd,1048576):h.update(raw)
                    if h.hexdigest()!=row['sha256']:raise Refusal('source bytes differ from exact unpack inventory')
                else:raise Refusal('only regular files/directories admitted')
            finally:os.close(fd)
        destination.mkdir(mode=0o700)
        for name,row in sorted(expected.items(),key=lambda item:(len(parts(item[0])),item[0])):
            target=destination/name
            if row['kind']=='directory':target.mkdir(mode=0o700);continue
            src=open_relative(rootfd,name,os.O_RDONLY);dst=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,int(row['mode'],8))
            h=hashlib.sha256();count=0
            try:
                st=os.fstat(src)
                if st.st_ino!=row['inode'] or st.st_dev!=row['device'] or st.st_uid!=row['uid'] or st.st_nlink!=1:raise Refusal('source changed before copy')
                while raw:=os.read(src,1048576):
                    h.update(raw);count+=len(raw);view=memoryview(raw)
                    while view:view=view[os.write(dst,view):]
                if count!=row['bytes'] or h.hexdigest()!=row['sha256']:raise Refusal('source changed during copy')
                os.fsync(dst)
            finally:os.close(src);os.close(dst)
        return {'entries':len(rows),'regular_bytes':sum(r.get('bytes',0) for r in rows)}
    finally:os.close(rootfd)

def install_wheel_exact(archive, row, site, scripts):
    """Fixed wheel bytes/RECORD only; no installer hooks, executable launch, or network."""
    fd=os.open(archive,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        st=os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.getuid() or st.st_nlink!=1 or st.st_size!=row['expected_bytes']:raise Refusal('wheel owner/size/link mismatch')
        with os.fdopen(os.dup(fd),'rb') as stream:raw=stream.read()
    finally:os.close(fd)
    if hashlib.sha256(raw).hexdigest()!=row['expected_sha256']:raise Refusal('wheel digest mismatch')
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        infos=z.infolist();files={};expanded=0
        for entry in infos:
            if entry.is_dir():parts(entry.filename.rstrip('/'));continue
            parts(entry.filename)
            mode=(entry.external_attr>>16)&0xffff
            if stat.S_IFMT(mode) not in (0,stat.S_IFREG):raise Refusal('wheel special entry rejected')
            if entry.filename in files:raise Refusal('duplicate wheel member')
            expanded+=entry.file_size
            if entry.file_size>67108864 or expanded>134217728:raise Refusal('wheel expanded bound exceeded')
            files[entry.filename]=z.read(entry)
        records=[name for name in files if name.endswith('.dist-info/RECORD')]
        if len(records)!=1:raise Refusal('one wheel RECORD required')
        seen=set()
        for name,digest,size in csv.reader(io.StringIO(files[records[0]].decode())):
            if name in seen or name not in files:raise Refusal('RECORD duplicates/missing byte member')
            seen.add(name)
            if name==records[0]:
                if digest or size:raise Refusal('RECORD self row must be empty')
            else:
                actual='sha256='+base64.urlsafe_b64encode(hashlib.sha256(files[name]).digest()).rstrip(b'=').decode()
                if digest!=actual or size!=str(len(files[name])):raise Refusal('wheel RECORD digest/size mismatch')
        if seen!=set(files):raise Refusal('wheel contains bytes absent from RECORD')
        targets=[]
        for name,data in files.items():
            if '.data/' in name:
                if row['artifact_id']!='python-build-wheel-maturin-1.14.1' or name!='maturin-1.14.1.data/scripts/maturin':raise Refusal('unreviewed wheel data mapping')
                base,relative,mode=scripts,'maturin',0o700
            else:base,relative,mode=site,name,0o600
            targets.append((base,relative,data,mode))
        if len({str(base/relative) for base,relative,_,_ in targets})!=len(targets):raise Refusal('mapped wheel path collision')
        for base,relative,data,mode in targets:
            names=parts(relative);parentfd=os.open(base,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            try:
                for component in names[:-1]:
                    try:os.mkdir(component,0o700,dir_fd=parentfd)
                    except FileExistsError:pass
                    child=os.open(component,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parentfd)
                    os.close(parentfd);parentfd=child
                    st=os.fstat(parentfd)
                    if st.st_uid!=os.getuid() or stat.S_IMODE(st.st_mode)&0o077:raise Refusal('wheel destination parent is not private')
                out=os.open(names[-1],os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,mode,dir_fd=parentfd)
                try:
                    view=memoryview(data)
                    while view:view=view[os.write(out,view):]
                    os.fsync(out)
                finally:os.close(out)
                os.fsync(parentfd)
            finally:os.close(parentfd)
        return {'artifact_id':row['artifact_id'],'members':len(files),'expanded_bytes':expanded}

def vendor_checksums(directory, package_sha):
    files={}
    for path in sorted(directory.rglob('*')):
        if path.is_symlink():raise Refusal('vendor symlink refused')
        if path.is_file():files[path.relative_to(directory).as_posix()]=hashlib.sha256(path.read_bytes()).hexdigest()
    if '.cargo-checksum.json' in files:raise Refusal('unexpected preexisting vendor checksum')
    exclusive_json(directory/'.cargo-checksum.json',{'files':files,'package':package_sha})
    return len(files)
