#!/usr/bin/env python3
"""Restore pinned public source to a NEW directory. No pip, engine or repo code runs.
Python 3.10+; Git preferred, HTTPS ZIP fallback. Full Git tree hash is verified.
Historical symlinks are written as inert files and recorded, never followed.
"""
from __future__ import annotations
import argparse,hashlib,json,os,re,shutil,stat,subprocess,tempfile,unicodedata,urllib.request,zipfile
from pathlib import Path,PurePosixPath
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1]
MAX_FILES=60000
MAX_TOTAL=4*1024**3
MAX_FILE=1024**3
MAX_DOWNLOAD=2*1024**3

def valid_path(name: str)->tuple[str,...]:
    if not name or '\\' in name or any(ord(c)<32 for c in name):raise ValueError('invalid path')
    parts=name.split('/')
    if any(p in ('','.','..') for p in parts):raise ValueError('path traversal/alias')
    for p in parts:
        if p.casefold()=='.git' or any(c in p for c in '<>:"|?*') or p.endswith((' ','.')):raise ValueError('unsafe Windows path')
        if re.fullmatch(r'CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³]',p.split('.')[0],re.I):raise ValueError('reserved device path')
    return tuple(parts)

def validate_paths(entries):
    seen={}
    for row in entries:
        parts=valid_path(row['path'])
        for i in range(1,len(parts)+1):
            raw='/'.join(parts[:i]);key=unicodedata.normalize('NFC',raw).casefold();leaf=i==len(parts)
            if key in seen:
                prior,was_leaf=seen[key]
                if prior!=raw or was_leaf or leaf:raise ValueError('colliding files/directories: '+raw)
            else:seen[key]=(raw,leaf)

def tree_digest(entries):
    tree={}
    for row in entries:
        parts=valid_path(row['path']);cursor=tree
        for part in parts[:-1]:
            cursor=cursor.setdefault(part,{})
            if not isinstance(cursor,dict):raise ValueError('file/directory collision')
        if parts[-1] in cursor:raise ValueError('duplicate')
        cursor[parts[-1]]=(row['mode'],row['blob'])
    def build(node):
        rows=[]
        for name,item in node.items():
            encoded=name.encode('utf-8')
            if isinstance(item,dict):mode,sha,sort='40000',build(item),encoded+b'/'
            else:mode,sha=item;sort=encoded
            rows.append((sort,mode.encode()+b' '+encoded+b'\0'+bytes.fromhex(sha)))
        payload=b''.join(p for _,p in sorted(rows))
        return hashlib.sha1(b'tree '+str(len(payload)).encode()+b'\0'+payload).hexdigest()
    return build(tree)

def reparse(p):
    try:s=p.lstat()
    except FileNotFoundError:return False
    return stat.S_ISLNK(s.st_mode) or bool(getattr(s,'st_file_attributes',0)&0x400)

def safe_ancestors(p):
    p=p.absolute()
    while True:
        if reparse(p):raise ValueError('reparse ancestor: '+str(p))
        if p.parent==p:break
        p=p.parent

def validate_pin(pin):
    if pin.get('remote')!='https://github.com/Everwalker/comsol-mcp.git':raise ValueError('unexpected source repository')
    for f in ('commit','tree'):
        if not re.fullmatch('[0-9a-f]{40}',str(pin.get(f,''))):raise ValueError('invalid '+f)
    if pin['archive_url']!='https://codeload.github.com/Everwalker/comsol-mcp/zip/'+pin['commit']:raise ValueError('unfixed archive')
    valid_path(pin['handoff_directory'])

def git(argv,cwd,**kwargs):
    env=os.environ.copy();env.update(GIT_TERMINAL_PROMPT='0',GIT_LFS_SKIP_SMUDGE='1')
    cmd=['git','-c','core.autocrlf=false','-c','core.symlinks=false','-c','core.hooksPath='+str(ROOT/'_state/no-hooks'),'-c','protocol.file.allow=never',*argv]
    return subprocess.run(cmd,cwd=cwd,env=env,check=True,timeout=600,**kwargs)

def copy_blob(stream,size,target):
    if not 0<=size<=MAX_FILE:raise ValueError('individual file exceeds budget')
    target.parent.mkdir(parents=True,exist_ok=True)
    blob=hashlib.sha1(b'blob '+str(size).encode()+b'\0');sha=hashlib.sha256();left=size;head=b''
    with target.open('xb') as dst:
        while left:
            chunk=stream.read(min(left,1024*1024))
            if not chunk:raise ValueError('truncated content')
            if len(head)<200:head+=chunk[:200-len(head)]
            dst.write(chunk);blob.update(chunk);sha.update(chunk);left-=len(chunk)
    return {'blob':blob.hexdigest(),'sha256':sha.hexdigest(),'size':size,'lfs_pointer':head.startswith(b'version https://git-lfs.github.com/spec/v1')}

def restore_git(stage,pin):
    git(['init','--template='],stage,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    for k,v in [('core.symlinks','false'),('core.autocrlf','false'),('core.longpaths','true'),('core.hooksPath',str(ROOT/'_state/no-hooks'))]:git(['config',k,v],stage,stdout=subprocess.DEVNULL)
    git(['remote','add','origin',pin['remote']],stage)
    git(['fetch','--depth=1','--no-tags','origin',pin['commit']],stage,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    actual=git(['rev-parse',pin['commit']+'^{tree}'],stage,stdout=subprocess.PIPE).stdout.decode().strip()
    if actual!=pin['tree']:raise ValueError('commit/tree mismatch')
    raw=git(['ls-tree','-rz',pin['commit']],stage,stdout=subprocess.PIPE).stdout
    rows=[]
    for record in raw.split(b'\0'):
        if not record:continue
        meta,name=record.split(b'\t',1);mode,kind,sha=meta.decode().split()
        if kind!='blob' or mode not in ('100644','100755','120000'):raise ValueError('unsupported object/submodule: '+name.decode())
        rows.append({'path':name.decode('utf-8'),'mode':mode,'blob':sha})
    if len(rows)>MAX_FILES:raise ValueError('file count budget')
    validate_paths(rows)
    if tree_digest(rows)!=pin['tree']:raise ValueError('tree reconstruction mismatch')
    inp=stage.parent/(stage.name+'.batch-input')
    inp.write_text(''.join(r['blob']+'\n' for r in rows),encoding='ascii')
    total=0
    with inp.open('rb') as batch, tempfile.TemporaryFile() as errors:
        process=subprocess.Popen(['git','cat-file','--batch'],cwd=stage,stdin=batch,stdout=subprocess.PIPE,stderr=errors)
        try:
            for row in rows:
                header=process.stdout.readline(300).decode().strip().split()
                if len(header)!=3 or header[0]!=row['blob'] or header[1]!='blob':raise ValueError('wrong batch object')
                size=int(header[2]);total+=size
                if total>MAX_TOTAL:raise ValueError('total bytes budget')
                path=stage.joinpath(*valid_path(row['path']))
                measured=copy_blob(process.stdout,size,path)
                if measured['blob']!=row['blob'] or process.stdout.read(1)!=b'\n':raise ValueError('blob mismatch')
                row.update(measured,inert_symlink=row['mode']=='120000')
                if os.name!='nt' and row['mode']=='100755':path.chmod(0o755)
            if process.wait(timeout=60)!=0:raise ValueError('cat-file failed')
        except Exception:
            process.kill();process.wait();raise # only this helper-owned process
        finally:
            if process.stdout is not None: process.stdout.close()
    inp.unlink()
    git(['update-ref','refs/heads/'+pin['working_branch'],pin['commit']],stage)
    git(['symbolic-ref','HEAD','refs/heads/'+pin['working_branch']],stage)
    git(['read-tree',pin['commit']],stage)
    return rows

def extract_archive(archive,stage,expected_tree):
    rows=[];total=0
    with zipfile.ZipFile(archive) as z:
        infos=z.infolist()
        if len(infos)>MAX_FILES*2:raise ValueError('archive entry budget')
        roots=set();preflight=[]
        for info in infos:
            if info.is_dir():continue
            parts=valid_path(info.filename);roots.add(parts[0])
            if len(parts)<2:raise ValueError('no archive root')
            rel='/'.join(parts[1:]);mode=(info.external_attr>>16)&0xffff;kind=stat.S_IFMT(mode)
            if kind not in (0,stat.S_IFREG,stat.S_IFLNK):raise ValueError('special file')
            gitmode='120000' if kind==stat.S_IFLNK else '100755' if mode&0o111 else '100644'
            total+=info.file_size
            if info.file_size>MAX_FILE or total>MAX_TOTAL:raise ValueError('archive size budget')
            preflight.append((info,{'path':rel,'mode':gitmode}))
        if len(roots)!=1 or not preflight:raise ValueError('invalid root or empty archive')
        validate_paths([row for _,row in preflight])
        for info,row in preflight:
            target=stage.joinpath(*valid_path(row['path']))
            with z.open(info) as src:row.update(copy_blob(src,info.file_size,target))
            row['inert_symlink']=row['mode']=='120000';rows.append(row)
            if os.name!='nt' and row['mode']=='100755':target.chmod(0o755)
    if tree_digest(rows)!=expected_tree:raise ValueError('archive is not complete pinned Git tree; use Git route, do not ignore')
    return rows

def download(url,path):
    request=urllib.request.Request(url,headers={'User-Agent':'comsol-workpack-pinned-recovery/1'})
    with urllib.request.urlopen(request,timeout=90) as src,path.open('xb') as dst:
        if not src.geturl().startswith('https://codeload.github.com/'):raise ValueError('unexpected redirect')
        total=0
        while chunk:=src.read(1024*1024):
            total+=len(chunk)
            if total>MAX_DOWNLOAD:raise ValueError('download budget')
            dst.write(chunk)

def main():
    global MAX_TOTAL,MAX_FILE,MAX_DOWNLOAD
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--target',type=Path,default=ROOT/'repository')
    ap.add_argument('--method',choices=('auto','git','archive'),default='auto')
    ap.add_argument('--max-total-gib',type=int,default=4)
    ap.add_argument('--max-file-gib',type=int,default=1)
    ap.add_argument('--max-download-gib',type=int,default=2)
    args=ap.parse_args()
    if not all(1<=n<=64 for n in (args.max_total_gib,args.max_file_gib,args.max_download_gib)):
        raise ValueError('Explicit file budgets must be between 1 and 64 GiB')
    MAX_TOTAL=args.max_total_gib*1024**3;MAX_FILE=args.max_file_gib*1024**3;MAX_DOWNLOAD=args.max_download_gib*1024**3
    pin=json.loads((ROOT/'PIN.json').read_text());validate_pin(pin)
    target=args.target.absolute();safe_ancestors(target)
    if target.exists():raise ValueError('Target exists. Choose a NEW directory; no overwrite or clean.')
    target.parent.mkdir(parents=True,exist_ok=True);stage=Path(tempfile.mkdtemp(prefix='.mcp-restore-',dir=target.parent))
    method=('git' if shutil.which('git') else 'archive') if args.method=='auto' else args.method
    try:
        if method=='git':rows=restore_git(stage,pin)
        else:
            archive=stage.parent/(stage.name+'.zip');download(pin['archive_url'],archive);rows=extract_archive(archive,stage,pin['tree'])
        overlay=stage.joinpath(*valid_path(pin['handoff_directory']))
        if overlay.exists():raise ValueError('new overlay unexpectedly exists in source')
        overlay.mkdir(parents=True)
        for name in ('MASTER_GOAL.md','START_HERE.md','REVIEW.md','PIN.json','SOURCE_MAP.json',
                     'RECOVERY.md','DEFERRED_BACKLOG.md','PROGRESS.md','TEAM_PROTOCOL.md',
                     'BLOCKER_POLICY.md','FULL_PLAN.md','CONTINUATION.md','RELEASE_DOD.md',
                     'ORIGINAL_REQUIREMENTS.json','INHERITED_CONTRACT_LOCK.json','GOAL.txt',
                     'CURRENT_BASELINE.json','SCOPE_DECISIONS_SNAPSHOT.json','CLEANUP_GUARD.md',
                     'INTEGRATION_PLAN.md','AGENTS.md'):
            shutil.copyfile(ROOT/name,overlay/name)
        for name in ('roles','inherited_contracts','tools','config'):
            shutil.copytree(ROOT/name,overlay/name,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        # The existing original task/action/test state is deliberately never initialized or replaced.
        receipt={'status':'PINNED_SOURCE_RESTORED','commit':pin['commit'],'tree':pin['tree'],'method':method,'file_count':len(rows),'files':rows,'created_utc':datetime.now(timezone.utc).isoformat(),'git_history_ready':method=='git','scope':'tracked Git content only; symlinks inert; LFS pointers do not include backing objects','missing_lfs_backing_objects':[r['path'] for r in rows if r.get('lfs_pointer')],'python_installed':False,'comsol_started':False}
        (overlay/'RESTORE_RECEIPT.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        safe_ancestors(target)
        if target.exists() or reparse(target):raise ValueError('Target appeared during recovery; refusing to overwrite')
        stage.rename(target)
        print(json.dumps({'status':receipt['status'],'target':str(target),'files':len(rows),'method':method,'next':'Run tools/init_resume.py --repo repository to read and index existing state without replacing it; then MASTER_GOAL.md to full completion.'},ensure_ascii=False,indent=2))
    except Exception as exc:
        failure={'status':'RECOVERY_FAILED','staging':str(stage),'type':type(exc).__name__,'message':str(exc)}
        state=ROOT/'_state';state.mkdir(exist_ok=True);(state/'recovery_failure.json').write_text(json.dumps(failure,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(failure,ensure_ascii=False));return 2
    return 0
if __name__=='__main__':raise SystemExit(main())
