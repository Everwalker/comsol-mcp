import os,sys,json,subprocess,time
from pathlib import Path
root=Path(__file__).parent;argv=[sys.executable,'-B','-u',str(root/'acquire_openssl359_exact_once.py')]
(root/'download.argv.json').write_text(json.dumps({'argv':argv,'actual_wrapper_pid':os.getpid()}))
start=time.monotonic()
with (root/'download.stdout').open('xb') as stdout,(root/'download.stderr').open('xb') as stderr:
 p=subprocess.Popen(argv,stdout=stdout,stderr=stderr);(root/'download.spawn.json').write_text(json.dumps({'actual_pid':p.pid,'argv':argv}));rc=p.wait();stdout.flush();stderr.flush();os.fsync(stdout.fileno());os.fsync(stderr.fileno())
(root/'download.terminal.json').write_text(json.dumps({'actual_pid':p.pid,'actual_wait_rc':rc,'elapsed_including_flush_s':time.monotonic()-start,'native_build':0}));print(json.dumps({'rc':rc,'pid':p.pid}));print((root/'download.stdout').read_text());print((root/'download.stderr').read_text()[-1800:]);sys.exit(rc)
