import os,sys,json,subprocess,time
from pathlib import Path
root=Path(__file__).parent
argv=[sys.executable,'-B','-u',str(root/'capture_official_metadata_r2.py')]
(root/'wrapper.argv.json').write_text(json.dumps({'argv':argv,'parent_pid':os.getpid(),'scope':'metadata only'}))
start=time.monotonic()
with (root/'raw.stdout').open('xb') as stdout,(root/'raw.stderr').open('xb') as stderr:
 p=subprocess.Popen(argv,stdout=stdout,stderr=stderr)
 (root/'wrapper.spawn.json').write_text(json.dumps({'actual_pid':p.pid,'argv':argv}))
 rc=p.wait();stdout.flush();stderr.flush();os.fsync(stdout.fileno());os.fsync(stderr.fileno())
(root/'wrapper.terminal.json').write_text(json.dumps({'actual_rc':rc,'actual_pid':p.pid,'actual_wait_terminal':True,'elapsed_s':time.monotonic()-start}))
print(json.dumps({'root':str(root),'rc':rc}));print((root/'raw.stdout').read_text());print((root/'raw.stderr').read_text()[-1600:]);sys.exit(rc)
