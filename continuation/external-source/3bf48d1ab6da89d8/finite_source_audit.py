import ast,hashlib,json,os
from pathlib import Path
BASE=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2');ROOT=Path(__file__).parent
TREES={'W23_r11':BASE/'local-preservation/w23-saved-mph-science-readback-20261013-candidate20-r11','Windows_r17':BASE/'local-preservation/windows_service_sid_sol_r17_20261013/candidate'}
WATCH=('gdbm','tkinter','dbm','shelve')
def row(p):
 fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW)
 try:
  st=os.fstat(fd);b=bytearray()
  while x:=os.read(fd,1048576):b.extend(x)
  fs=os.fstat(fd);ns=p.lstat()
  f=lambda x:(x.st_dev,x.st_ino,x.st_size,x.st_mode,x.st_uid,x.st_nlink)
  if f(st)!=f(fs) or f(st)!=f(ns) or len(b)!=st.st_size:raise RuntimeError('source changed during static read')
  return {'path':str(p),'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()},bytes(b)
 finally:os.close(fd)
result={}
for name,base in TREES.items():
 files=sorted(p for folder in ('comsol_mcp','tools') for p in (base/folder).rglob('*') if p.is_file() and not p.is_symlink() and p.suffix in ('.py','.java','.ps1','.md','.toml','.json') and '__pycache__' not in p.parts and not p.name.startswith('._'))
 records=[];watched=[];dynamic=[];positive=[];astcount=0
 for p in files:
  r,b=row(p);records.append(r);text=b.decode();relative=p.relative_to(base).as_posix()
  for n,line in enumerate(text.splitlines(),1):
   if any(w in line.lower() for w in WATCH):watched.append({'file':relative,'line':n,'text':line})
   if any(w in line for w in ('sqlite3.connect','from AppKit','import AppKit','System.Windows.Automation','tar.xz')):positive.append({'file':relative,'line':n,'text':line})
  if p.suffix=='.py':
   tree=ast.parse(text);astcount+=1
   for node in ast.walk(tree):
    if isinstance(node,ast.Call):
     fn=ast.unparse(node.func)
     if fn.endswith('import_module') or fn=='__import__':dynamic.append({'file':relative,'line':node.lineno,'call':ast.unparse(node)})
 for r in records:
  now,_=row(Path(r['path']));assert now==r
 result[name]={'root':str(base),'files':records,'Python_AST':astcount,'watched_product_references':watched,'all_dynamic_import_calls':dynamic,'positive_actual_source_references':positive,'prepost_source_bytes_equal':True,'runtime_imports_or_tests':0,'limited_scope':'finite product package/tools only, no generic stdlib crawl or transitive dependency source proof'}
with (ROOT/'FINITE_CURRENT_CANDIDATE_CENSUS.json').open('x') as f:json.dump(result,f,indent=2,sort_keys=True);f.write('\n')
print(json.dumps({k:{'files':len(v['files']),'AST':v['Python_AST'],'watched_matches':len(v['watched_product_references']),'dynamic':len(v['all_dynamic_import_calls'])} for k,v in result.items()},indent=2))
