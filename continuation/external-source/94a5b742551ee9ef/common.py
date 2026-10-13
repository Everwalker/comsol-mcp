"""Small dependency unit helpers; wheel input code is never imported during preparation."""
import hashlib,json,os,stat,time
from pathlib import Path
class Refusal(RuntimeError):pass
def raw_file(path,maximum=1073741824,expected=None):
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
 try:
  a=os.fstat(fd)
  if not stat.S_ISREG(a.st_mode) or a.st_uid!=os.getuid() or a.st_nlink!=1 or a.st_size>maximum or a.st_mode&0o022:raise Refusal('untrusted regular input')
  parts=[];h=hashlib.sha256();n=0
  while b:=os.read(fd,1048576):
   n+=len(b)
   if n>maximum:raise Refusal('bounded input exceeded')
   h.update(b);parts.append(b)
  z=os.fstat(fd);named=os.stat(path,follow_symlinks=False)
  key=lambda q:(q.st_dev,q.st_ino,q.st_mode,q.st_uid,q.st_nlink,q.st_size,q.st_mtime_ns,q.st_ctime_ns)
  if key(a)!=key(z) or key(a)!=key(named) or n!=a.st_size:raise Refusal('first regular identity drift')
  record={'path':str(path),'bytes':n,'sha256':h.hexdigest(),'dev':a.st_dev,'inode':a.st_ino,'uid':a.st_uid,'mode':stat.S_IMODE(a.st_mode),'nlink':a.st_nlink}
  if expected is not None and record!=expected:raise Refusal('fixed regular checkpoint changed')
  return b''.join(parts),record
 finally:os.close(fd)
def publish(path,obj):
 fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 try:
  raw=(json.dumps(obj,indent=2,sort_keys=True)+'\n').encode();v=memoryview(raw)
  while v:v=v[os.write(fd,v):]
  os.fsync(fd)
 finally:os.close(fd)
 return raw_file(path)[1]
def secure_dir(path,expected=None):
 a=path.lstat()
 if not stat.S_ISDIR(a.st_mode) or a.st_uid!=os.getuid() or a.st_mode&0o077:raise Refusal('root not owner-private real directory')
 row={'device':a.st_dev,'inode':a.st_ino}
 if expected is not None and row!=expected:raise Refusal('directory identity changed')
 return row
def copy_blob(path,raw):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        view=memoryview(raw)
        while view:view=view[os.write(fd,view):]
        os.fsync(fd)
    finally:os.close(fd)

def exact_copy(path,destination,expected):
 raw,_=raw_file(path,expected=expected)
 copy_blob(destination,raw)
 data,row=raw_file(destination)
 if data!=raw or row["sha256"]!=expected["sha256"] or row["bytes"]!=expected["bytes"]:raise Refusal("copy differs from original accepted bytes")
 return row

def system_binary(path,expected=None):
 if str(path)!='/usr/bin/sandbox-exec':raise Refusal('unapproved system native launcher')
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
 try:
  a=os.fstat(fd)
  if not stat.S_ISREG(a.st_mode) or a.st_uid!=0 or a.st_nlink!=1 or a.st_mode&0o022 or a.st_size>33554432:raise Refusal('system launcher ownership/mode/bound changed')
  h=hashlib.sha256();n=0
  while b:=os.read(fd,1048576):h.update(b);n+=len(b)
  z=os.fstat(fd);named=path.lstat()
  if (a.st_dev,a.st_ino,a.st_mode,a.st_size,a.st_mtime_ns)!=(z.st_dev,z.st_ino,z.st_mode,z.st_size,z.st_mtime_ns) or (a.st_dev,a.st_ino)!=(named.st_dev,named.st_ino):raise Refusal('system launcher identity changed while reading')
  row={'path':str(path),'bytes':n,'sha256':h.hexdigest(),'dev':a.st_dev,'inode':a.st_ino,'uid':a.st_uid,'mode':stat.S_IMODE(a.st_mode),'nlink':a.st_nlink}
  if expected is not None and row!=expected:raise Refusal('fixed system launcher changed')
  return row
 finally:os.close(fd)
