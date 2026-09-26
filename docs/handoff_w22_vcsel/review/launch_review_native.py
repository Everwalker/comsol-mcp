"""Windows-only independent Reviewer dispatch for a frozen public MCP runner.

Called only after main scan/control termination. Does not install or mutate the
shared task venv. All model/server state lives in a new Reviewer run directory.
"""
import argparse,hashlib,json,os,subprocess
from pathlib import Path
ap=argparse.ArgumentParser()
ap.add_argument('--version',choices=['6.3','6.4'],required=True)
ap.add_argument('--name',required=True)
ap.add_argument('--wheel-sha256',required=True)
ap.add_argument('--reopen')
ap.add_argument('--foreground',action='store_true',help='Keep SSH session alive until Windows child exits.')
a=ap.parse_args()
assert os.name=='nt','dispatch only on Windows native test node'
root=Path(r'C:\Temp\w22_20260926')
assert a.name.replace('_','').isalnum(),'simple owned-run name required'
assert not (root/a.name).exists(),'refuse existing Reviewer work directory'
wheel=root/'comsol_mcp-0.1.9-py3-none-any.whl'
assert hashlib.sha256(wheel.read_bytes()).hexdigest()==a.wheel_sha256,'candidate wheel mismatch'
command=[str(root/'venv'/'Scripts'/'python.exe'),str(root/'run_w22_native.py'),
 '--work',str(root/a.name),'--version',a.version,
 '--comsol',r'C:\Program Files\COMSOL\COMSOL'+a.version.replace('.','')+r'\Multiphysics',
 '--jdk',r'C:\Users\Everwalker\jdk11','--fixture',str(root/'W22Fixture.java'),
 '--spec',str(root/'benchmark_spec.json')]
if a.reopen:command+=['--reopen',str(root/a.reopen)]
else:command+=['--candidate',json.dumps({'L':.03,'p0':.6,'p1':.65})]
log=root/(a.name+'.log')
with log.open('xb') as output:
    process=subprocess.Popen(command,stdout=output,stderr=subprocess.STDOUT,cwd=root)
record={'reviewer_dispatch':True,'pid':process.pid,'command':command,'log':str(log),
 'wheel_sha256':a.wheel_sha256,'runner_sha256':hashlib.sha256((root/'run_w22_native.py').read_bytes()).hexdigest()}
(root/(a.name+'_reviewer_dispatch.json')).write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps(record,indent=2))
if a.foreground:
    raise SystemExit(process.wait())
