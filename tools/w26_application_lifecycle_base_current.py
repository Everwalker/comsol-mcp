"""Current closed installed-app lifecycle: one original W26 owner, existing release APIs."""
import argparse,ast,json,os,platform,re,socket,subprocess,sys,time
from pathlib import Path
from types import SimpleNamespace
from packaging.markers import default_environment
from tools import w26_runtime_actor_current as actor,full_project_release as release,release_transition_lifecycle as lifecycle,w26_sealed_data_current as data,w26_dependency_unit_current as deps,w26_application_bundle_current as app
from tools.w26_prepare_current_inputs import _helpers,COMMON

SEAL='/private/tmp/cvw26-lifecycle-inputs-c4563dee9656/SEAL_FINAL.json'
SEAL_SHA='1b06bc4b8f681d61139a41307254b8e5b13759eaad2344922d06311b51ce7d76'
APP_ROOT=Path('/private/tmp/cvw26-application-640d8c72')
DEP_ROOT=Path('/private/tmp/cvw26-dependencies-2cd91a47')
OLD_SHA='c1d82034e19e1f0fd897f3ec76911a3ccad229ed35f613148633bfb56f923ae6'
NEW_SHA='597d3f6dda5e1a3b29af5b6035db64ccfc1c68914725c4845576a859877657df'
DESKTOP_STATUS_CODE="""import hashlib,json,os,pathlib,sys,jpype
from importlib import metadata
from comsol_mcp._desktop_platforms import create_native_metadata_adapter
import comsol_mcp._desktop_platforms as factory_source,comsol_mcp._desktop_macos as native_source,comsol_mcp._desktop_service as service_source
assert metadata.version('comsol-mcp')=='0.1.10'
before=bool(jpype.isJVMStarted());assert before is False
status=create_native_metadata_adapter().status()
after=bool(jpype.isJVMStarted());assert after is False
sources={}
for module in (factory_source,native_source,service_source):
 p=pathlib.Path(module.__file__);sources[module.__name__]={'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
print(json.dumps({'caller_pid':os.getpid(),'executable':sys.executable,'installed_version':metadata.version('comsol-mcp'),'status':status,'source_hashes':sources,'jpype_started_before_after':[before,after],'source_bound_api_upper_limit':{'AXIsProcessTrusted':1,'CGPreflightScreenCaptureAccess':1},'prompt_requested':False,'window_tree_pixels_enumerated':False,'GUI_certification':'NOT_RUN'}))"""

SYSTEM_PS_SIGNATURE=Path(__file__).parents[2]/'_state/w26-system-ps-signature-input-r1.json'
SYSTEM_PS_SIGNATURE_SHA='b0158f5e95041e3c406c00f4b69f3b22fce82038f0a3e21ca30a75db17e5b63f'
SYSTEM_READER_ARGV=['/bin/ps','-axo','pid=,ppid=,command=']
R18_SYSTEM_READER_PROFILE='macos-arm64-current-system-reader-r18'
PROFILE=R18_SYSTEM_READER_PROFILE
R18_SYSTEM_READER_SCHEMA='w26-current-system-reader-proof/r18'
R18_SYSTEM_READER_BINDING_SCHEMA='w26-current-system-reader-proof-binding/r18'
SCHEMA=R18_SYSTEM_READER_SCHEMA
BINDING_SCHEMA=R18_SYSTEM_READER_BINDING_SCHEMA
R18_LIFECYCLE_PARENT=Path('/private/tmp')
R18_LIFECYCLE_ROOT_RE=re.compile(r'cvw26-lifecycle-r15-[0-9a-f]{8}')
R18_PROOF_FILENAME='CURRENT_SYSTEM_READER_PROOF_R18.json'
R18_DIAGNOSTIC_FILENAME='CURRENT_SYSTEM_READER_CODESIGN_DIAGNOSTIC_R18.json'
R18_DISPLAY_ARGV=['/usr/bin/codesign','-d','--verbose=4','/bin/ps']
R18_VERIFY_ARGV=['/usr/bin/codesign','--verify','--strict','--verbose=2','/bin/ps']
DISPLAY_ARGV=R18_DISPLAY_ARGV
VERIFY_ARGV=R18_VERIFY_ARGV
PROOF_FILENAME=R18_PROOF_FILENAME
DIAGNOSTIC_FILENAME=R18_DIAGNOSTIC_FILENAME
R18_COMMAND_ENV={'PATH':'/usr/bin:/bin','LC_ALL':'C'}
R18_REQUIRED_DISPLAY_LINES=('Identifier=com.apple.ps','Authority=macOS Software Signing',
    'Authority=Apple Code Signing Certification Authority','Authority=Apple Root CA')
R18_REQUIRED_VERIFY_LINES=('/bin/ps: valid on disk','/bin/ps: satisfies its Designated Requirement')

def _r18_lifecycle_root(root):
    path=Path(root)
    if (not path.is_absolute() or path.parent!=R18_LIFECYCLE_PARENT
            or not R18_LIFECYCLE_ROOT_RE.fullmatch(path.name) or path.is_symlink()
            or path.resolve(strict=True)!=path):raise RuntimeError('R18 proof requires the exact fresh lifecycle root namespace')
    h=_helpers();first_raw,_=h.raw_file(path/'FIRST_RECORD.json',maximum=65536);first=json.loads(first_raw)
    if type(first) is not dict or first.get('root')!=str(path):raise RuntimeError('R18 lifecycle root first record is malformed')
    identity=first.get('root_identity')
    if type(identity) is not dict or set(identity)!={'device','inode'} or any(type(v) is not int for v in identity.values()):
        raise RuntimeError('R18 lifecycle root first record is malformed')
    h.secure_dir(path,identity)
    return path,identity

def _r18_reader_identity(record,path,mode):
    if (type(record) is not dict or set(record)!={'path','bytes','sha256','identity'}
            or record.get('path')!=path or type(record.get('bytes')) is not int
            or type(record.get('sha256')) is not str or not re.fullmatch(r'[0-9a-f]{64}',record.get('sha256',''))):
        return [path+':record-shape']
    identity=record.get('identity')
    if type(identity) is not list or len(identity)!=8 or any(type(v) is not int for v in identity):
        return [path+':record-fields']
    failures=[]
    if identity[2]!=0:failures.append(path+':not-root-owned')
    if identity[3]&0o7777!=mode:failures.append(path+':mode-differs')
    if identity[4]!=1:failures.append(path+':link-count-differs')
    if identity[5]!=record['bytes']:failures.append(path+':size-differs')
    if identity[3]&0o022:failures.append(path+':group-or-other-writable')
    return failures

def _r18_validate_outputs(display,verify):
    failures=[]
    for name,value in (('display',display),('verify',verify)):
        if type(value) is not dict or set(value)!={'argv','returncode','stdout','stderr'}:
            failures.append(name+'-record-shape')
    if type(display) is not dict:display={}
    if type(verify) is not dict:verify={}
    if display.get('argv')!=R18_DISPLAY_ARGV:failures.append('display-argv-differs')
    if display.get('returncode')!=0:failures.append('display-returncode-not-zero')
    display_lines=display.get('stderr','').splitlines() if type(display.get('stderr')) is str else []
    if display.get('stdout')!='' or not all(line in display_lines for line in R18_REQUIRED_DISPLAY_LINES):
        failures.append('display-Apple-authority-output-differs')
    if any('Authority=(unavailable)' in line for line in display_lines):failures.append('display-authority-unavailable')
    if verify.get('argv')!=R18_VERIFY_ARGV:failures.append('verify-argv-differs')
    if verify.get('returncode')!=0:failures.append('verify-returncode-not-zero')
    verify_lines=verify.get('stderr','').splitlines() if type(verify.get('stderr')) is str else []
    if verify.get('stdout')!='' or tuple(verify_lines)!=R18_REQUIRED_VERIFY_LINES:failures.append('verify-strict-output-differs')
    return failures

def _r18_codesign_run(argv):
    result=subprocess.run(argv,cwd='/',env=dict(R18_COMMAND_ENV),stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=15,check=False)
    return {'argv':list(argv),'returncode':result.returncode,
        'stdout':result.stdout.decode('utf-8',errors='strict'),'stderr':result.stderr.decode('utf-8',errors='strict')}

def _r18_codesign_observation():
    if platform.system()!='Darwin' or platform.machine()!='arm64':
        raise RuntimeError('R18 current system-reader proof is finite to macOS arm64')
    before_ps=actor.system_record('/bin/ps');before_codesign=actor.system_record('/usr/bin/codesign')
    display=_r18_codesign_run(R18_DISPLAY_ARGV);verify=_r18_codesign_run(R18_VERIFY_ARGV)
    after_ps=actor.system_record('/bin/ps');after_codesign=actor.system_record('/usr/bin/codesign')
    if before_ps!=after_ps or before_codesign!=after_codesign:
        raise RuntimeError('R18 system-reader identity changed while capturing codesign proof')
    failures=_r18_reader_identity(after_ps,'/bin/ps',0o4755)
    failures.extend(_r18_reader_identity(after_codesign,'/usr/bin/codesign',0o755))
    failures.extend(_r18_validate_outputs(display,verify))
    return {'schema':R18_SYSTEM_READER_SCHEMA,'profile':R18_SYSTEM_READER_PROFILE,
        'status':'PASS_CURRENT_CODESIGN_STRICT' if not failures else 'NOT_READY_CURRENT_CODESIGN_STRICT',
        'read_only':True,'network':'NOT_USED','system_ps':after_ps,'codesign_system':after_codesign,
        'display':display,'verify':verify,'failure_reasons':failures,
        'producer_source':_helpers().raw_file(Path(__file__).resolve(strict=True))[1]}

def capture_current_system_reader_proof(root):
    """Capture strict read-only codesign output into one exact new lifecycle root."""
    lifecycle_root,root_identity=_r18_lifecycle_root(root);h=_helpers()
    proof_path=lifecycle_root/PROOF_FILENAME;diagnostic_path=lifecycle_root/DIAGNOSTIC_FILENAME
    if proof_path.exists() or proof_path.is_symlink():raise RuntimeError('R18 system-reader proof path is occupied; no replacement')
    if diagnostic_path.exists() or diagnostic_path.is_symlink():raise RuntimeError('R18 diagnostic path is occupied; no replacement')
    observation=_r18_codesign_observation()
    value={**observation,'lifecycle_root':str(lifecycle_root),'lifecycle_root_identity':root_identity}
    if observation['status']!='PASS_CURRENT_CODESIGN_STRICT':
        record=h.publish(diagnostic_path,value)
        return {'status':observation['status'],'diagnostic':record,'proof':'NOT_CREATED',
                'failure_reasons':observation['failure_reasons']}
    record=h.publish(proof_path,value)
    return {'status':observation['status'],'proof':record,'profile':R18_SYSTEM_READER_PROFILE,
            'root':str(lifecycle_root),'root_identity':root_identity,'read_only':True,'network':'NOT_USED'}

def bind_current_system_reader_proof(root,path,expected_sha256):
    lifecycle_root,root_identity=_r18_lifecycle_root(root);proof_path=lifecycle_root/PROOF_FILENAME
    if str(path)!=str(proof_path) or type(expected_sha256) is not str or not re.fullmatch(r'[0-9a-f]{64}',expected_sha256):
        raise RuntimeError('R18 current system-reader proof path/digest is outside the finite profile')
    h=_helpers();raw,record=h.raw_file(proof_path,maximum=65536)
    if record.get('sha256')!=expected_sha256:raise RuntimeError('R18 current system-reader proof digest differs from the Main-bound value')
    binding={'schema':R18_SYSTEM_READER_BINDING_SCHEMA,'profile':R18_SYSTEM_READER_PROFILE,
        'root':str(lifecycle_root),'root_identity':root_identity,'expected_sha256':expected_sha256,'first_record':record}
    verify_current_system_reader_binding(binding)
    return binding

def verify_current_system_reader_binding(binding,expected_root=None):
    expected={'schema','profile','root','root_identity','expected_sha256','first_record'}
    if type(binding) is not dict or set(binding)!=expected or binding.get('schema')!=R18_SYSTEM_READER_BINDING_SCHEMA \
            or binding.get('profile')!=R18_SYSTEM_READER_PROFILE:
        raise RuntimeError('R18 current system-reader proof binding shape/profile differs')
    root,root_identity=_r18_lifecycle_root(binding.get('root',''))
    if binding.get('root_identity')!=root_identity or (expected_root is not None and str(Path(expected_root))!=str(root)):
        raise RuntimeError('R18 current system-reader proof lifecycle root identity differs')
    proof_path=root/PROOF_FILENAME;record=binding.get('first_record')
    if type(record) is not dict or record.get('path')!=str(proof_path) or record.get('sha256')!=binding.get('expected_sha256'):
        raise RuntimeError('R18 current system-reader proof first-record path/digest differs')
    h=_helpers();raw,observed=h.raw_file(proof_path,maximum=65536,expected=record)
    if observed!=record or observed.get('sha256')!=binding['expected_sha256']:
        raise RuntimeError('R18 current system-reader proof first record changed')
    value=json.loads(raw)
    if (type(value) is not dict or value.get('schema')!=R18_SYSTEM_READER_SCHEMA
            or value.get('profile')!=R18_SYSTEM_READER_PROFILE or value.get('status')!='PASS_CURRENT_CODESIGN_STRICT'
            or value.get('read_only') is not True or value.get('network')!='NOT_USED'
            or value.get('lifecycle_root')!=str(root) or value.get('lifecycle_root_identity')!=root_identity
            or value.get('failure_reasons')!=[]):raise RuntimeError('R18 current system-reader proof content/status differs')
    if value.get('producer_source')!=h.raw_file(Path(__file__).resolve(strict=True))[1]:
        raise RuntimeError('R18 current system-reader proof producer source differs')
    failures=_r18_reader_identity(value.get('system_ps'),'/bin/ps',0o4755)
    failures.extend(_r18_reader_identity(value.get('codesign_system'),'/usr/bin/codesign',0o755))
    if actor.system_record('/bin/ps')!=value.get('system_ps'):failures.append('current-ps-first-record-differs')
    if actor.system_record('/usr/bin/codesign')!=value.get('codesign_system'):failures.append('current-codesign-first-record-differs')
    failures.extend(_r18_validate_outputs(value.get('display'),value.get('verify')))
    if failures:raise RuntimeError('R18 current system-reader proof fields fail strict validation: '+','.join(failures))
    return value

def verify_system_reader_signature(record):
    h=_helpers()
    if record['path']!=str(SYSTEM_PS_SIGNATURE) or record['sha256']!=SYSTEM_PS_SIGNATURE_SHA:raise RuntimeError('fixed system reader signature input differs')
    value=json.loads(h.raw_file(Path(record['path']),expected=record)[0]);system=value['system_ps']
    if (value['schema']!='w26-fixed-system-ps-signature/1' or value['status']!='PASS_OBSERVED_CODESIGN_STRICT' or value['verify_returncode']!=0
            or system['path']!='/bin/ps' or system['identity'][2]!=0 or system['identity'][3]&0o7777!=0o4755
            or actor.system_record('/bin/ps')!=system or actor.system_record('/usr/bin/codesign')!=value['codesign_system']
            or value['display_argv']!=['/usr/bin/codesign','-d','--verbose=4','/bin/ps']
            or value['verify_argv']!=['/usr/bin/codesign','--verify','--strict','--verbose=2','/bin/ps']):raise RuntimeError('fixed original Apple system reader identity/signature differs')
    display=h.raw_file(Path(value['display_stderr']['path']),expected=value['display_stderr'])[0].decode()
    verified=h.raw_file(Path(value['verify_stderr']['path']),expected=value['verify_stderr'])[0].decode()
    if not all(line in display.splitlines() for line in ['Identifier=com.apple.ps','Authority=macOS Software Signing','Authority=Apple Code Signing Certification Authority','Authority=Apple Root CA']) or verified.splitlines()!=['/bin/ps: valid on disk','/bin/ps: satisfies its Designated Requirement']:raise RuntimeError('actual Apple system reader codesign evidence differs')
    return value

def system_reader_lane(row,command):
    if row['label']!='uninstall-process-guard' or row.get('lane')!='SYSTEM_READER' or row['cap']!=15 or row['max_calls']!=2 or row['request_argv']!=['ps','-axo','pid=,ppid=,command='] or command!=SYSTEM_READER_ARGV:raise RuntimeError('exact readonly system reader lane refused')

def verify_prepared_system_reader(prepared, root=None):
    profile=prepared.get('system_reader_profile')
    if profile is None:
        if 'current_system_reader_proof' in prepared:raise RuntimeError('R11 fixed reader rejects an unrelated R18 system proof')
        return verify_system_reader_signature(prepared['system_reader_signature'])
    if profile!=R18_SYSTEM_READER_PROFILE or 'system_reader_signature' in prepared:
        raise RuntimeError('R18 finite current system-reader proof profile differs')
    binding=prepared.get('current_system_reader_proof')
    if not isinstance(binding,dict):raise RuntimeError('R12 product reader requires the explicit R18 proof binding')
    return verify_current_system_reader_binding(binding, expected_root=root)

def run_system_reader(launch,index,label,actual):
    row=next(r for r in launch.plan['inventory'] if r['label']=='uninstall-process-guard')
    system_reader_lane(row,actual);signature=verify_prepared_system_reader(launch.prepared,launch.root);owner=launch.owner;h=_helpers();owner.remaining(15);owner.sample()
    record=h.publish(launch.root/'logs'/('STAGE_'+str(index)+'.json'),{'argv':actual,'cwd':str(launch.root/'outside'),'environment':{'PATH':'/usr/bin:/bin','LC_ALL':'C'},'nonce':launch.binding['nonce'],'parent_pid':os.getpid(),'lane':'SYSTEM_READER'})
    go=launch.root/'logs'/('GO_'+str(index)+'.json');wrapper=[launch.plan['administrative_python']['path'],'-I','-S','-B',str(Path(__file__).with_name('w26_hold_exec.py')),'--stage',record['path'],'--sha',record['sha256'],'--go',str(go)];owner.expected=wrapper
    def birth(process,args):
        observed=actor.original.actual_birth(process,args)
        if observed.get('status')=='OBSERVED':h.publish(go,{'pid':process.pid,'nonce':launch.binding['nonce'],'stage_sha':record['sha256']})
        return observed
    result=actor.original.run(launch.logsfd,label,wrapper,launch.root,environment(launch.root),timeout=owner.remaining(15),birth_reader=birth)
    signature_input=(launch.prepared.get('current_system_reader_proof') if launch.prepared.get('system_reader_profile') else launch.prepared['system_reader_signature'])
    h.publish(launch.root/'evidence'/('SYSTEM_READER_'+str(index)+'.json'),{'network_proof':'NETWORK_NOT_APPLICABLE_READONLY_SYSTEM_INVENTORY','system_source':signature['system_ps'],'signature_input':signature_input,'system_reader_profile':launch.prepared.get('system_reader_profile','legacy-fixed-r11'),'argv':actual,'cap':15,'original_result':result['result_name'],'original_pid':result['pid'],'same_original_birth_go_exec':True,'no_product_wheel_code':True})
    return result

def environment(root):
    value=deps.dependency_environment(root);value['COMSOL_SERVER_MCP_HOME']=str(Path(root)/'import-home');value['PIP_NO_CACHE_DIR']='1';return value

def literal_code(name):
    tree=ast.parse(Path(release.__file__).read_bytes());function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name)
    if name=='_installed_version':return next(n.value for n in ast.walk(function) if isinstance(n,ast.Constant) and isinstance(n.value,str) and n.value.startswith('import importlib.metadata as m; print('))
    return next(n.value.value for n in function.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='code' for t in n.targets))

def inventory(root,python,descriptor,old_input,new_input):
    root=Path(root);transition=root/'transition-work';py=str(transition/'transition-venv/bin/python');current=str(root/'current-venv/bin/python');profile=str(transition/'transition-evidence/network-deny-transition.sb');rows=[]
    def add(label,argv,cap=180,max_calls=1,cwd=None,offline=False):rows.append({'label':label,'request_argv':argv,'cap':cap,'max_calls':max_calls,'cwd':cwd,'offline_listener_object':offline})
    marker="import json; from packaging.markers import default_environment; print(json.dumps(default_environment()))"
    add('marker-environment',[python,'-I','-B','-c',marker],cwd=str(Path(python).parent.parent))
    capture=lifecycle.CAPTURE_SCRIPT.replace("for rel in json.loads(sys.argv[1]):","for rel in json.loads(sys.argv[1]):\n if rel.split('.dist-info/')[0] != 'comsol_mcp-'+d.version: continue")
    metadata_paths=sorted({r['path'] for item in (old_input,new_input) for r in item['members'] if r['path'].endswith('.dist-info/METADATA')})
    add('installed-identity-capture',['/usr/bin/sandbox-exec','-f',profile,py,'-I','-B','-c',capture,json.dumps(metadata_paths)],max_calls=4,cwd=str(transition/'transition-venv'))
    add('installed-pip-check',['/usr/bin/sandbox-exec','-f',profile,py,'-I','-B','-c',"import subprocess,sys; raise SystemExit(subprocess.call([sys.executable,'-I','-m','pip','check']))"],max_calls=3,cwd=str(transition/'transition-venv'))
    for phase in ['old-read','candidate-backup','restore','old-restored-read']:
        add('data-'+phase,['/usr/bin/sandbox-exec','-f',profile,py,'-I','-B','-c',lifecycle.SEALED_PHASE_SCRIPT,str(Path(data.__file__)),descriptor['path'],descriptor['sha256'],phase],cap=600 if phase in ['candidate-backup','restore'] else 180,cwd=str(transition/'transition-venv'))
    for label,folder,lock in [('candidate-package-install','new-wheelhouse','candidate-requirements.lock'),('old-package-rollback','old-wheelhouse','old-requirements.lock')]:
        add(label,['/usr/bin/sandbox-exec','-f',profile,py,'-m','pip','install','--no-index','--only-binary=:all:','--disable-pip-version-check','--no-input','--force-reinstall','--find-links',str(root/folder),'--require-hashes','--requirement',str(transition/'transition-evidence/inputs'/lock)],cap=600)
    for name in ['package_fingerprint','distribution_fingerprint']:
        add('current-'+name,[current,'-I','-c',literal_code(name)],cwd=str(root/'current-venv'))
    add('current-caller-desktop-metadata-status',[current,'-I','-B','-c',DESKTOP_STATUS_CODE],cwd=str(root/'current-venv'))
    add('uninstall-process-guard',['ps','-axo','pid=,ppid=,command='],cap=15,max_calls=2);rows[-1]['lane']='SYSTEM_READER'
    # Two live original listener objects mint the only dynamic port; JSON cannot.
    for label,venv,evidence,lock,house in [('current-offline-install',root/'current-venv',root/'current-install-evidence',root/'requirements-new.lock',root/'new-wheelhouse'),('old-offline-install',transition/'transition-venv',transition/'transition-evidence',transition/'transition-evidence/inputs/old-requirements.lock',root/'old-wheelhouse')]:
        add(label,['/usr/bin/sandbox-exec','-f',str(evidence/'network-deny.sb'),python,str(Path(release.__file__)),'_offline_child','--python',python,'--requirements',str(lock),'--wheelhouse',str(house),'--venv',str(venv),'--port','OWNED_ORIGINAL_LISTENER_PORT'],cap=600,offline=True)
    return rows

def verify_inputs(root,prepared):
    h=_helpers();root=Path(root);verify_prepared_system_reader(prepared,root);deps.verify_inputs(root,prepared['manifest'],prepared['runtime']);app.verify_prior_profiles(prepared['profiles']);closure=data.verify_seal(SEAL,SEAL_SHA)
    if prepared['seal']['path']!=SEAL or prepared['seal']['sha256']!=SEAL_SHA:raise RuntimeError('lifecycle exact source seal differs')
    for key in ['app_result','app_wheel','old_wheel','python','descriptor', 'requirements_old','requirements_new']:
        h.raw_file(Path(prepared[key]['path']),expected=prepared[key],maximum=268435456)
    current_profile=prepared.get('current_application_profile');current=data.current_application_profile(current_profile) if current_profile is not None else None
    if current is not None and (prepared['app_result']!=current['result'] or prepared['app_wheel']!=current['wheel']):raise RuntimeError('current installed producer first records differ')
    expected_new=NEW_SHA if current is None else current['wheel']['sha256']
    if prepared['app_wheel']['sha256']!=expected_new or prepared['old_wheel']['sha256']!=OLD_SHA:raise RuntimeError('closed genuine old/current wheel identities differ')
    if release.wheel_metadata(Path(prepared['app_wheel']['path']))['version']!='0.1.10' or release.wheel_metadata(Path(prepared['old_wheel']['path']))['version']!='0.1.9':raise RuntimeError('closed old/current versions differ')
    for role in ['old','new']:
        expected=prepared['wheelhouses'][role];actual={p.name for p in (root/(role+'-wheelhouse')).iterdir()}
        if actual!=set(expected):raise RuntimeError('closed lifecycle wheelhouse dirty member')
        for row in expected.values():h.raw_file(Path(row['path']),expected=row)
    descriptor,_=data.descriptor(prepared['descriptor']['path'],prepared['descriptor']['sha256'])
    if descriptor.get('current_application_profile')!=current_profile:raise RuntimeError('current installed data/parent profile differs')
    return closure

def prepare(root,*,tag='life1',current_application_root=None):
    h=_helpers();root=Path(root)
    if root.parent!=Path('/private/tmp') or root!=root.resolve() or not root.name.startswith('cvw26-') or not re.fullmatch('[a-z0-9-]{1,48}',tag):raise RuntimeError('fresh lifecycle namespace refused')
    first=json.loads(h.raw_file(root/'FIRST_RECORD.json')[0]);h.secure_dir(root,first['root_identity']);closure=data.verify_seal(SEAL,SEAL_SHA)
    runtime=h.raw_file(root/'CURRENT_RUNTIME_INPUT.json')[1];manifest=h.raw_file(root/'INPUTS_MANIFEST.json')[1];deps.verify_inputs(root,manifest,runtime)
    profiles={role:h.raw_file(DEP_ROOT/'evidence'/('PROFILE_'+role+'.json'))[1] for role in ['builder','runtime']};app.verify_prior_profiles(profiles)
    for name in ['old-wheelhouse','new-wheelhouse','transition-work','current-venv','current-install-evidence','uninstall-evidence','logs','tmp','evidence','outside','empty-home','import-home']:
        if (root/name).exists() or (root/name).is_symlink():raise RuntimeError('lifecycle fresh effect namespace occupied')
    current_profile=None;current_loader_sources=None
    if current_application_root is None:
        app_result=h.raw_file(APP_ROOT/'APPLICATION_BUNDLE_RESULT.json')[1];new_app=h.raw_file(APP_ROOT/'appwheel/comsol_mcp-0.1.10-py3-none-any.whl')[1]
        if new_app['sha256']!=NEW_SHA:raise RuntimeError('exact actual current app wheel differs')
    else:
        producer_root=Path(current_application_root)
        current_loader_sources=actor.source_fingerprints(actor.APP_LIFECYCLE)
        for path in [Path(data.__file__).with_name('w26_target_inputs.py'),data.CURRENT_APPLICATION_LOCK,data.CURRENT_APPLICATION_R5_LOCK,data.CURRENT_APPLICATION_R6_LOCK]:
            row=h.raw_file(path)[1];current_loader_sources[row['path']]=row
        current_profile={'schema':'w26-current-installed-application/1','source_profile':'current175-r6','root':str(producer_root),**{key:h.raw_file(producer_root/name)[1] for key,name in [('result','APPLICATION_BUNDLE_RESULT.json'),('candidate','ACTOR_CANDIDATE_app6c.json'),('once','UNIT_ONCE_GUARD.json')]},'loader_source_closure':data.current_application_loader_closure(current_loader_sources)}
        current=data.current_application_profile(current_profile);app_result=current['result'];new_app=current['wheel']
    old_app=data.raw_file(closure['genuine_prior_wheel']['copy']['path'],closure['genuine_prior_wheel']['copy'])[0]
    wheelhouses={}
    runtime_pins=release.parse_hash_requirements(root/'requirements-runtime.lock','macos_arm64')
    for role,appblob,appname in [('old',old_app,'comsol_mcp-0.1.9-py3-none-any.whl'),('new',h.raw_file(Path(new_app['path']),expected=new_app)[0],'comsol_mcp-0.1.10-py3-none-any.whl')]:
        folder=root/(role+'-wheelhouse');folder.mkdir(mode=0o700,exist_ok=False)
        for p in sorted((root/'wheelhouse').glob('*.whl')):
            if release.wheel_metadata(p)['normalized_name'] in runtime_pins:h.copy_blob(folder/p.name,h.raw_file(p)[0])
        h.copy_blob(folder/appname,appblob);release.compose_install_lock(root/'requirements-runtime.lock',folder/appname,'macos_arm64',root/('requirements-'+role+'.lock'));wheelhouses[role]={p.name:h.raw_file(p)[1] for p in sorted(folder.iterdir())}
        if len(wheelhouses[role])!=42:raise RuntimeError('whole lifecycle42 input closure differs')
    descriptor=h.publish(root/('EXTERNAL_DATA_'+tag+'.json'),{'schema':'w26-sealed-r6-lifecycle-descriptor/1','unit_root':str(root),'seal':SEAL,'seal_sha256':SEAL_SHA,'synthetic_data':False,'old_historical_dependencies':'UNVERIFIED_CURRENT43_NEW_TEST_BASELINE',**({'current_application_profile':current_profile} if current_profile is not None else {})})
    python=h.raw_file(DEP_ROOT/'runtime/bin/python',maximum=268435456)[1];marker_model=default_environment()
    old_input=lifecycle.wheel_input(release,root/'requirements-old.lock',root/'old-wheelhouse',OLD_SHA,marker_model);new_input=lifecycle.wheel_input(release,root/'requirements-new.lock',root/'new-wheelhouse',new_app['sha256'],marker_model)
    prepared_value={**({'current_application_profile':current_profile} if current_profile is not None else {}),'system_reader_signature':h.raw_file(SYSTEM_PS_SIGNATURE)[1],'manifest':manifest,'runtime':runtime,'seal':h.raw_file(Path(SEAL))[1],'profiles':profiles,'app_result':app_result,'app_wheel':new_app,'old_wheel':h.raw_file(root/'old-wheelhouse/comsol_mcp-0.1.9-py3-none-any.whl')[1],'python':python,'descriptor':descriptor,'requirements_old':h.raw_file(root/'requirements-old.lock')[1],'requirements_new':h.raw_file(root/'requirements-new.lock')[1],'wheelhouses':wheelhouses,'old_input':old_input,'new_input':new_input,'marker_model_NOT_actual_evidence':marker_model,'legacy_sources':{name:h.raw_file(COMMON.parent/file)[1] for name,file in [('common','common.py'),('consumers','consumers.py'),('context','CONTEXT.json'),('config','MPh.ini')]}}
    prepared=h.publish(root/('LIFECYCLE_INPUT_'+tag+'.json'),prepared_value)
    plan=h.publish(root/('CURRENT_BUILD_PLAN_'+tag+'.json'),{**actor.activity_scope({'activity_kind':actor.APP_LIFECYCLE}),'inventory':inventory(root,python['path'],descriptor,old_input,new_input),'administrative_python':{'path':str(Path(sys.executable).resolve()),'record':h.raw_file(Path(sys.executable).resolve(),maximum=268435456)[1],'product_runtime':False},'native_process_umask':'0077','workflow':'existing offline_install/doctor/transition_check with externalsealeddata/uninstall only'})
    sources=current_loader_sources or actor.source_fingerprints(actor.APP_LIFECYCLE)
    if current_profile is not None:
        if sources!=current_loader_sources:raise RuntimeError('current lifecycle loader/source candidate closure drifted')
    for row in prepared_value['legacy_sources'].values():sources[row['path']]=row
    source_record=h.publish(root/('CURRENT_ACTOR_SOURCE_'+tag+'.json'),sources);tools=h.publish(root/('LIFECYCLE_SYSTEM_'+tag+'.json'),{'sandbox':actor.system_record('/usr/bin/sandbox-exec'),'ps':actor.system_record('/bin/ps')})
    return h.publish(root/('ACTOR_CANDIDATE_'+tag+'.json'),{'status':'READY_APPLICATION_LIFECYCLE_MAIN_REVIEW_NOT_NATIVE_APPROVED',**actor.activity_scope({'activity_kind':actor.APP_LIFECYCLE}),'root_identity':first['root_identity'],'prepared':prepared,'toolchain':tools,'plan':plan,'source':source_record,'native_Popen':0})

class LifecycleLaunch:
    def __init__(self,root,owner,candidate,binding,plan,prepared,logsfd,port):
        self.root=Path(root);self.owner=owner;self.candidate=candidate;self.binding=binding;self.plan=plan;self.prepared=prepared;self.logsfd=logsfd;self.port=port;self.completed=[];self.counts={};self.offline=None;self.pending_unknown=None;self.protected_captures={}
    def before_build_wheel(self,*args):raise RuntimeError('lifecycle scope excludes application build')
    def before_offline_install(self,args,script,listener,profile):
        if self.pending_unknown is not None:raise self.pending_unknown
        if script!=Path(release.__file__) or type(listener) is not socket.socket or listener.fileno()<0 or listener.getsockname()[0]!='127.0.0.1':raise RuntimeError('offline boundary requires original owned listening object')
        matches=[r for r in self.plan['inventory'] if r['offline_listener_object'] and r['request_argv'][3]==str(args.python) and r['request_argv'][4]==str(script) and r['request_argv'][9]==str(args.requirements) and r['request_argv'][11]==str(args.wheelhouse) and r['request_argv'][13]==str(args.venv) and r['request_argv'][2]==str(profile)]
        if len(matches)!=1:raise RuntimeError('offline exact admitted paths differ before child')
        port=listener.getsockname()[1]
        with socket.create_connection(('127.0.0.1',port),timeout=1):positive,_=listener.accept();positive.close()
        self.offline=(listener,matches[0],port)
    def admit(self,command,kwargs):
        if self.pending_unknown is not None:raise self.pending_unknown
        selected=[]
        for row in self.plan['inventory']:
            argv=list(row['request_argv'])
            if row['offline_listener_object']:
                if self.offline is None or self.offline[1] is not row:continue
                listener,_,port=self.offline
                if listener.fileno()<0 or listener.getsockname()!=('127.0.0.1',port):raise RuntimeError('original offline listener lost before Popen')
                argv[-1]=str(port)
            allowed={'capture_output':True,'text':True}
            if row['label']=='uninstall-process-guard':allowed['timeout']=15
            elif not row['offline_listener_object']:allowed['cwd']=row['cwd']
            if argv==command and kwargs==allowed:selected.append(row)
        if len(selected)!=1:raise RuntimeError('lifecycle command/kwargs is outside exact closed inventory')
        row=selected[0];used=self.counts.get(row['label'],0)
        if used>=row['max_calls']:raise RuntimeError('lifecycle capture count exhausted; no replay')
        return row
    def capture(self,command,**kwargs):
        row=self.admit(command,kwargs);self.owner.remaining(row['cap']);verify_inputs(self.root,self.prepared);actor.verify_actor(self.candidate);h=_helpers()
        actual=list(command)
        if actual[0]=='/usr/bin/sandbox-exec':actual=actual[3:]
        if actual[0]=='ps':actual[0]='/bin/ps'
        else:
            if '-I' not in actual[1:3]:actual.insert(1,'-I')
            if '-B' not in actual[1:4]:actual.insert(actual.index('-I')+1,'-B')
            if 'pip' in actual and 'install' in actual and '--no-cache-dir' not in actual:actual.insert(actual.index('--find-links'),'--no-cache-dir')
        index=len(self.completed);label=row['label']+'-'+str(index)
        context=h.publish(self.root/'logs'/('EXEC_PLAN_'+str(index)+'.json'),{'activity_kind':actor.APP_LIFECYCLE,'unit_root':str(self.root),'root_identity':self.candidate['root_identity'],'outside':str(self.root/'outside'),'environment':environment(self.root),'legacy_sources':self.prepared['legacy_sources'],'commands':[{'label':label,'argv':actual}]})
        runtime=json.loads(h.raw_file(Path(self.prepared['runtime']['path']),expected=self.prepared['runtime'])[0]);argv=[runtime['base_python_record']['path'],'-I','-S','-B',str(Path(__file__).with_name('w26_lifecycle_stage.py')),'--context',context['path'],'--sha',context['sha256'],'--index','0','--port',str(self.port)]
        try:
            if row.get('lane')=='SYSTEM_READER':result=run_system_reader(self,index,label,actual)
            else:result=actor.run_owned_stage(self.owner,self.logsfd,index,label,argv,self.root/'outside',environment(self.root),row['cap'],self.binding,self.plan,sandbox_path=self.root/'LIFECYCLE_SANDBOX.sb')
        except actor.original.StageUnknown as exc:
            self.pending_unknown=exc;self.owner.observe_only=True;self.owner.stop_reason=self.owner.stop_reason or 'UNKNOWN_RETAIN_ORIGINAL_OWNER_NO_NEXT_STAGE'
            if self.owner.first_cause is None:self.owner.first_cause=exc.original_cause if exc.original_cause is not None else exc
            raise
        except actor.original.TerminalCommandFailure as exc:
            result=exc.result
            if result['owned_group_absent'] is not True or result['owned_process_terminal'] is not True or type(result['rc']) is not int:raise
            if self.owner.first_cause is None:self.owner.first_cause=exc
        if row['label'] in {'data-candidate-backup','data-restore'} and result['rc']==0:
            if self.owner.process is None:raise RuntimeError('original data producer Popen unavailable')
            self.protected_captures[row['label']]={'process':self.owner.process,'result':result,'request':row,
                'stdout':h.raw_file(self.root/'logs'/result['stdout'],maximum=16777216)[1],
                'result_record':h.raw_file(self.root/'logs'/(result['stdout'].removesuffix('.stdout')+'.result.json'))[1],
                'source_record':h.raw_file(Path(self.candidate['source']['path']),expected=self.candidate['source'])[1],
                'once_record':h.raw_file(self.root/'UNIT_ONCE_GUARD.json')[1]}
        self.completed.append(result);self.counts[row['label']]=self.counts.get(row['label'],0)+1
        if row['offline_listener_object']:self.offline=None
        h.publish(self.root/'logs'/('TERMINAL_'+str(index)+'.json'),result)
        return SimpleNamespace(returncode=result['rc'],stdout=h.raw_file(self.root/'logs'/result['stdout'],maximum=16777216)[0].decode(),stderr=h.raw_file(self.root/'logs'/result['stderr'],maximum=16777216)[0].decode())

def _original_protection_capture(launch,label):
    h=_helpers()
    if not isinstance(launch,LifecycleLaunch) or actor._RETAINED.get(str(launch.root)) is not launch.owner or launch.pending_unknown is not None:raise RuntimeError('protection requires this original admitted launch owner')
    actor.verify_actor(launch.candidate)
    observed=launch.protected_captures.get(label)
    if observed is None:raise RuntimeError('original data capture absent')
    result=observed['result'];process=observed['process'];request=observed['request']
    if (not isinstance(process,actor.subprocess.Popen) or process.pid!=result['pid'] or process.returncode!=0
            or not any(r is result for r in launch.completed) or not any(r is request for r in launch.plan['inventory'])
            or request['label']!=label or result['status']!='PASS' or result['rc']!=0
            or result['owned_group_absent'] is not True or result['owned_process_terminal'] is not True
            or result['birth']['status']!='OBSERVED' or result['birth']['pid']!=process.pid):raise RuntimeError('original producer capture status/birth/label differs')
    for key in ['stdout','result_record','source_record','once_record']:
        h.raw_file(Path(observed[key]['path']),expected=observed[key],maximum=16777216)
    if result.get('result_name')!=Path(observed['result_record']['path']).name or json.loads(h.raw_file(Path(observed['result_record']['path']),expected=observed['result_record'])[0])!={k:v for k,v in result.items() if k!='result_name'}:raise RuntimeError('original capture result/envelope changed')
    once=json.loads(h.raw_file(Path(observed['once_record']['path']),expected=observed['once_record'])[0])
    if once['nonce']!=launch.binding['nonce'] or once['activity_kind']!=actor.APP_LIFECYCLE:raise RuntimeError('original capture once differs')
    h.raw_file(Path(once['binding_first_record']['path']),expected=once['binding_first_record'])
    if json.loads(h.raw_file(Path(once['binding_first_record']['path']),expected=once['binding_first_record'])[0])!=launch.binding:raise RuntimeError('original capture binding differs')
    output=json.loads(h.raw_file(Path(observed['stdout']['path']),expected=observed['stdout'],maximum=16777216)[0])
    if output.get('success') is not True or output.get('jpype_started_before_after')!=[False,False]:raise RuntimeError('original data producer output differs')
    return output

class _ProtectionPairs:
    """Parent-owned read-only FDs; original closed child FDs are never revived."""
    def __init__(self,launch):
        self.members={};self.fds=[];self.close_attempted=False;self.close_failure=None
        try:
            backup=_original_protection_capture(launch,'data-candidate-backup');restored=_original_protection_capture(launch,'data-restore')
            paths=data.data_paths({'unit_root':str(launch.root)});evidence=paths['evidence'];archive=backup['actual_archive_binding'];receipt=backup['backup'];scratch=Path(archive['scratch']['path'])
            if (archive['schema']!='w26-observed-original-fullbackup-archive/1' or archive['producer_source_sha256']!=data.backup_producer_sha(launch.prepared)
                    or archive['producer_receipt']!=receipt or receipt['operation']!='full_backup' or receipt['success'] is not True
                    or receipt['archive_path']!=str(evidence/'before-candidate-open.full-state.tar')
                    or scratch.parent!=evidence or not re.fullmatch(r'\.comsol-full-backup-[a-z0-9_]{8}',scratch.name)
                    or archive['scratch']['identity']!=data.signature(scratch.lstat()) or scratch!=scratch.resolve()
                    or scratch.stat().st_mode&0o777!=0o700 or scratch.stat().st_uid!=os.getuid()
                    or archive['after_publish']['path']!=str(scratch/'full-state.tar')
                    or archive['target']['path']!=receipt['archive_path']
                    or archive['target']['sha256']!=receipt['archive_sha256'] or archive['target']['bytes']!=receipt['archive_size_bytes']):raise RuntimeError('original archive read-only protection proof differs')
            self.add_pair(archive['after_publish'],archive['target'],launch.root)
            binding=restored['readback']['restored_database_binding'];restore_receipt=restored['restore']
            if (binding['producer_receipt']!=restore_receipt or restore_receipt['operation']!='full_restore' or restore_receipt['success'] is not True
                    or restore_receipt['worker_or_model_bindings_revived'] is not False or restore_receipt['operation_replay_performed'] is not False
                    or binding['live']['path']!=str(paths['restored_home']/'operations.sqlite3')
                    or binding['recovery_alias']['path']!=str(Path(restore_receipt['recovery_directory'])/'operations.sqlite3')):raise RuntimeError('original restored DB protection proof differs')
            data._restored_record(Path(binding['live']['path']),binding)
            self.add_pair(binding['live'],binding['recovery_alias'],launch.root)
        except BaseException as failure:
            data._close_archive_observation(self,failure);raise
    def add_pair(self,one,two,root):
        if any(one[k]!=two[k] for k in one if k!='path') or one['path']==two['path']:raise RuntimeError('protection pair identity differs')
        for row in (one,two):
            path=Path(row['path'])
            if not path.is_relative_to(root/'transition-work') or path!=path.resolve() or row['mode']&0o777!=0o600 or row['uid']!=os.getuid() or row['nlink']!=2:raise RuntimeError('protection pair closed path/owner differs')
            if str(path) in self.members:raise RuntimeError('protection pair repeats member')
            fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW);self.fds.append(fd)
            _,current=data._archive_fd_record(fd,path,2)
            if current!=row:raise RuntimeError('protection pair original fullstat differs')
            self.members[str(path)]=(fd,row)
    def read(self,path):
        if self.close_attempted:raise RuntimeError('protection original parent FD closed')
        fd,row=self.members[str(path)];_,current=data._archive_fd_record(fd,Path(path),2)
        if current!=row:raise RuntimeError('protection original parent FD/name changed')
        return current
    def close(self):
        if self.close_attempted:
            if self.close_failure is not None:raise self.close_failure
            return
        self.close_attempted=True
        for fd in self.fds:
            try:os.close(fd)
            except BaseException as failure:self.close_failure=failure;raise

def protect(root,proof):
    h=_helpers();root=Path(root);rows={}
    if not isinstance(proof,_ProtectionPairs):raise RuntimeError('JSON cannot mint parent original protection FD proof')
    for p in sorted((root/'transition-work').rglob('*')):
        if p.is_file() and not p.is_symlink() and not p.is_relative_to(root/'transition-work/transition-venv'):
            rows[str(p)]=proof.read(p) if str(p) in proof.members else h.raw_file(p,maximum=268435456)[1]
    for p in sorted((root/'current-install-evidence').rglob('*')):
        if p.is_file():rows[str(p)]=h.raw_file(p)[1]
    if not set(proof.members).issubset(rows):raise RuntimeError('protection pair member disappeared')
    return rows

def execute_lifecycle(root,candidate,binding,binding_record,*,began,admission_token=None):
    if admission_token is not actor._OWNER_TOKEN:raise RuntimeError('lifecycle cannot mint owner from JSON')
    h=_helpers();root=Path(root);plan=json.loads(h.raw_file(Path(candidate['plan']['path']),expected=candidate['plan'])[0]);scope=actor.activity_scope(plan)
    if scope['activity_kind']!=actor.APP_LIFECYCLE:raise RuntimeError('wrong closed lifecycle activity')
    prepared=json.loads(h.raw_file(Path(candidate['prepared']['path']),expected=candidate['prepared'])[0]);owner=actor.RuntimeOwner(root,plan,token=actor._OWNER_TOKEN,began=began);actor._RETAINED[str(root)]=owner;owner.remaining(600)
    verify_inputs(root,prepared);actor.verify_actor(candidate)
    if plan['inventory']!=inventory(root,prepared['python']['path'],prepared['descriptor'],prepared['old_input'],prepared['new_input']):raise RuntimeError('closed lifecycle exact scripts/argv/caps differ')
    tools=json.loads(h.raw_file(Path(candidate['toolchain']['path']),expected=candidate['toolchain'])[0])
    for row in tools.values():
        if actor.system_record(row['path'])!=row:raise RuntimeError('bound lifecycle system reader differs')
    h.raw_file(Path(plan['administrative_python']['path']),expected=plan['administrative_python']['record'])
    if time.monotonic()-began>=600:raise TimeoutError('lifecycle preparation exhausted before effects')
    for name in ['transition-work','current-venv','current-install-evidence','uninstall-evidence','logs','tmp','evidence','outside','empty-home','import-home']:
        if (root/name).exists() or (root/name).is_symlink():raise RuntimeError('lifecycle effect target already exists; no reuse')
    h.publish(root/'UNIT_ONCE_GUARD.json',{'binding_first_record':binding_record,'nonce':binding['nonce'],'activity_kind':actor.APP_LIFECYCLE,'epoch':began,'budget':3600,'cleanup_reserve':600})
    for name in ['logs','tmp','evidence','outside','empty-home','import-home']:(root/name).mkdir(mode=0o700,exist_ok=False)
    sandbox='(version 1)\n(allow default)\n(deny network*)\n(deny file-write*)\n'+''.join('(allow file-write* (subpath '+json.dumps(str(root/name))+'))\n' for name in ['logs','tmp','evidence','import-home','current-venv','current-install-evidence','transition-work','uninstall-evidence'])+'(allow file-write* (literal "/dev/null"))\n';h.copy_blob(root/'LIFECYCLE_SANDBOX.sb',sandbox.encode());logsfd=os.open(root/'logs',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW);old=actor.original.subprocess;launch=None
    actor.original.subprocess=SimpleNamespace(Popen=owner.popen,run=owner.diagnostic_run,TimeoutExpired=actor.subprocess.TimeoutExpired)
    try:
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0));listener.listen(1);listener.settimeout(.25);port=listener.getsockname()[1]
            with socket.create_connection(('127.0.0.1',port),timeout=1):positive,_=listener.accept();positive.close()
            h.publish(root/'evidence/OWNED_LISTENER_POSITIVE.json',{'host':'127.0.0.1','port':port,'actual_owned_parent_listener':True,'positive_connected':True});launch=LifecycleLaunch(root,owner,candidate,binding,plan,prepared,logsfd,port)
            with release._controlled_launch_scope(launch):
                current=release.offline_install(argparse.Namespace(python=Path(prepared['python']['path']),requirements=root/'requirements-new.lock',wheelhouse=root/'new-wheelhouse',venv=root/'current-venv',evidence=root/'current-install-evidence'),Path(release.__file__))
                if current['status']!='PASS_PROCESS_ISOLATED_INSTALL_AND_IMPORT' or current['doctor']['identity']['version']!='0.1.10':raise RuntimeError('actual current installed doctor failed')
                py=root/'current-venv/bin/python';package=release.package_fingerprint(py);distributions=release.distribution_fingerprint(py)
                expected=[{'path':r['path'].removeprefix('comsol_mcp/'),'sha256':r['sha256']} for r in prepared['new_input']['package_members']]
                if package['files']!=expected:raise RuntimeError('actual current installed complete source-bound package identity differs')
                required={n:r['version'] for n,r in prepared['new_input']['applicable_pins'].items()};actual={r['name']:r['version'] for r in distributions['distributions']}
                if actual!={**required,'pip':'25.0.1'}:raise RuntimeError('actual current installed full distributions differ')
                desktop_record=release.run_record([str(py),'-I','-B','-c',DESKTOP_STATUS_CODE],cwd=root/'current-venv')
                if desktop_record['exit_code']!=0:raise RuntimeError('actual installed caller desktop status read failed')
                desktop=json.loads(desktop_record['stdout'])
                if desktop['jpype_started_before_after']!=[False,False] or desktop['prompt_requested'] is not False:raise RuntimeError('desktop nonprompt/JVM bounds differ')
                args=argparse.Namespace(python=Path(prepared['python']['path']),old_requirements=root/'requirements-old.lock',new_requirements=root/'requirements-new.lock',old_wheelhouse=root/'old-wheelhouse',new_wheelhouse=root/'new-wheelhouse',work_root=root/'transition-work',with_data_lifecycle=True,old_wheel_sha256=OLD_SHA,new_wheel_sha256=prepared['app_wheel']['sha256'],external_sealed_descriptor=prepared['descriptor']['path'],external_sealed_descriptor_sha=prepared['descriptor']['sha256'],synthetic_data=False)
                transition=release.transition_check(args,Path(release.__file__));h.publish(root/'evidence/TRANSITION_RESULT.json',transition)
                if transition['status']!='PASS_DISPOSABLE_CODE_DATA_LIFECYCLE_RELEASE_UNVERIFIED' or transition['version_migration_verified'] is not True:raise RuntimeError('actual complete old/new/rollback/whole4 lifecycle failed')
                proof=_ProtectionPairs(launch);original_failure=None
                try:
                    preserved=protect(root,proof);uninstall=release.uninstall_venv(root/'current-venv',root/'uninstall-evidence',confirm=True)
                    if uninstall['status']!='PASS_UNINSTALLED' or (root/'current-venv').exists():raise RuntimeError('actual marked nonempty venv uninstall failed')
                    if protect(root,proof)!=preserved:raise RuntimeError('whole protected data/evidence changed during uninstall')
                except BaseException as failure:original_failure=failure;raise
                finally:data._close_archive_observation(proof,original_failure)
            try:accepted,_=listener.accept();accepted.close();raise RuntimeError('lifecycle child reached held listener')
            except socket.timeout:pass
        owner.remaining(180);verify_inputs(root,prepared);actor.verify_actor(candidate)
        h.publish(root/'APPLICATION_LIFECYCLE_RESULT.json',{'status':'PASS_CURRENT_APPLICATION_LIFECYCLE_REAL_R6_SCOPED_NOT_RELEASE','current_install':current,'current_package':package,'current_distributions':distributions,'current_caller_desktop_metadata':desktop,'transition':transition,'uninstall':uninstall,'commands':launch.completed,'samples':owner.samples,'protected_before_uninstall':preserved,'legacy_old_environment':'UNVERIFIED_CURRENT43_TEST_BASELINE','synthetic_rows_created':0,'COMSOL_JVM_GUI':'NOT_RUN','physical_Intel_Windows':'NOT_RUN','native_recipes':'NOT_RUN','independentfinalreview':'NOT_RUN'})
        return root/'APPLICATION_LIFECYCLE_RESULT.json'
    except BaseException as exc:
        if launch is not None and launch.pending_unknown is not None:exc=launch.pending_unknown
        actor.retain_first_failure(owner,logsfd,exc,launch.completed if launch else [],stem='LIFECYCLE');raise exc
    finally:actor.original.subprocess=old;os.close(logsfd)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--tag',default='life1');p.add_argument('--current-application-root',type=Path);a=p.parse_args();print(prepare(a.root,tag=a.tag,current_application_root=a.current_application_root))
