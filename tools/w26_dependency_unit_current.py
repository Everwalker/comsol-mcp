"""Current43 logical workflow using the one W26 owner and existing release APIs."""
import argparse,hashlib,json,os,socket,sys,time,re
from pathlib import Path
from types import SimpleNamespace
from tools import w26_runtime_actor_current as actor
from tools import full_project_release as release
from tools import w26_prepare_current_inputs as inputprep
from tools.w26_prepare_current_inputs import _helpers,COMMON,load_closure,validate_wheel,lock_bytes

def dependency_environment(root):
    root=Path(root)
    return {'PATH':'/usr/bin:/bin','HOME':str(root/'empty-home'),'TMPDIR':str(root/'tmp'),'LC_ALL':'C','LANG':'C',
            'PYTHONNOUSERSITE':'1','PYTHONDONTWRITEBYTECODE':'1','PIP_CONFIG_FILE':'/dev/null','PIP_NO_INDEX':'1'}

def _private_task_root(path, label):
    path=Path(path)
    if (not path.is_absolute() or path.parent!=Path('/private/tmp') or path!=path.resolve()
            or not path.name.startswith('cvw26-') or path.is_symlink()):
        raise RuntimeError(label+' must be a canonical direct private cvw26 namespace')
    return path

def _verify_runtime_command_rows(commands, build_root):
    expected_keys={'argv','birth','diagnostic_errors','finished_unix','group','owned_group_absent',
        'owned_process_terminal','parent_pid','pid','rc','requested_new_session','result_name','status',
        'stderr','stdout','uid_at_spawn'}
    birth_keys={'actual_ps_command','birth_observed','invocation_argv','pid','ppid','raw','status','uid'}
    raw_keys={'argv','rc','stderr','stdout'}
    group_keys={'actual_pgid','actual_sid','independent_session_observed','observed_unix','parent_sid','status'}
    labels=[]
    for arch in ('arm64','x86_64'):
        for component in ('openssl','xz','gdbm','tcl','tk'):
            labels.extend((component+'-'+arch+'-configure',component+'-'+arch+'-build',component+'-'+arch+'-install'))
    labels.extend('fat-'+name for name in ('libssl.a','libcrypto.a','liblzma.a','libgdbm.a','libtcl8.6.a','libtk8.6.a'))
    labels.extend(('cpython-configure','cpython-build','cpython-install','runtime-required-profile','runtime-relocation-profile'))
    if not isinstance(commands,list) or len(commands)!=41:
        raise RuntimeError('runtime BUILD_RESULT must contain the exact 41 terminal command rows')
    for index,(row,label) in enumerate(zip(commands,labels,strict=True)):
        if not isinstance(row,dict) or set(row)!=expected_keys:
            raise RuntimeError('runtime command row does not match the existing PASS receipt shape')
        birth=row['birth'];group=row['group']
        if (not isinstance(birth,dict) or set(birth)!=birth_keys
                or not isinstance(group,dict) or set(group)!=group_keys
                or not isinstance(birth.get('raw'),dict) or set(birth['raw'])!=raw_keys):
            raise RuntimeError('runtime command row has an unknown owner/group/PASS receipt envelope')
        if (row['status']!='PASS' or type(row['rc']) is not int or row['rc']!=0
                or row['owned_group_absent'] is not True or row['owned_process_terminal'] is not True
                or row['requested_new_session'] is not True or row['diagnostic_errors']!=[]
                or type(row['pid']) is not int or row['pid']<=0 or type(row['parent_pid']) is not int or row['parent_pid']<=0
                or type(row['uid_at_spawn']) is not int or row['uid_at_spawn']!=birth['uid']
                or birth['status']!='OBSERVED' or birth['pid']!=row['pid']
                or birth['ppid']!=row['parent_pid']
                or birth['invocation_argv']!=row['argv']
                or birth['raw']['rc']!=0
                or group['status']!='OBSERVED' or group['independent_session_observed'] is not True
                or group['actual_pgid']!=row['pid'] or group['actual_sid']!=row['pid']
                or not isinstance(row['result_name'],str)
                or not re.fullmatch(re.escape(label)+r'-[0-9a-f]{32}\.result\.json',row['result_name'])
                or not isinstance(row['stdout'],str) or not row['stdout'].endswith('.stdout')
                or not isinstance(row['stderr'],str) or not row['stderr'].endswith('.stderr')):
            raise RuntimeError('runtime command row is not a completed original-owner PASS receipt')
        argv=row['argv']
        if (not isinstance(argv,list) or '/private/tmp/'+build_root.name not in ' '.join(argv)
                or not any('_hold_exec.py' in part for part in argv)):
            raise RuntimeError('runtime command row leaves the selected owned build root/runner')

def _verify_runtime_profile(profile, *, mode, prefix, executable, build_root):
    modules={'_ssl','_hashlib','ssl','hashlib','zlib','sqlite3','ctypes','venv','_lzma','_gdbm','_tkinter'}
    provider={'INFO':[3,5,0,9,0],'NUMBER':0x30500090,'TEXT':'OpenSSL 3.5.9 29 Sep 2026'}
    if (not isinstance(profile,dict)
            or profile.get('status')!='PASS_ARM_SSL359_LZMA_GDBM_TCL_HEADLESS_REQUIRED_RUNTIME'
            or profile.get('mode')!=mode or profile.get('platform')!='arm64'
            or not isinstance(profile.get('python_version'),str)
            or not profile['python_version'].startswith('3.12.13 ')
            or profile.get('provider')!=provider or profile.get('prefix')!=str(prefix)
            or profile.get('executable')!=str(executable)
            or set(profile.get('required_modules',{}))!=modules
            or profile.get('delivery_acceptance')!='NOT_RUN'
            or profile.get('GUI_acceptance')!='NOT_RUN'
            or profile.get('physical_Intel_acceptance')!='NOT_RUN'
            or profile.get('COMSOL')!='NOT_RUN' or profile.get('dependency43_install')!='NOT_RUN'
            or profile.get('Tk_window_created') is not False):
        raise RuntimeError('runtime profile mode/platform/Python/provider/path/scope differs')
    for name,record in profile['required_modules'].items():
        path=Path(record.get('path','')) if isinstance(record,dict) else Path('.')
        if (not isinstance(record,dict) or not path.is_relative_to(prefix)
                or not path.resolve(strict=True).is_relative_to(prefix.resolve(strict=True))):
            raise RuntimeError('runtime profile required-module producer path leaves its prefix: '+name)
        _helpers().raw_file(path,expected=record,maximum=268435456)

def _verify_runtime_build_result(build_root, expected_sha256):
    if not re.fullmatch(r'[0-9a-f]{64}',expected_sha256):
        raise RuntimeError('runtime BUILD_RESULT expected SHA-256 must be explicit lowercase hex')
    h=_helpers();build_root=_private_task_root(build_root,'runtime build root')
    first_raw,first_record=h.raw_file(build_root/'PREPARE_FIRST_RECORD.json')
    first=json.loads(first_raw)
    if not isinstance(first.get('root_identity'),dict):
        raise RuntimeError('runtime build root lacks its original producer root identity')
    h.secure_dir(build_root,first['root_identity'])
    record_path=build_root/'BUILD_RESULT.json'
    build_raw,build_record=h.raw_file(record_path)
    if build_record['sha256']!=expected_sha256:
        raise RuntimeError('runtime BUILD_RESULT differs from the explicit expected SHA-256')
    built=json.loads(build_raw)
    if (not isinstance(built,dict)
            or set(built)!={'status','commands','runtime_profile','relocation_profile','runtime_fat_bytes',
                            'samples','physical_Intel','full_product_install'}
            or built['status']!='PASS_CURRENT_RUNTIME_BUILD_ARM_PROFILE_FAT_BYTES_SCOPED'
            or built['physical_Intel']!='NOT_RUN' or built['full_product_install']!='NOT_RUN'):
        raise RuntimeError('runtime BUILD_RESULT is not the exact scoped ARM producer result')
    _verify_runtime_command_rows(built['commands'],build_root)
    base_prefix=build_root/'install/cpython'
    relocated_root=build_root/'build/relocation-probe'
    relocated_prefix=relocated_root/'install/cpython'
    h.secure_dir(base_prefix)
    h.secure_dir(relocated_root)
    h.secure_dir(relocated_prefix)
    profile_records=(built['runtime_profile'],built['relocation_profile'])
    expected_profiles=((build_root/'logs/RUNTIME_PROFILE_RESULT.json','build-local',base_prefix),
                       (relocated_root/'logs/RUNTIME_PROFILE_RESULT.json','relocated',relocated_prefix))
    profiles=[]
    for record,(path,mode,prefix) in zip(profile_records,expected_profiles,strict=True):
        if not isinstance(record,dict) or record.get('path')!=str(path):
            raise RuntimeError('runtime profile first-record path differs from the selected producer')
        raw,_=h.raw_file(path,expected=record,maximum=16777216)
        profile=json.loads(raw)
        _verify_runtime_profile(profile,mode=mode,prefix=prefix,
                                executable=prefix/'bin/python3.12',build_root=build_root)
        profiles.append(profile)
    rows=built['runtime_fat_bytes']
    expected_bins={'python3.12','_ssl.cpython-312-darwin.so','_hashlib.cpython-312-darwin.so',
                   '_lzma.cpython-312-darwin.so','_gdbm.cpython-312-darwin.so','_tkinter.cpython-312-darwin.so',
                   '_sqlite3.cpython-312-darwin.so','_ctypes.cpython-312-darwin.so','zlib.cpython-312-darwin.so'}
    if not isinstance(rows,list) or len(rows)!=9 or {Path(r.get('record',{}).get('path','')).name for r in rows}!=expected_bins:
        raise RuntimeError('runtime binary producer first-record membership differs')
    python_rows=[r for r in rows if Path(r['record']['path']).name=='python3.12']
    if len(python_rows)!=1:raise RuntimeError('runtime binary closure has no unique base Python producer record')
    for row in rows:
        if (set(row)!={'RPATH','fat_MachO','physical_Intel','record'} or row['RPATH']!='ABSENT'
                or row['physical_Intel']!='NOT_RUN'
                or set(row['fat_MachO'])!={'arm64','x86_64'}):
            raise RuntimeError('runtime binary first-record scope differs')
        record=row['record'];path=Path(record['path'])
        if not path.is_relative_to(base_prefix) or path.is_symlink():
            raise RuntimeError('runtime binary producer path leaves the selected build prefix')
        h.raw_file(path,expected=record,maximum=268435456)
    base_python_record=python_rows[0]['record']
    if base_python_record['path']!=profiles[0]['executable']:
        raise RuntimeError('runtime base Python executable does not match its native profile')
    return {'base_python_record':base_python_record,'base_prefix':str(base_prefix),
            'actual_profile_first_record':built['runtime_profile'],
            'actual_relocation_profile_first_record':built['relocation_profile'],
            'actual_BUILD_RESULT_first_record':build_record,
            'producer_provenance':{'schema':'w26-runtime-input-producer-provenance/1',
                'build_root':str(build_root),'build_root_identity':first['root_identity'],
                'build_result_sha256':expected_sha256,'producer_status':built['status'],
                'runtime_fat_binary_records':rows}}

def prepare_runtime_input(dep_root, *, build_root, expected_build_result_sha256):
    """Publish one exact runtime input from an independently frozen runtime producer.

    This bridge performs no process launch, once consumption, or runtime-tree
    write. The only effect is one exclusive CURRENT_RUNTIME_INPUT.json in an
    already-prepared, private exact43 dependency namespace.
    """
    h=_helpers();dep_root=_private_task_root(dep_root,'dependency input root')
    build_root=_private_task_root(build_root,'runtime build root')
    if dep_root==build_root:raise RuntimeError('dependency and runtime producer roots must be separate')
    first_raw,_=h.raw_file(dep_root/'FIRST_RECORD.json');first=json.loads(first_raw)
    if first.get('root')!=str(dep_root) or not isinstance(first.get('root_identity'),dict):
        raise RuntimeError('dependency input root first record differs')
    h.secure_dir(dep_root,first['root_identity'])
    wheelhouse=dep_root/'wheelhouse'
    h.secure_dir(wheelhouse)
    closure=load_closure()
    if first.get('closure_sha256')!=inputprep.CLOSURE_SHA:
        raise RuntimeError('dependency input root is not prepared from the fixed exact43 closure')
    manifest_path=dep_root/'INPUTS_MANIFEST.json'
    manifest_record=h.raw_file(manifest_path)[1]
    manifest_raw,_=h.raw_file(manifest_path,expected=manifest_record)
    manifest=json.loads(manifest_raw)
    pins={r['filename']:r for r in closure['actual_inputs']}
    if (manifest.get('all43_present') is not True or len(manifest.get('rows',[]))!=43
            or {r.get('filename') for r in manifest['rows']}!=set(pins)):
        raise RuntimeError('dependency input root is not the exact 43-wheel prepared namespace')
    if {p.name for p in wheelhouse.iterdir()}!=set(pins):
        raise RuntimeError('dependency wheelhouse contains files outside the exact43 input closure')
    for row in manifest['rows']:
        expected=dep_root/'wheelhouse'/row['filename']
        if row['current_record'].get('path')!=str(expected):
            raise RuntimeError('current43 wheel producer record leaves exact dependency root')
        blob,_=h.raw_file(expected,expected=row['current_record']);validate_wheel(blob,pins[row['filename']])
    for role in ('backend','runtime'):
        lock_path=dep_root/('requirements-'+role+'.lock')
        if h.raw_file(lock_path)[0]!=lock_bytes(role):
            raise RuntimeError('current43 fixed '+role+' lock differs')
    output=dep_root/'CURRENT_RUNTIME_INPUT.json'
    if output.exists() or output.is_symlink():
        raise RuntimeError('CURRENT_RUNTIME_INPUT already exists; O_EXCL producer refuses replacement')
    runtime=_verify_runtime_build_result(build_root,expected_build_result_sha256)
    runtime['schema']='w26-current-runtime-input/1'
    runtime['input_producer']='R17_PREPARE_RUNTIME_INPUT_FROM_EXPLICIT_BUILD_RESULT_SHA'
    runtime['dependency_root_identity']=first['root_identity']
    runtime_record=h.publish(output,runtime)
    verify_inputs(dep_root,manifest_record,runtime_record)
    return runtime_record

def verify_inputs(root,manifest_record,runtime_record):
    h=_helpers();root=Path(root);raw,_=h.raw_file(Path(manifest_record['path']),expected=manifest_record)
    manifest=json.loads(raw);closure=load_closure();pins={r['filename']:r for r in closure['actual_inputs']}
    if manifest.get('all43_present') is not True or len(manifest.get('rows',[]))!=43 or {r['filename'] for r in manifest['rows']}!=set(pins):raise RuntimeError('current exact43 input membership differs')
    wheelhouse=root/'wheelhouse';h.secure_dir(wheelhouse)
    if {p.name for p in wheelhouse.iterdir()}!=set(pins):raise RuntimeError('current exact43 wheelhouse membership differs')
    for row in manifest['rows']:
        expected=wheelhouse/row['filename']
        if row['current_record']['path']!=str(expected):raise RuntimeError('current43 wheel path leaves fixed input root')
        blob,_=h.raw_file(expected,expected=row['current_record']);validate_wheel(blob,pins[row['filename']])
    for role in ('backend','runtime'):
        if h.raw_file(root/('requirements-'+role+'.lock'))[0]!=lock_bytes(role):raise RuntimeError('current43 role lock changed')
    raw,_=h.raw_file(Path(runtime_record['path']),expected=runtime_record);runtime=json.loads(raw)
    h.raw_file(Path(runtime['base_python_record']['path']),expected=runtime['base_python_record'],maximum=268435456)
    for key in ('actual_profile_first_record','actual_relocation_profile_first_record','actual_BUILD_RESULT_first_record'):
        h.raw_file(Path(runtime[key]['path']),expected=runtime[key])
    profile=json.loads(h.raw_file(Path(runtime['actual_profile_first_record']['path']),expected=runtime['actual_profile_first_record'])[0])
    if profile['platform']!='arm64' or profile['provider']!={'INFO':[3,5,0,9,0],'NUMBER':0x30500090,'TEXT':'OpenSSL 3.5.9 29 Sep 2026'} or profile['executable']!=runtime['base_python_record']['path'] or profile['prefix']!=runtime['base_prefix']:
        raise RuntimeError('current base actual full native provider/identity differs')
    h.secure_dir(Path(runtime['base_prefix']))
    for record in profile['required_modules'].values():h.raw_file(Path(record['path']),expected=record,maximum=268435456)
    built=json.loads(h.raw_file(Path(runtime['actual_BUILD_RESULT_first_record']['path']),expected=runtime['actual_BUILD_RESULT_first_record'])[0])
    if built['status']!='PASS_CURRENT_RUNTIME_BUILD_ARM_PROFILE_FAT_BYTES_SCOPED':raise RuntimeError('current base actual scoped build proof differs')
    for row in built['runtime_fat_bytes']:h.raw_file(Path(row['record']['path']),expected=row['record'],maximum=268435456)
    return runtime

def dependency_plan(root,runtime,context_record):
    root=Path(root);base=runtime['base_python_record']['path'];stage_file=str(Path(__file__).with_name('w26_dependency_profile.py'))
    commands=[]
    def add(label,request,argv,cap):commands.append({'label':label,'request_argv':request,'argv':argv,'cap':cap})
    for role in ('builder','runtime'):
        request=[base,'-m','venv',str(root/role)]
        add(role+'-create',request,[base,'-I','-B','-m','venv','--copies',str(root/role)],300)
    for role in ('builder','runtime'):
        py=str(root/role/'bin/python');lock=str(root/('requirements-'+('backend' if role=='builder' else 'runtime')+'.lock'))
        request=[py,'-m','pip','install','--no-index','--only-binary=:all:','--disable-pip-version-check','--no-input','--find-links',str(root/'wheelhouse'),'--require-hashes','--requirement',lock]
        argv=[py,'-I','-B',*request[1:]];argv.insert(argv.index('--find-links'),'--no-cache-dir')
        add(role+'-install',request,argv,300)
    for role in ('builder','runtime'):
        argv=[str(root/role/'bin/python'),'-I','-B','-m','pip','check'];add(role+'-check',argv,argv,180)
    for role in ('builder','runtime'):
        argv=[str(root/role/'bin/python'),'-I','-B',stage_file,'--role',role,'--context',context_record['path'],'--sha',context_record['sha256']]
        add(role+'-profile',argv,argv,180)
    return commands

def prepare(root,*,tag='deps1'):
    root=Path(root);h=_helpers()
    if root.parent!=Path('/private/tmp') or root!=root.resolve() or not root.name.startswith('cvw26-') or not re.fullmatch('[a-z0-9-]{1,48}',tag):raise RuntimeError('fresh current43 root/tag refused')
    first=json.loads(h.raw_file(root/'FIRST_RECORD.json')[0]);h.secure_dir(root,first['root_identity'])
    manifest=h.raw_file(root/'INPUTS_MANIFEST.json')[1];runtime_record=h.raw_file(root/'CURRENT_RUNTIME_INPUT.json')[1]
    runtime=verify_inputs(root,manifest,runtime_record)
    for folder in ('builder','runtime','outside','evidence','logs','tmp','empty-home'):
        if (root/folder).exists() or (root/folder).is_symlink():raise RuntimeError('dependency effect target already exists; fresh namespace required')
    legacy={name:h.raw_file(COMMON.parent/filename)[1] for name,filename in [('common','common.py'),('consumers','consumers.py'),('context','CONTEXT.json'),('config','MPh.ini')]}
    original_context=json.loads(h.raw_file(Path(legacy['context']['path']),expected=legacy['context'])[0])
    context={'schema':'w26-current43-source-bound-context/1','unit_root':str(root),'root_identity':first['root_identity'],
             'base_prefix':runtime['base_prefix'],'roles':original_context['roles'],'pip':original_context['pip'],
             'mph_public_defaults':original_context['mph_public_defaults'],'legacy_sources':legacy,'outside':str(root/'outside'),
             'environment':dependency_environment(root)}
    context_record=h.publish(root/('DEPENDENCY_CONTEXT_'+tag+'.json'),context)
    commands=dependency_plan(root,runtime,context_record)
    # Profile context record cannot include its own SHA; exact eight argv live in
    # a separate immutable execution plan consumed by the same-PID precondition.
    plan={**actor.activity_scope({'activity_kind':actor.CURRENT43}),'commands':commands,'context':context_record,
          'administrative_python':{'path':str(Path(sys.executable).resolve()),'record':h.raw_file(Path(sys.executable).resolve(),maximum=268435456)[1],'product_runtime':False},
          'native_process_umask':'0077'}
    prepared_record=h.publish(root/('DEPENDENCY_INPUT_BINDING_'+tag+'.json'),{'manifest':manifest,'runtime':runtime_record,'root_identity':first['root_identity'],'legacy_sources':legacy})
    tools=h.publish(root/('DEPENDENCY_SYSTEM_'+tag+'.json'),{'sandbox':actor.system_record('/usr/bin/sandbox-exec'),'ps':actor.system_record('/bin/ps')})
    source=actor.source_fingerprints(actor.CURRENT43)
    for row in legacy.values():source[row['path']]=row
    for path in (Path(actor.__file__).resolve(strict=True),Path(__file__).resolve(strict=True)):
        row=h.raw_file(path)[1]
        source[row['path']]=row
    source_record=h.publish(root/('CURRENT_ACTOR_SOURCE_'+tag+'.json'),source)
    plan_record=h.publish(root/('CURRENT_BUILD_PLAN_'+tag+'.json'),plan)
    return h.publish(root/('ACTOR_CANDIDATE_'+tag+'.json'),{'status':'READY_CURRENT43_FOR_MAIN_REVIEW_NOT_EXECUTION_APPROVED',
        **actor.activity_scope(plan),'root_identity':first['root_identity'],'prepared':prepared_record,'toolchain':tools,'source':source_record,'plan':plan_record,'native_Popen':0})

class DependencyLaunch:
    def __init__(self,root,owner,candidate,binding,plan,prepared,logsfd,port):
        self.root=Path(root);self.owner=owner;self.candidate=candidate;self.binding=binding;self.plan=plan;self.prepared=prepared;self.logsfd=logsfd;self.port=port;self.index=0;self.completed=[]
    def before_build_wheel(self,*args):
        raise RuntimeError('CURRENT43 excludes application wheel build before any staging effect')
    def capture(self,command,**kwargs):
        if self.index>=8:raise RuntimeError('eight commands exhausted; no extra effect')
        stage=self.plan['commands'][self.index]
        if command!=stage['request_argv'] or kwargs!={'cwd':kwargs.get('cwd'),'capture_output':True,'text':True}:raise RuntimeError('existing release call differs from exact admitted stage')
        if kwargs['cwd'] not in (None,str(self.root/'outside')):raise RuntimeError('release caller cwd not admitted')
        self.owner.remaining(stage['cap']);actor.verify_actor(self.candidate);verify_inputs(self.root,self.prepared['manifest'],self.prepared['runtime'])
        h=_helpers();ctx=json.loads(h.raw_file(Path(self.plan['context']['path']),expected=self.plan['context'])[0])
        exec_plan=h.publish(self.root/'logs'/('EXEC_PLAN_'+str(self.index)+'.json'),{**ctx,'commands':self.plan['commands']})
        argv=[ctx['base_prefix']+'/bin/python3.12','-I','-S','-B',str(Path(__file__).with_name('w26_dependency_stage.py')),
              '--context',exec_plan['path'],'--sha',exec_plan['sha256'],'--index',str(self.index),'--port',str(self.port)]
        result=actor.run_owned_stage(self.owner,self.logsfd,self.index,stage['label'],argv,self.root/'outside',ctx['environment'],stage['cap'],self.binding,self.plan,sandbox_path=self.root/'DEPENDENCY_SANDBOX.sb')
        self.completed.append(result);h.publish(self.root/'logs'/('PASS_'+str(self.index)+'.json'),result)
        stdout=h.raw_file(self.root/'logs'/result['stdout'],maximum=16777216)[0].decode();stderr=h.raw_file(self.root/'logs'/result['stderr'],maximum=16777216)[0].decode()
        self.index+=1
        return SimpleNamespace(returncode=result['rc'],stdout=stdout,stderr=stderr)

def execute_dependencies(root,candidate,binding,binding_record,*,began,admission_token=None):
    if admission_token is not actor._OWNER_TOKEN:raise RuntimeError('current43 cannot mint owner from JSON; original source gate required')
    h=_helpers();root=Path(root);plan=json.loads(h.raw_file(Path(candidate['plan']['path']),expected=candidate['plan'])[0]);scope=actor.activity_scope(plan)
    if scope['activity_kind']!=actor.CURRENT43:raise RuntimeError('wrong dependency activity')
    prepared=json.loads(h.raw_file(Path(candidate['prepared']['path']),expected=candidate['prepared'])[0]);owner=actor.RuntimeOwner(root,plan,token=actor._OWNER_TOKEN,began=began);actor._RETAINED[str(root)]=owner
    owner.remaining(600);runtime=verify_inputs(root,prepared['manifest'],prepared['runtime']);actor.verify_actor(candidate)
    if plan['commands']!=dependency_plan(root,runtime,plan['context']):raise RuntimeError('closed current43 exact eight argv/caps differ')
    ctx=json.loads(h.raw_file(Path(plan['context']['path']),expected=plan['context'])[0])
    original_context=json.loads(h.raw_file(Path(prepared['legacy_sources']['context']['path']),expected=prepared['legacy_sources']['context'])[0])
    if ctx['unit_root']!=str(root) or ctx['outside']!=str(root/'outside') or ctx['environment']!=dependency_environment(root) or ctx['base_prefix']!=runtime['base_prefix'] or ctx['root_identity']!=candidate['root_identity'] or any(ctx[k]!=original_context[k] for k in ('roles','pip','mph_public_defaults')) or ctx['legacy_sources']!=prepared['legacy_sources']:
        raise RuntimeError('current producer exact role/source/base binding differs')
    tools=json.loads(h.raw_file(Path(candidate['toolchain']['path']),expected=candidate['toolchain'])[0])
    for row in tools.values():
        if actor.system_record(row['path'])!=row:raise RuntimeError('dependency bound system tool differs')
    h.raw_file(Path(plan['administrative_python']['path']),expected=plan['administrative_python']['record'])
    if time.monotonic()-began>=600:raise TimeoutError('current43 prepare cap exhausted before first effect')
    h.publish(root/'UNIT_ONCE_GUARD.json',{'binding_first_record':binding_record,'nonce':binding['nonce'],'activity_kind':actor.CURRENT43,'epoch':owner.began,'budget':1800,'cleanup_reserve':300})
    for folder in ('outside','evidence','logs','tmp','empty-home'):(root/folder).mkdir(mode=0o700,exist_ok=False)
    ctx=json.loads(h.raw_file(Path(plan['context']['path']),expected=plan['context'])[0]);h.copy_blob(root/'outside/MPh.ini',h.raw_file(Path(ctx['legacy_sources']['config']['path']),expected=ctx['legacy_sources']['config'])[0])
    profile='(version 1)\n(allow default)\n(deny network*)\n(deny file-write*)\n'+''.join('(allow file-write* (subpath '+json.dumps(str(root/f))+'))\n' for f in ('builder','runtime','tmp','logs','evidence'))+'(allow file-write* (literal "/dev/null"))\n'
    h.copy_blob(root/'DEPENDENCY_SANDBOX.sb',profile.encode());logsfd=os.open(root/'logs',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW);old=actor.original.subprocess
    actor.original.subprocess=SimpleNamespace(Popen=owner.popen,run=owner.diagnostic_run,TimeoutExpired=actor.subprocess.TimeoutExpired)
    launch=None
    try:
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0));listener.listen(1);listener.settimeout(.25);port=listener.getsockname()[1]
            with socket.create_connection(('127.0.0.1',port),timeout=1):positive,_=listener.accept();positive.close()
            h.publish(root/'evidence/OWNED_LISTENER_POSITIVE.json',{'host':'127.0.0.1','port':port,'actual_owned_parent_listener':True,'positive_connected':True})
            launch=DependencyLaunch(root,owner,candidate,binding,plan,prepared,logsfd,port)
            with release._controlled_launch_scope(launch):
                for role in ('builder','runtime'):
                    owner.remaining(300);release.create_venv(Path(runtime['base_python_record']['path']),root/role)
                for role in ('builder','runtime'):
                    owner.remaining(300);release.pip_install_lock(root/role/'bin/python',root/('requirements-'+('backend' if role=='builder' else 'runtime')+'.lock'),root/'wheelhouse')
                for stage in plan['commands'][4:]:release.run_record(stage['request_argv'],cwd=root/'outside')
            try:accepted,_=listener.accept();accepted.close();raise RuntimeError('sandboxed stage reached owned parent listener')
            except socket.timeout:pass
        if launch.index!=8:raise RuntimeError('current eight commands incomplete')
        for role in ('builder','runtime'):
            raw,record=h.raw_file(root/'evidence'/('PROFILE_'+role+'.json'));value=json.loads(raw)
            if value['status']!='PASS_SCOPED_ACTUAL_DEPENDENCY_IMPORTS_NOT_COMSOL_OR_FULL_STDLIB' or value['role']!=role:raise RuntimeError('actual first profile rejected')
            if role=='runtime' and value['jpype_started_before_after']!=[False,False]:raise RuntimeError('JVM unexpectedly started')
        actor.verify_actor(candidate);verify_inputs(root,prepared['manifest'],prepared['runtime'])
        h.publish(root/'DEPENDENCY_RESULT.json',{'status':'PASS_ACTUAL_CURRENT43_ENVIRONMENTS_PROFILES_OFFLINE_SCOPED','commands':launch.completed,
            'samples':owner.samples,'profile_records':{role:h.raw_file(root/'evidence'/('PROFILE_'+role+'.json'))[1] for role in ('builder','runtime')},
            'application_wheel':'NOT_RUN','upgrade_rollback_uninstall_restore':'NOT_RUN','COMSOL_JVM_GUI':'NOT_RUN','physical_Intel_Windows':'NOT_RUN'})
        return root/'DEPENDENCY_RESULT.json'
    except BaseException as exc:
        actor.retain_first_failure(owner,logsfd,exc,launch.completed if launch else [],stem='DEPENDENCY');raise
    finally:actor.original.subprocess=old;os.close(logsfd)

def main(argv=None):
    argv=list(sys.argv[1:] if argv is None else argv)
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,required=True);parser.add_argument('--tag',default='deps1')
    parser.add_argument('--prepare-runtime-input',action='store_true');parser.add_argument('--build-root',type=Path)
    parser.add_argument('--expected-build-result-sha256');args=parser.parse_args(argv)
    if args.prepare_runtime_input:
        if (args.build_root is None or args.expected_build_result_sha256 is None or args.tag!='deps1'
                or any(token=='--tag' or token.startswith('--tag=') for token in argv)):
            parser.error('--prepare-runtime-input requires --build-root and --expected-build-result-sha256 and does not accept --tag')
        result=prepare_runtime_input(args.root,build_root=args.build_root,
            expected_build_result_sha256=args.expected_build_result_sha256)
        print(json.dumps(result,sort_keys=True))
        return result
    if args.build_root is not None or args.expected_build_result_sha256 is not None:
        parser.error('--build-root/--expected-build-result-sha256 require --prepare-runtime-input')
    result=prepare(args.root,tag=args.tag)
    print(json.dumps(result,sort_keys=True))
    return result

if __name__=='__main__':
    main()
