import os,json,hashlib,stat,subprocess
from pathlib import Path
B=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2'); O=B/'local-preservation/github-main-sync-20261013-r1'; S=O/'send-tree'
S.mkdir(mode=0o700)
roots=[('repository',B/'repository'),('continuation/candidates/windows-r19',B/'local-preservation/windows_appcontainer_sol_r19_20261013'),('continuation/candidates/w23-r14',B/'local-preservation/w23-saved-mph-science-readback-20261013-candidate20-r14'),('continuation/candidates/w24-coupled05',B/'local-preservation/w24-coupled-science-20261013-candidate05')]
for p in sorted((B/'local-preservation').iterdir()):
 if p.is_dir() and (p.name.startswith(('w26-','root-w26-','activity021-runtime-observation','windows-appcontainer-r19-independent','w24-coupled05-independent'))): roots.append(('continuation/packets/'+p.name,p))
allowed={'.py','.java','.ps1','.psm1','.cs','.c','.h','.sh','.bat','.cmd','.toml','.lock','.ini','.yaml','.yml','.md','.rst','.txt','.json','.jsonl','.xml','.patch','.diff','.sb','.csv'}
excluded_parts={'.git','__pycache__','.pytest_cache','.venv','venv','node_modules','comsol-server-home','actual_production_artifacts','prebirth-input-tests'}
forbidden={'.env','credentials','id_rsa','id_ed25519','license.dat','license.lic'}
files=[];local=[];issues=[]
for destroot,srcroot in roots:
 for directory,dirs,names in os.walk(srcroot,followlinks=False):
  dirs[:]=[d for d in dirs if d not in excluded_parts and not d.startswith('._')]
  for name in sorted(names):
   p=Path(directory)/name; rel=p.relative_to(srcroot); st=p.lstat()
   if not stat.S_ISREG(st.st_mode): issues.append({'source':str(p),'reason':'nonregular-not-copied'});continue
   selected=(p.suffix.lower() in allowed or name in {'Dockerfile','Makefile','MANIFEST.in','.gitignore','.gitattributes'}) and name not in forbidden and not name.startswith('._')
   if st.st_size>2*1024*1024 and p.suffix not in {'.py','.java','.ps1','.c','.h'}:selected=False
   raw=p.read_bytes(); rec={'source':str(p),'path':str(Path(destroot)/rel),'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'mode':oct(stat.S_IMODE(st.st_mode))}
   if not selected:local.append(rec);continue
   if b'-----BEGIN PRIVATE KEY-----' in raw or b'-----BEGIN OPENSSH PRIVATE KEY-----' in raw: issues.append(dict(rec,reason='private-key-marker'));continue
   q=S/rec['path'];q.parent.mkdir(parents=True,exist_ok=True);q.write_bytes(raw);q.chmod(0o755 if st.st_mode&0o111 else 0o644)
   if hashlib.sha256(q.read_bytes()).hexdigest()!=rec['sha256']:raise RuntimeError('copy mismatch')
   files.append(rec)
status=subprocess.run(['git','status','--porcelain=v1','-uall'],cwd=B/'repository',capture_output=True,check=True);(O/'ORIGINAL_GIT_STATUS.raw').write_bytes(status.stdout)
plan={'status':'PRIVATE_PREPARATION_NOT_PUSHED_NOT_INTEGRATED','roots':[(d,str(s)) for d,s in roots],'files':len(files),'bytes':sum(r['bytes'] for r in files),'kept_local_count':len(local),'kept_local_bytes':sum(r['bytes'] for r in local),'issues':issues,'base_head':'799bf7a0fa95313e7659f44d8ff40ff1678314d5','last_local_origin_main':'60464c85b429e649f4f80484473999e5365ad96a','remote':'https://github.com/Everwalker/comsol-mcp.git','constraints':['no old .git mutations','no push','no candidate overlays','Root final progress snapshot pending','runtimeMismatch UNFIXED','Activity022/W26 installer NOT_RUN','machine-bound frozen originals require explicit portable reconstruction']}
for n,v in [('SYNC_PLAN.json',plan),('SEND_MANIFEST.json',files),('LOCAL_ONLY_MANIFEST.json',local)]: (O/n).write_text(json.dumps(v,indent=2)+'\n')
print(json.dumps({k:v for k,v in plan.items() if k not in {'roots','issues'}},indent=2));print('issues',len(issues))
