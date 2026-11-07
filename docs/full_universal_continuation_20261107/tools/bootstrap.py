#!/usr/bin/env python3
"""Restore pinned public source to a NEW directory. No pip, engine or repo code runs.
Python 3.10+; Git preferred, HTTPS ZIP fallback. Full Git tree map is verified.
Git active scope defers historical blob bytes; full scope restores all tracked bytes.
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
    for path in pin.get('active_paths', []): valid_path(path)
    for path in pin.get('active_root_excluded', []):
        if len(valid_path(path)) != 1: raise ValueError('root exclusion must be a root file')

class RestoredRows(list):
    def __init__(self, restored, tree_entries, scope):
        super().__init__(restored)
        self.tree_entries = tree_entries
        self.restore_scope = scope

def selected_path(path, pin):
    if '/' not in path and pin.get('active_root_files', True):
        return path not in pin.get('active_root_excluded', [])
    return any(path == prefix or path.startswith(prefix+'/')
               for prefix in pin.get('active_paths', []))

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

def restore_git(stage,pin,scope='full'):
    git(['init','--template='],stage,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    for k,v in [('core.symlinks','false'),('core.autocrlf','false'),('core.longpaths','true'),('core.hooksPath',str(ROOT/'_state/no-hooks'))]:git(['config',k,v],stage,stdout=subprocess.DEVNULL)
    git(['remote','add','origin',pin['remote']],stage)
    if scope not in ('active','full'): raise ValueError('unknown restore scope')
    if scope == 'active':
        git(['config','remote.origin.promisor','true'],stage)
        git(['config','remote.origin.partialclonefilter','blob:none'],stage)
    fetch=['fetch','--depth=1','--no-tags']
    if scope == 'active': fetch.append('--filter=blob:none')
    git([*fetch,'origin',pin['commit']],stage,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
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
    all_rows = rows
    rows = [r for r in all_rows if scope == 'full' or selected_path(r['path'], pin)]
    if not rows: raise ValueError('active scope selected no files')
    inp=stage.parent/(stage.name+'.batch-input')
    inp.write_text(''.join(r['blob']+'\n' for r in rows),encoding='ascii')
    total=0
    with inp.open('rb') as batch, tempfile.TemporaryFile() as errors:
        env=os.environ.copy();env.update(GIT_TERMINAL_PROMPT='0',GIT_LFS_SKIP_SMUDGE='1')
        process=subprocess.Popen(['git','-c','protocol.file.allow=never','cat-file','--batch'],cwd=stage,env=env,stdin=batch,stdout=subprocess.PIPE,stderr=errors)
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
    if scope == 'active':
        omitted=[r['path'] for r in all_rows if not selected_path(r['path'], pin)]
        if omitted:
            git(['update-index','--skip-worktree','-z','--stdin'],stage,
                input=('\0'.join(omitted)+'\0').encode('utf-8'),stdout=subprocess.DEVNULL)
    return RestoredRows(rows, all_rows, scope)

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

def package_manifest(root):
    from verify_package import verify
    checked = verify(root)
    if checked['errors']:
        raise ValueError('workpack integrity failed: '+json.dumps(checked['errors'], ensure_ascii=False))
    return json.loads((root/'PACKAGE_MANIFEST.json').read_text(encoding='utf-8'))

def write_overlay(stage, pin, manifest, rows, method):
    overlay = stage.joinpath(*valid_path(pin['handoff_directory']))
    if overlay.exists():
        raise ValueError('handoff directory already exists in pinned source')
    overlay.mkdir(parents=True)
    for row in manifest['files']:
        parts = valid_path(row['path'])
        target = overlay.joinpath(*parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT.joinpath(*parts), target)
    shutil.copyfile(ROOT/'PACKAGE_MANIFEST.json', overlay/'PACKAGE_MANIFEST.json')
    all_rows = getattr(rows, 'tree_entries', rows)
    restore_scope = getattr(rows, 'restore_scope', 'full')
    restored_paths = {r['path'] for r in rows}
    tree_map = {'commit': pin['commit'], 'tree': pin['tree'],
                'entries': [{**r, 'materialized': r['path'] in restored_paths} for r in all_rows]}
    (overlay/'RESTORE_SOURCE_MAP.json').write_text(
        json.dumps(tree_map,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    receipt = {
        'status': 'PINNED_ACTIVE_SOURCE_RESTORED' if restore_scope == 'active' else 'PINNED_SOURCE_RESTORED',
        'commit': pin['commit'], 'tree': pin['tree'], 'method': method,
        'candidate_status': pin['candidate_status'],
        'restore_scope': restore_scope,
        'tree_entries_verified': len(all_rows),
        'deferred_blob_count': len(all_rows)-len(rows),
        'all_tracked_blob_bytes_materialized': len(rows)==len(all_rows),
        'file_count': len(rows), 'source_bytes': sum(r['size'] for r in rows),
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'git_history_ready': method == 'git',
        'inert_symlinks': [r['path'] for r in rows if r.get('inert_symlink')],
        'missing_lfs_backing_objects': [r['path'] for r in rows if r.get('lfs_pointer')],
        'scope': 'only materialized tracked bytes are verified locally; full tree map is verified; continuation packet is an added overlay',
        'dependencies_installed': False, 'comsol_started': False,
        'runtime_authority_restored': False, 'product_acceptance': 'NOT_ESTABLISHED'
    }
    (overlay/'RESTORE_RECEIPT.json').write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return receipt

def main(argv=None):
    global MAX_TOTAL, MAX_FILE, MAX_DOWNLOAD
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', type=Path, default=ROOT/'repository')
    parser.add_argument('--method', choices=('auto','git','archive'), default='auto')
    parser.add_argument('--scope', choices=('active','full'), default='active',
                        help='Git active selects current code/tests/core docs; archive always restores full tracked tree')
    parser.add_argument('--max-total-gib', type=int, default=4)
    parser.add_argument('--max-file-gib', type=int, default=1)
    parser.add_argument('--max-download-gib', type=int, default=2)
    args = parser.parse_args(argv)
    stage = None
    try:
        if not all(1 <= n <= 64 for n in (
                args.max_total_gib, args.max_file_gib, args.max_download_gib)):
            raise ValueError('explicit file budgets must be between 1 and 64 GiB')
        MAX_TOTAL = args.max_total_gib*1024**3
        MAX_FILE = args.max_file_gib*1024**3
        MAX_DOWNLOAD = args.max_download_gib*1024**3
        manifest = package_manifest(ROOT)
        pin = json.loads((ROOT/'PIN.json').read_text(encoding='utf-8'))
        validate_pin(pin)
        target = args.target.absolute()
        safe_ancestors(target)
        if target.exists():
            raise ValueError('target exists; choose a NEW directory, no overwrite or clean')
        target.parent.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix='.mcp-restore-', dir=target.parent))
        method = ('git' if shutil.which('git') else 'archive') if args.method == 'auto' else args.method
        if method == 'git':
            rows = restore_git(stage, pin, scope=args.scope)
        else:
            archive = stage.parent/(stage.name+'.zip')
            download(pin['archive_url'], archive)
            rows = extract_archive(archive, stage, pin['tree'])
        receipt = write_overlay(stage, pin, manifest, rows, method)
        safe_ancestors(target)
        if target.exists() or reparse(target):
            raise ValueError('target appeared during recovery; refusing overwrite')
        stage.rename(target)
        print(json.dumps({
            'status': receipt['status'], 'target': str(target),
            'files': len(rows), 'method': method,
            'source_commit': pin['commit'], 'candidate_status': pin['candidate_status'],
            'restore_scope': receipt['restore_scope'],
            'deferred_blob_count': receipt['deferred_blob_count'],
            'next': 'Read MASTER_GOAL.md and TASKS.json. Continue all U01-U12 tasks; final independent Reviewer acceptance is required. No old runtime authority is restored.'
        }, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        failure = {'status': 'RECOVERY_FAILED', 'staging': str(stage) if stage else None,
                   'type': type(exc).__name__, 'message': str(exc)}
        state = ROOT/'_state'
        state.mkdir(exist_ok=True)
        (state/'recovery_failure.json').write_text(
            json.dumps(failure, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        print(json.dumps(failure, ensure_ascii=False))
        return 2

if __name__ == '__main__':
    raise SystemExit(main())


