import ast,hashlib,io,json,os,sys,tempfile,zipfile,tomllib
from pathlib import Path
from packaging.requirements import Requirement
R=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2');D=Path(__file__).parent;O=D.parent/'w26-offline-input-closure-r1';A=D.parent/'w26-helper-successor-r2';E=R/'repository/docs/full_project_continuation_v2/evidence/intel-builder-execution-r1'
sys.path.insert(0,str(A));from strict_derived import read_regular
x=json.loads(read_regular(O/'ACTUAL43_METADATA_CLOSURE.json')[0]);rows=x['actual_inputs'];by={r['name']:r for r in rows};env=x['marker_environment']
project=tomllib.loads(read_regular(R/'repository/pyproject.toml')[0].decode());constraints=[]
for text in project['project']['dependencies']+project['project']['optional-dependencies']['desktop']:
 req=Requirement(text);applies=not req.marker or req.marker.evaluate({**env,'extra':'desktop'});n=req.name.lower().replace('_','-')
 if applies and (n not in by or by[n]['version'] not in req.specifier):raise RuntimeError('actual project root version constraint unsatisfied: '+text)
 constraints.append({'requirement':text,'active':applies,'selected_version':by[n]['version'] if applies else None,'result':'PASS_EXACT_ACTUAL_METADATA_VERSION' if applies else 'INACTIVE_MARKER'})
unit=Path('/private/var/folders/4d/6pphmzkx5szf8dyqx7zhqh1r0000gn/T/comsol-intel-inputs-r1-obneuyy4/w26-dependency-environments-r1');base=unit.parent/'intel-liblzma-r2/cpython'
modules={'annotated-types':['annotated_types'],'anyio':['anyio'],'attrs':['attrs'],'certifi':['certifi'],'cffi':['cffi'],'click':['click'],'cryptography':['cryptography'],'h11':['h11'],'httpcore':['httpcore'],'httpx':['httpx'],'httpx-sse':['httpx_sse'],'idna':['idna'],'jpype1':['jpype'],'jsonschema':['jsonschema'],'jsonschema-specifications':['jsonschema_specifications'],'mcp':['mcp'],'mph':['mph'],'numpy':['numpy'],'packaging':['packaging'],'pycparser':['pycparser'],'pydantic':['pydantic'],'pydantic-core':['pydantic_core'],'pydantic-settings':['pydantic_settings'],'pyjwt':['jwt'],'python-dotenv':['dotenv'],'python-multipart':['python_multipart'],'referencing':['referencing'],'rpds-py':['rpds'],'sse-starlette':['sse_starlette'],'starlette':['starlette'],'typing-extensions':['typing_extensions'],'typing-inspection':['typing_inspection'],'uvicorn':['uvicorn'],'psutil':['psutil'],'pyobjc-core':['objc'],'pyobjc-framework-applicationservices':['ApplicationServices'],'pyobjc-framework-cocoa':['AppKit','Foundation'],'pyobjc-framework-coremedia':['CoreMedia'],'pyobjc-framework-coretext':['CoreText'],'pyobjc-framework-quartz':['Quartz'],'pyobjc-framework-screencapturekit':['ScreenCaptureKit'],'setuptools':['setuptools'],'wheel':['wheel']}
roles={};entries={}
for r in rows:
 raw,rec=read_regular(Path(r['actual_record']['path']));assert rec==r['actual_record']
 with zipfile.ZipFile(io.BytesIO(raw)) as z:
  picked=[]
  for m in modules[r['name']]:
   choices=[m.replace('.','/')+'/__init__.py',m.replace('.','/')+'.py'];found=[p for p in choices if p in z.namelist()]
   if len(found)!=1:raise RuntimeError('explicit official module leaf not unique: '+m)
   picked.append({'module':m,'relative_path':found[0],'sha256':hashlib.sha256(z.read(found[0])).hexdigest(),'distribution':r['name']})
  entries[r['name']]=picked
for role,selected in [('runtime',[r for r in rows if r['role']!='backend']),('builder',[by[n] for n in ['setuptools','wheel','packaging']])]:
 roles[role]={'distributions':[{'name':r['name'],'version':r['version'],'METADATA_sha256':r['METADATA_sha256'],'WHEEL_sha256':r['WHEEL_sha256']} for r in selected],'modules':[m for r in selected for m in entries[r['name']]]}
bundle=base/'lib/python3.12/ensurepip/_bundled/pip-25.0.1-py3-none-any.whl';raw,bundled=read_regular(bundle)
with zipfile.ZipFile(io.BytesIO(raw)) as z:pip={'name':'pip','version':'25.0.1','METADATA_sha256':hashlib.sha256(z.read('pip-25.0.1.dist-info/METADATA')).hexdigest(),'WHEEL_sha256':hashlib.sha256(z.read('pip-25.0.1.dist-info/WHEEL')).hexdigest()}
context={'schema':'comsol-w26-dependency-consumer-context/1','unit_root':str(unit),'base_prefix':str(base),'roles':roles,'pip':pip,'bootstrap_record':bundled,'mph_public_defaults':{'session':'client-server','caching':False,'classkit':False},'parent_project_constraints':constraints,'actual_marker_environment':env,'actual_input_rows':rows,'original61active199finite':{'active':61,'all':199},'packaging_version_correction':'26.3 actual immutable wheel/lock d7193…; earlier26.2 prose preserved in oldR1freeze, not rewritten'}
with (D/'CONTEXT.json').open('x') as f:json.dump(context,f,indent=2,sort_keys=True);f.write('\n')
for name in ['requirements-runtime.lock','requirements-backend.lock']:(D/name).write_bytes((O/name).read_bytes())
# A fixed synthetic current-dir MPh config prevents mph.config.load() reaching user HOME.
ini=b'[config]\nsession = client-server\ncaching = False\nclasskit = False\n';(D/'MPh.ini').write_bytes(ini)
primaries={}
for p in ['/usr/share/sandbox/mdworker-sizing.sb','/usr/share/sandbox/com.apple.mobileassetd.sb']:
 raw,record=read_regular(Path(p));primaries[p]=record
# Capture only public wheel source relevant to import boundary.
with zipfile.ZipFile(by['mph']['actual_record']['path']) as z:
 for n in ['mph/__init__.py','mph/config.py']:(D/('PRIMARY_'+n.replace('/','_'))).write_bytes(z.read(n))
(D/'PRIMARY_REFS.json').write_text(json.dumps({'public_sandbox_file_rules':primaries,'existing_release_helper':read_regular(R/'repository/tools/full_project_release.py')[1],'copy_blob_original':read_regular(E/'tls_cpython_checkpoint_r4.py')[1],'bootstrap_original_source':json.loads(read_regular(O/'BOOTSTRAP_PRIMARY.json')[0]),'MPh_first_config_rule':'mph/config.py load() constructs folders [cwd,location(),module] and opens first present MPh.ini; owned cwd fixture exists and is pinned, HOME unchanged, no user MPh.ini read required.'},indent=2,sort_keys=True)+'\n')
W=Path(tempfile.mkdtemp(prefix='comsol-w26-offline-venv-execution-r1-')).resolve();(W/'logs').mkdir(mode=0o700);(D/'W_PATH.txt').write_text(str(W)+'\n')
print('context',read_regular(D/'CONTEXT.json')[1]);print('W',W);print('constraints',constraints)
