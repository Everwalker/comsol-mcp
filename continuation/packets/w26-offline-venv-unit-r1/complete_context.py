"""Finish SOURCE metadata after strict-user reader correctly rejected root-owned public SB primary."""
import json,os,stat,hashlib,tempfile,sys
from pathlib import Path
R=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2');D=Path(__file__).parent;A=D.parent/'w26-helper-successor-r2';O=D.parent/'w26-offline-input-closure-r1';E=R/'repository/docs/full_project_continuation_v2/evidence/intel-builder-execution-r1';sys.path.insert(0,str(A));from strict_derived import read_regular
primaries={}
for p in [Path('/usr/share/sandbox/mdworker-sizing.sb'),Path('/usr/share/sandbox/com.apple.mobileassetd.sb')]:
 fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW)
 try:
  s=os.fstat(fd);assert stat.S_ISREG(s.st_mode) and s.st_uid==0 and s.st_nlink==1 and s.st_size<1048576 and not s.st_mode&0o022
  raw=os.read(fd,1048576);n=p.lstat();assert (s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns)==(n.st_dev,n.st_ino,n.st_size,n.st_mtime_ns)
  primaries[str(p)]={'path':str(p),'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),'uid':s.st_uid,'dev':s.st_dev,'inode':s.st_ino,'mode':s.st_mode&0o777,'nlink':s.st_nlink};(D/('PRIMARY_'+p.name)).write_bytes(raw)
 finally:os.close(fd)
import zipfile
x=json.loads(read_regular(O/'ACTUAL43_METADATA_CLOSURE.json')[0]);row=next(r for r in x['actual_inputs'] if r['name']=='mph')
with zipfile.ZipFile(row['actual_record']['path']) as z:
 for n in ['mph/__init__.py','mph/config.py']:(D/('PRIMARY_'+n.replace('/','_'))).write_bytes(z.read(n))
(D/'PRIMARY_REFS.json').write_text(json.dumps({'public_sandbox_file_rules':primaries,'existing_release_helper':read_regular(R/'repository/tools/full_project_release.py')[1],'copy_blob_original':read_regular(E/'tls_cpython_checkpoint_r4.py')[1],'bootstrap_original_source':json.loads(read_regular(O/'BOOTSTRAP_PRIMARY.json')[0]),'MPh_first_config_rule':'Public config.load() tries owned current-dir MPh.ini first; fixed present fixture prevents user config data access, HOME inherited.'},indent=2,sort_keys=True)+'\n')
W=Path(tempfile.mkdtemp(prefix='comsol-w26-offline-venv-execution-r1-')).resolve();(W/'logs').mkdir(mode=0o700);(D/'W_PATH.txt').write_text(str(W)+'\n');print('W',W)
