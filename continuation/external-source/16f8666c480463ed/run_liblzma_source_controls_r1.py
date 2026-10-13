import os,sys,json,hashlib,tempfile,subprocess,time,ast
from pathlib import Path
E=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2/repository/docs/full_project_continuation_v2/evidence/intel-builder-execution-r1');T=Path('/private/var/folders/4d/6pphmzkx5szf8dyqx7zhqh1r0000gn/T');W=Path(tempfile.mkdtemp(prefix='comsol-liblzma-unit-source-controls-',dir=T));rows=[]
names=['liblzma_cpython_checkpoint_r1.py','liblzma_build_gate_r1.py','liblzma_runtime_profile_r1.py','liblzma_cert_checks_r1.py','liblzma_prepare_r1.py','liblzma_merge_r1.py','liblzma_acceptance_r1.py','liblzma_successor_r1.py','liblzma_verify_r1.py','test_liblzma_producer_r1.py','test_liblzma_unit_r1.py']
for n in names:
 raw=(E/n).read_bytes();ast.parse(raw);(W/n).write_bytes(raw);rows.append({'path':str(E/n),'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
(W/'sources.json').write_text(json.dumps(rows,indent=2)+'\n')
argv=[sys.executable,'-B','-m','pytest',str(E/'test_liblzma_producer_r1.py'),str(E/'test_liblzma_unit_r1.py'),'-q','--junitxml='+str(W/'raw.junit.xml'),'--basetemp='+str(W/'fixtures')];(W/'argv.json').write_text(json.dumps({'argv':argv,'cwd':str(E),'network_guard':'NOT_SUPPLIED','target_runtime':0,'native_compile':0,'official_unpack':0},indent=2)+'\n');start=time.monotonic();fds=[os.open(W/n,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600) for n in ('stdout','stderr')];p=subprocess.Popen(argv,cwd=E,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'),stdout=fds[0],stderr=fds[1]);(W/'spawn.json').write_text(json.dumps({'actual_pid':p.pid,'uid':os.getuid(),'argv':argv,'born_lstart':'NOT_CAPTURED'},indent=2)+'\n');rc=p.wait()
for fd in fds:os.fsync(fd);os.close(fd)
for r in rows:assert hashlib.sha256(Path(r['path']).read_bytes()).hexdigest()==r['sha256']
(W/'result.json').write_text(json.dumps({'actual_rc':rc,'elapsed_s':time.monotonic()-start,'11new_AST':True,'source_unchanged':True,'targetruntime_or_compile_or_official_unpacked':0},indent=2)+'\n');print(W);print(rc);print((W/'stdout').read_text());print((W/'stderr').read_text());sys.exit(rc)
