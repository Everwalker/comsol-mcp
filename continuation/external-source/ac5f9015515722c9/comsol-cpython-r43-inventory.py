import os,json,hashlib,stat,struct,subprocess,time
from pathlib import Path
P=Path('/private/var/folders/4d/6pphmzkx5szf8dyqx7zhqh1r0000gn/T/comsol-intel-inputs-r1-obneuyy4');W=Path('/private/var/folders/4d/6pphmzkx5szf8dyqx7zhqh1r0000gn/T/comsol-cpython-r43-wrapper-wi0gg0yk');I=P/'cpython-universal2'
def save(name,data):
 with (W/name).open('x') as f:json.dump(data,f,indent=2,sort_keys=True);f.write('\n');f.flush();os.fsync(f.fileno())
def record(path):
 st=path.lstat();r={'path':str(path),'bytes':st.st_size,'inode':st.st_ino,'dev':st.st_dev,'uid':st.st_uid,'mode':stat.S_IMODE(st.st_mode),'nlink':st.st_nlink}
 if path.is_symlink():r.update(type='symlink',target=os.readlink(path));assert path.resolve().is_relative_to(I)
 elif path.is_file():r.update(type='regular',sha256=hashlib.sha256(path.read_bytes()).hexdigest())
 else:r['type']='directory'
 assert st.st_uid==os.getuid();return r
def macho(raw):
 magic=struct.unpack_from('>I',raw)[0]
 if magic==0xcafebabe:
  n=struct.unpack_from('>I',raw,4)[0];assert n==2;slices=[]
  for index in range(n):
   cpu,sub,offset,size,align=struct.unpack_from('>iiIII',raw,8+20*index);assert offset+size<=len(raw)
   slices.append(thin(raw[offset:offset+size]))
  return slices
 return [thin(raw)]
def thin(raw):
 magic,cpu,sub,ft,n,nb,flags,res=struct.unpack_from('<IiiIIIII',raw);assert magic==0xfeedfacf and ft==2
 off=32;versions=[];libs=[]
 for i in range(n):
  cmd,size=struct.unpack_from('<II',raw,off);assert size>=8 and off+size<=32+nb
  if cmd==0x32:
   _,_,plat,minimum,sdk,tools=struct.unpack_from('<IIIIII',raw,off);assert plat==1;versions.append(minimum)
  if cmd==0x24:versions.append(struct.unpack_from('<IIII',raw,off)[2])
  if cmd in (0xc,0x80000018,0x8000001f,0x20,0x80000023):
   pos=struct.unpack_from('<I',raw,off+8)[0];assert 24<=pos<size;libs.append(raw[off+pos:off+size].split(b'\0')[0].decode())
  off+=size
 assert off==32+nb and versions==[0xb0000]
 return {'cpu':cpu,'architecture':{0x100000c:'arm64',0x1000007:'x86_64'}[cpu],'minimum_os':[11,0,0],'linked_dylibs':libs,'executed':False}
files=[record(path) for path in sorted(I.rglob('*'))];save('INSTALL_TREE_INVENTORY.json',files)
exe=I/'bin/python3.12';slices=macho(exe.read_bytes());assert {s['architecture'] for s in slices}=={'arm64','x86_64'}
code='''import json,sys,platform,importlib,sysconfig
names=['_ssl','_hashlib','_lzma','_gdbm','_tkinter','zlib','sqlite3','ctypes','venv']
results={}
for name in names:
 try:
  m=importlib.import_module(name);results[name]={'status':'IMPORTED','file':getattr(m,'__file__',None)}
 except Exception as e:results[name]={'status':'IMPORT_FAILED','error_type':type(e).__name__,'error':str(e)}
print(json.dumps({'version':sys.version,'executable':sys.executable,'machine':platform.machine(),'platform':sysconfig.get_platform(),'modules':results,'intel_execution':False},sort_keys=True))
'''
argv=[str(exe),'-B','-c',code];save('ARM_RUNTIME_INVENTORY.request.json',{'argv':argv,'cwd':str(I),'environment_override':{'PYTHONDONTWRITEBYTECODE':'1'},'started_unix':time.time()})
with (W/'ARM_RUNTIME_INVENTORY.stdout').open('xb') as out,(W/'ARM_RUNTIME_INVENTORY.stderr').open('xb') as err:
 child=subprocess.Popen(argv,cwd=I,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'),stdout=out,stderr=err,start_new_session=True)
 save('ARM_RUNTIME_INVENTORY.spawn.json',{'pid':child.pid,'uid':os.getuid(),'parent_pid':os.getpid(),'argv':argv});rc=child.wait(timeout=30)
 out.flush();os.fsync(out.fileno());err.flush();os.fsync(err.fileno())
save('ARM_RUNTIME_INVENTORY.terminal.json',{'pid':child.pid,'rc':rc,'wait_complete':True,'logs_flushed':True})
assert rc==0
runtime=json.loads((W/'ARM_RUNTIME_INVENTORY.stdout').read_text());assert runtime['machine']=='arm64'
save('ARTIFACT_SUMMARY.json',{'binary':record(exe),'macho_slices':slices,'runtime':runtime,'tree_records':len(files),'regular_files':sum(x['type']=='regular' for x in files),'regular_bytes':sum(x['bytes'] for x in files if x['type']=='regular'),'pure_x86_runtime':'NOT_RUN','scientific_acceptance':'NOT_RUN','final_delivery_acceptance':'NOT_RUN'})
print(json.dumps({'wrapper':str(W),'inventory_records':len(files),'runtime_rc':rc,'slices':slices}))
