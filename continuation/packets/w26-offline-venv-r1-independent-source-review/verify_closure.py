from pathlib import Path
import ast,hashlib,io,json,os,stat,sys,zipfile,re,tomllib
B=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2');C=B/'local-preservation/w26-offline-venv-unit-r1';O=Path(__file__).parent;E=B/'repository/docs/full_project_continuation_v2/evidence/intel-builder-execution-r1';sys.path.insert(0,str(C));import common
from packaging.requirements import Requirement
refs={}
def row(p):
 fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW)
 try:
  a=os.fstat(fd);assert stat.S_ISREG(a.st_mode) and a.st_nlink==1 and not a.st_mode&0o022
  h=hashlib.sha256();n=0
  while b:=os.read(fd,1048576):h.update(b);n+=len(b)
  z=os.fstat(fd);named=p.lstat();key=lambda q:(q.st_dev,q.st_ino,q.st_mode,q.st_uid,q.st_nlink,q.st_size,q.st_mtime_ns,q.st_ctime_ns)
  assert key(a)==key(z)==key(named)
  return {'path':str(p),'bytes':n,'sha256':h.hexdigest(),'dev':a.st_dev,'inode':a.st_ino,'uid':a.st_uid,'mode':stat.S_IMODE(a.st_mode),'nlink':a.st_nlink}
 finally:os.close(fd)
def collect(x):
 if isinstance(x,dict):
  if isinstance(x.get('path'),str) and Path(x['path']).is_absolute() and 'bytes' in x and 'sha256' in x:
   old=refs.get(x['path']);assert old is None or (old['bytes'],old['sha256'])==(x['bytes'],x['sha256']);refs[x['path']]=x
  for v in x.values():collect(v)
 elif isinstance(x,list):
  for v in x:collect(v)
def checked(r):
 actual=row(Path(r['path']))
 for k in actual.keys()&r.keys():assert actual[k]==r[k],(r['path'],k,actual[k],r[k])
 return actual
def load(p,expected=None):
 r=row(p);assert expected is None or r['sha256']==expected;collect(r);x=json.loads(p.read_bytes());collect(x);return x
def save(name,obj):
 fd=os.open(O/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 with os.fdopen(fd,'w') as f:f.write(json.dumps(obj,indent=2)+'\n')
f=load(C/'FREEZE_R1.json','b5fff6f1b2dedafc66b4bcdf0f0c4bd67cfc9968f4233196d0cabe105673fed2');root=load(E/'ROOT_W26_OFFLINE_VENV_SOURCE_REVIEW_R1.json','55823360be5ed0cf23ce194153683ac8fff69a6e05b2e697a14cb6140b5c2bce');m=load(Path(f['metadata']['path']));W=Path(f['W']);context=load(C/'CONTEXT.json');graph=load(C.parent/'w26-offline-input-closure-r1/ACTUAL43_METADATA_CLOSURE.json');primary=load(C/'PRIMARY_REFS.json')
assert f['files']==[row(p) for p in sorted(C.iterdir()) if p.is_file() and p.name!='FREEZE_R1.json']
assert f['work_files']==[row(p) for p in sorted(W.iterdir()) if p.is_file()]
common.secure_dir(W,f['W_identity']);assert (W/'logs').is_dir() and not list((W/'logs').iterdir())
for r in f['work_files']:
 if Path(r['path']).name!='METADATA.json':assert Path(r['path']).read_bytes()==(C/Path(r['path']).name).read_bytes()
syntax=[]
for p in list(C.glob('*.py'))+list(W.glob('*.py')):
 ast.parse(p.read_bytes(),filename=str(p));syntax.append(row(p))
assert len(syntax)==16
assert len(f['files'])==32 and len(f['work_files'])==9 and len(m['input_refs'])==43 and len(m['archive_refs'])==46 and len(m['old_trees'])==2
assert m['commands']==f['actual_command_plan'] and m['context']==context and m['budget']==f['budget'] and m['budget']=={'whole':1800,'cleanup':300,'prepare':600,'profile':180,'epoch':'controller main entry before preflight/Popen; shared dynamic elapsed, no reset'}
assert f['actual8commands_not_run'] is True and f['controller']==row(W/'controller.py')
labels=[r['label'] for r in m['commands']];assert labels==['builder-create','runtime-create','builder-install','runtime-install','builder-check','runtime-check','builder-profile','runtime-profile']
for r in m['commands']:
 argv=r['argv'];assert argv[1:3]==['-I','-B'] and r['cap'] in (180,300)
 if r['label'].endswith('-install'):assert all(x in argv for x in ['--no-index','--only-binary=:all:','--require-hashes','--no-cache-dir','--no-input','--disable-pip-version-check'])
 if r['label'].endswith('-create'):assert argv[3:]==['-m','venv','--copies',str(Path(context['unit_root'])/r['label'].split('-')[0])]
assert {r['path']:r for r in m['input_refs']}=={r['actual_record']['path']:r['actual_record'] for r in graph['actual_inputs']}
assert context['actual_input_rows']==graph['actual_inputs'] and graph['counts']['active_dependency_checks']==61 and graph['counts']['dependency_checks']==199
by={r['name']:r for r in graph['actual_inputs']};module_checks=[];wheel_checks=[]
for r in graph['actual_inputs']:
 raw,accepted=common.raw_file(Path(r['actual_record']['path']),expected=r['actual_record'])
 with zipfile.ZipFile(io.BytesIO(raw)) as z:
  meta=[n for n in z.namelist() if n.count('/')==1 and n.endswith('.dist-info/METADATA')];wh=[n for n in z.namelist() if n.count('/')==1 and n.endswith('.dist-info/WHEEL')];assert len(meta)==len(wh)==1
  assert hashlib.sha256(z.read(meta[0])).hexdigest()==r['METADATA_sha256'] and hashlib.sha256(z.read(wh[0])).hexdigest()==r['WHEEL_sha256']
  for role,spec in context['roles'].items():
   for md in spec['modules']:
    if md['distribution']==r['name']:assert hashlib.sha256(z.read(md['relative_path'])).hexdigest()==md['sha256'];module_checks.append({'role':role,'module':md['module'],'accepted_wheel':accepted['path'],'member':md['relative_path'],'member_sha256':md['sha256']})
 wheel_checks.append({'name':r['name'],'version':r['version'],'wheel':accepted,'METADATA':r['METADATA_sha256'],'WHEEL':r['WHEEL_sha256']})
assert len(wheel_checks)==43 and len(module_checks)==45 and len(context['roles']['builder']['distributions'])==3 and len(context['roles']['runtime']['distributions'])==41 and len(context['roles']['runtime']['modules'])==42
for role,spec in context['roles'].items():
 expected={r['name']:(r['version'],r['METADATA_sha256'],r['WHEEL_sha256']) for r in spec['distributions']}
 for name,values in expected.items():assert values==(by[name]['version'],by[name]['METADATA_sha256'],by[name]['WHEEL_sha256'])
 text=(C/('requirements-backend.lock' if role=='builder' else 'requirements-runtime.lock')).read_text();pinned={m[0]:(m[1],m[2]) for m in re.findall(r'(?m)^([^#\s=]+)==([^\s]+) --hash=sha256:([a-f0-9]{64})$',text)}
 assert pinned=={name:(by[name]['version'],by[name]['actual_record']['sha256']) for name in expected}
constraints=[];project=tomllib.loads((B/'repository/pyproject.toml').read_text())
for text in project['project']['dependencies']+project['project']['optional-dependencies']['desktop']:
 req=Requirement(text);applies=not req.marker or req.marker.evaluate({**graph['marker_environment'],'extra':'desktop'});name=req.name.lower().replace('_','-')
 if applies:assert by[name]['version'] in req.specifier
 constraints.append({'requirement':text,'active':applies,'selected_version':by[name]['version'] if applies else None})
assert [{k:r[k] for k in ('requirement','active','selected_version')} for r in context['parent_project_constraints']]==constraints
raw,bootstrap=common.raw_file(Path(m['bootstrap']['path']),expected=m['bootstrap'])
with zipfile.ZipFile(io.BytesIO(raw)) as z:
 for leaf in ['METADATA','WHEEL']:assert hashlib.sha256(z.read('pip-25.0.1.dist-info/'+leaf)).hexdigest()==context['pip'][leaf+'_sha256']
common.system_binary(Path('/usr/bin/sandbox-exec'),m['fixed_sandbox_binary'])
source=(E/'tls_cpython_checkpoint_r4.py').read_text();ours=(C/'common.py').read_text();fn=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=='copy_blob');own=next(n for n in ast.parse(ours).body if isinstance(n,ast.FunctionDef) and n.name=='copy_blob');assert ast.get_source_segment(source,fn)==ast.get_source_segment(ours,own)
sys.path.insert(0,str(E));import intel_deps_stages as deps
trees=[]
for entry in m['old_trees']:
 p=Path(entry['root']);common.secure_dir(p,entry['identity']);snapshot=load(Path(entry['snapshot']['path']));actual=deps.tree_record(p);assert actual==snapshot;trees.append({'root':str(p),'records':len(actual),'original_snapshot_same':True})
assert [x['records'] for x in trees]==[16870,100]
assert sorted(p.name for p in Path(m['old_archive_root']).iterdir())==sorted(Path(r['path']).name for r in m['archive_refs'])
stderr=(C/'TEST_MAIN.stderr').read_text();assert 'Ran 21 tests' in stderr and stderr.rstrip().endswith('OK');assert 'PermissionError' in (C/'CONTEXT.stderr').read_text() or 'Refusal' in (C/'CONTEXT.stderr').read_text()
unit=Path(context['unit_root']);absent=[unit,W/'UNIT_ONCE_GUARD.json',W/'CONTROLLER_BIRTH.json',W/'TERMINAL.json',W/'COMPLETE_NEW_UNIT_TREE.json',Path(f['root_binding_expected']['path'])]
assert not any(os.path.lexists(p) for p in absent)
prefix=f['next_invocation_prefix'];assert prefix[-1]=='--binding-sha' and prefix[-2]==str(W/'controller.py') and len(prefix)==5
pins=[checked(r) for r in refs.values()]
for pin in pins:checked(pin)
report={'result':'PASS_MECHANICAL_INPUT_AND_SOURCE_CLOSURE_ONLY','freeze':row(C/'FREEZE_R1.json'),'Root_source_external_basis':row(E/'ROOT_W26_OFFLINE_VENV_SOURCE_REVIEW_R1.json'),'material32':True,'staged_work9_exact':True,'AST16_includes_duplicate_staged4':syntax,'source_sameFD_refs':len(pins),'all_first_pins_prepost_exact':True,'current43_wheel_metadata_WHEEL_MODULE_payload_checks':wheel_checks,'module_members45_builder3_runtime42':module_checks,'runtime_distributions41_builder3':True,'bundled_official_pip2501':bootstrap,'original_61active199all':True,'current_project_constraints':constraints,'old_complete_trees':trees,'old_archives46_full_membership':True,'current_source_tests':{'methods':21,'checks':25,'old_rerun':False,'guard':'NOT_SUPPLIED_ORIGINAL_PUBLIC_BOUNDARY_CONTROLS_NOT_NATIVE_NETWORK_PROOF'},'actual8command_plan':m['commands'],'entry_prefix':prefix,'actual_oncebinding_absent':True,'future_paths_absent':[str(p) for p in absent],'no_actual_unit_run':True,'no_targetPython_wheelcode_installer_sandbox_socket_native_COMSOL_OSprobe_CAS_Git_progress_effects':True,'first_sameFD_pins':pins}
save('CLOSURE_VERIFICATION.json',report);print(json.dumps({k:report[k] for k in ['source_sameFD_refs','old_complete_trees','runtime_distributions41_builder3','actual_oncebinding_absent']}))
