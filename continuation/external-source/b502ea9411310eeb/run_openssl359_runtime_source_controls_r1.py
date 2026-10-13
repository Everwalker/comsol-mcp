import os,sys,json,hashlib,tempfile,subprocess,time,ast
from pathlib import Path
E=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2/repository/docs/full_project_continuation_v2/evidence/intel-builder-execution-r1');T=Path('/private/var/folders/4d/6pphmzkx5szf8dyqx7zhqh1r0000gn/T');W=Path(tempfile.mkdtemp(prefix='comsol-openssl359-runtime-source-controls-',dir=T))
names=['openssl359_runtime_profile_r2.py','openssl359_runtime_successor_r1.py','openssl359_runtime_verify_r1.py','test_openssl359_runtime_r1.py'];rows=[]
for n in names:
 raw=(E/n).read_bytes();ast.parse(raw);(W/n).write_bytes(raw);rows.append({'path':str(E/n),'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
(W/'sources.json').write_text(json.dumps(rows,indent=2)+'\n')
argv=[sys.executable,'-B','-m','pytest',str(E/names[-1]),'-q','--junitxml='+str(W/'raw.junit.xml'),'--basetemp='+str(W/'fixtures')];(W/'argv.json').write_text(json.dumps({'argv':argv,'cwd':str(E),'network_guard':'NOT_SUPPLIED','target_runtime':0,'native_compile':0},indent=2)+'\n')
fds=[os.open(W/n,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600) for n in ('stdout','stderr')];start=time.monotonic();p=subprocess.Popen(argv,cwd=E,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'),stdout=fds[0],stderr=fds[1]);(W/'spawn.json').write_text(json.dumps({'actual_pid':p.pid,'uid':os.getuid(),'argv':argv,'born_lstart':'NOT_CAPTURED'},indent=2)+'\n');rc=p.wait()
for fd in fds:os.fsync(fd);os.close(fd)
for row in rows:assert hashlib.sha256(Path(row['path']).read_bytes()).hexdigest()==row['sha256']
(W/'result.json').write_text(json.dumps({'actual_rc':rc,'elapsed_s':time.monotonic()-start,'new4_AST':True,'source_unchanged':True,'new_targetruntime_compile_unpack':0},indent=2)+'\n');print(W);print(rc);print((W/'stdout').read_text());print((W/'stderr').read_text());sys.exit(rc)
