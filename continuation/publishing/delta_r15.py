import os,stat,json,hashlib,re
from pathlib import Path
B=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2');O=B/'local-preservation/github-main-sync-20261013-r1';S=O/'send-tree';D=O/'delta-r15';D.mkdir(mode=0o700);R=B/'local-preservation/w23-saved-mph-science-readback-20261013-candidate20-r15';prefix='continuation/candidates/w23-r15';rows=[];local=[]
allowed={'.py','.java','.ps1','.psm1','.zsh','.bash','.fish','.sh','.c','.h','.sql','.schema','.html','.js','.css','.toml','.ini','.lock','.yaml','.yml','.md','.rst','.json','.patch','.diff','.sb'}
def copy(p,expected=None):
 p=Path(p);st=p.lstat();assert stat.S_ISREG(st.st_mode) and st.st_uid==os.getuid();raw=p.read_bytes();sha=hashlib.sha256(raw).hexdigest()
 if expected:assert len(raw)==expected['bytes'] and sha==expected['sha256'],str(p)
 rel=str(p.relative_to(R));dest=prefix+'/'+rel;q=S/dest;q.parent.mkdir(parents=True,exist_ok=True);q.write_bytes(raw);q.chmod(0o755 if st.st_mode&0o111 else 0o644);assert q.read_bytes()==raw
 rows.append({'source':str(p),'source_relative':str(p.relative_to(B)),'published_path':dest,'bytes':len(raw),'sha256':sha,'status':'REVIEW_PENDING_SOURCE_ONLY_NATIVE_NOT_RUN'})
for p in R.rglob('*'):
 if not p.is_file() or p.is_symlink() or any(x in p.parts for x in {'__pycache__','.git','comsol-server-home','node_modules'}):continue
 st=p.stat()
 if (p.suffix in allowed or p.name in {'LICENSE','Makefile','.gitignore','MANIFEST.in'}) and (st.st_size<=2*1024*1024 or p.suffix in {'.py','.java','.c','.h'}):copy(p)
 else:local.append({'source':str(p),'bytes':st.st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'reason':'local native/raw artifact, no code execution'})
binding=R/'root-review-packet/final3/FINAL_SOURCE_SOFTWARE_AND_FIXED_ENTRY_BINDING_ACTIVITY022_R15.json';assert hashlib.sha256(binding.read_bytes()).hexdigest()=='d963166d639549a7102a34db624428171a2b9dff345da4ad84cfc9e1022855b0';meta=json.loads(binding.read_text());packet=json.loads(Path(meta['wholepacket']['path']).read_text());current=packet['current_source_files'];assert len(current)==426
for name,record in current.items():
 p=Path(record['path']);q=S/prefix/name
 if not q.exists():copy(p,record)
 else:raw=q.read_bytes();assert len(raw)==record['bytes'] and hashlib.sha256(raw).hexdigest()==record['sha256'],name
for name in ('metadata','script','freeze','wholepacket','preflight_reference'):copy(meta[name]['path'],meta[name])
# The sole server-home entry is a blank frozen source configuration, not an active model/account ledger.
assert json.loads((R/'comsol-server-home/workflow_state.json').read_text())['current_main_model_path']==''
patterns=[rb'gh[pousr]_[A-Za-z0-9]{30,}',rb'AKIA[0-9A-Z]{16}',rb'(?m)^-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----$'];bad=[]
for row in rows:
 raw=(S/row['published_path']).read_bytes()
 if any(re.search(p,raw) for p in patterns):bad.append(row['published_path'])
assert not bad,bad
for name,data in [('R15_SOURCE_DELTA.json',rows),('R15_LOCAL_ARTIFACTS.json',local),('R15_SOURCE_CHECK.json',{'status':'PASS_426_EXACT_FROZEN_SOURCE_RECORDS_PLUS_FIXED_ENTRY','source_records':426,'delta_records':len(rows),'bytes':sum(r['bytes'] for r in rows),'metadata':meta['metadata'],'script':meta['script'],'source_review':'PENDING','native':'NOT_RUN','scientific_acceptance':'NOT_ACCEPTED','secret_pattern_findings':bad})]: (D/name).write_text(json.dumps(data,indent=2)+'\n')
(D/'STAGE_DELTA.nul').write_bytes(b'\0'.join(r['published_path'].encode() for r in rows)+b'\0');print('R15 delta',len(rows),sum(r['bytes'] for r in rows),'frozen426exact','local artifacts',len(local))
