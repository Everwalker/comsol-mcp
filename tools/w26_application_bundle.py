"""Closed current app/bundle activity reusing release APIs and original W26 actor."""
import argparse,ast,contextlib,json,os,re,socket,sys,time
from pathlib import Path
from types import SimpleNamespace
from tools import w26_runtime_actor as actor,full_project_release as release,w26_dependency_unit as deps
from tools.w26_prepare_current_inputs import _helpers,COMMON,load_closure

VERSION='0.1.10';TARGET='macos_arm64'

def source_members(source):
    source=Path(source);members={}
    for p in sorted((source/'comsol_mcp').rglob('*')):
        if p.is_symlink():raise RuntimeError('package source link refused')
        rel=p.relative_to(source)
        if any(x in {'__pycache__','.pytest_cache'} or x.startswith('._') for x in rel.parts) or p.suffix in {'.pyc','.pyo'}:continue
        if p.is_file():members[rel.as_posix()]=p
        elif not p.is_dir():raise RuntimeError('package source special member refused')
    for name in release.SOURCE_ROOT_FILES:members[name]=source/name
    return members

def verify_version(source):
    import tomllib
    source=Path(source);h=_helpers();project=tomllib.loads(h.raw_file(source/'pyproject.toml')[0].decode())
    server=ast.parse(h.raw_file(source/'comsol_mcp/_server.py')[0]);versions=[]
    for node in server.body:
        if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='VERSION' for t in node.targets):versions.append(ast.literal_eval(node.value))
    if project['project']['version']!=VERSION or versions!=[VERSION]:raise RuntimeError('application declared/package version not exact current0.1.10')

def source_capture(source,destination):
    h=_helpers();source=Path(source).resolve(strict=True);destination=Path(destination);members=source_members(source);verify_version(source)
    if len(members)!=156:raise RuntimeError('current Main package153 plus3 roots membership differs')
    rows={}
    for rel,p in members.items():rows[rel]=h.raw_file(p)[1]
    destination.mkdir(mode=0o700,exist_ok=False)
    for rel,row in rows.items():
        target=destination/rel;target.parent.mkdir(parents=True,exist_ok=True,mode=0o700);raw,_=h.raw_file(Path(row['path']),expected=row);h.copy_blob(target,raw)
    if set(source_members(source))!=set(rows):raise RuntimeError('original source membership changed during capture')
    for row in rows.values():h.raw_file(Path(row['path']),expected=row)
    verify_version(destination)
    return {'original_source':str(source),'original_members':rows,'snapshot_source':str(destination),'snapshot_members':{rel:h.raw_file(destination/rel)[1] for rel in rows}}

def verify_source_input(source):
    h=_helpers();root=Path(source['snapshot_source']);h.secure_dir(root);verify_version(root)
    if set(source_members(root))!=set(source['snapshot_members']) or len(source['snapshot_members'])!=156:raise RuntimeError('immutable app source membership drift')
    for rel,row in source['snapshot_members'].items():
        if row['path']!=str(root/rel):raise RuntimeError('app source record leaves snapshot')
        h.raw_file(root/rel,expected=row)
    original=Path(source['original_source'])
    if set(source_members(original))!=set(source['original_members']):raise RuntimeError('original Main source membership drift')
    for row in source['original_members'].values():h.raw_file(Path(row['path']),expected=row)

def verify_prior_profiles(records):
    h=_helpers()
    def walk(value):
        if isinstance(value,dict):
            if {'path','bytes','sha256','dev','inode','uid','mode','nlink'}<=set(value):h.raw_file(Path(value['path']),expected=value,maximum=268435456)
            else:
                for x in value.values():walk(x)
        elif isinstance(value,list):
            for x in value:walk(x)
    for role,record in records.items():
        raw,_=h.raw_file(Path(record['path']),expected=record);v=json.loads(raw)
        if v['role']!=role or v['status']!='PASS_SCOPED_ACTUAL_DEPENDENCY_IMPORTS_NOT_COMSOL_OR_FULL_STDLIB' or v['provider']!={'INFO':[3,5,0,9,0],'NUMBER':0x30500090,'TEXT':'OpenSSL 3.5.9 29 Sep 2026'}:raise RuntimeError('actual prior current dependency profile differs')
        if role=='builder' and v['distributions']!={'pip':'25.0.1','packaging':'26.3','setuptools':'80.9.0','wheel':'0.48.0'}:raise RuntimeError('actual builder complete distribution set differs')
        if role=='runtime' and v['jpype_started_before_after']!=[False,False]:raise RuntimeError('prior dependency JVM state differs')
        walk(v)

def commands(root,python):
    root=Path(root);stage=str(root/'tmp/build-source/source')
    requests=[[python,'-c','import setuptools, wheel; print(setuptools.__version__, wheel.__version__)'],[python,'-m','pip','wheel','--no-deps','--no-build-isolation','--disable-pip-version-check','--wheel-dir',str(root/'appwheel'),stage]]
    actual=[[python,'-I','-B',*requests[0][1:]],[python,'-I','-B','-m','pip','wheel','--no-index','--no-cache-dir',*requests[1][4:]]]
    return [{'label':label,'request_argv':req,'argv':argv,'cap':cap} for label,req,argv,cap in zip(['builder-check','application-build'],requests,actual,[180,600])]

def prepare(root,prior_root,source,*,tag='app1'):
    h=_helpers();root=Path(root);prior_root=Path(prior_root);source=Path(source).resolve(strict=True)
    if root.parent!=Path('/private/tmp') or root!=root.resolve() or not root.name.startswith('cvw26-') or not re.fullmatch('[a-z0-9-]{1,48}',tag):raise RuntimeError('fresh closed app root/tag refused')
    first=json.loads(h.raw_file(root/'FIRST_RECORD.json')[0]);h.secure_dir(root,first['root_identity'])
    manifest=h.raw_file(root/'INPUTS_MANIFEST.json')[1];runtime_record=h.raw_file(root/'CURRENT_RUNTIME_INPUT.json')[1];runtime=deps.verify_inputs(root,manifest,runtime_record)
    profiles={role:h.raw_file(prior_root/'evidence'/('PROFILE_'+role+'.json'))[1] for role in ['builder','runtime']};verify_prior_profiles(profiles)
    prior=h.raw_file(prior_root/'DEPENDENCY_RESULT.json')[1];priorvalue=json.loads(h.raw_file(Path(prior['path']),expected=prior)[0])
    if priorvalue['status']!='PASS_ACTUAL_CURRENT43_ENVIRONMENTS_PROFILES_OFFLINE_SCOPED' or priorvalue['profile_records']!=profiles:raise RuntimeError('original current43 producer-profile closure differs')
    python=prior_root/'builder/bin/python';builder=h.raw_file(python,maximum=268435456)[1]
    for name in ['source-input','tmp','logs','evidence','outside','empty-home','appwheel','bundlewheelhouse','metadata','bundle']:
        if (root/name).exists() or (root/name).is_symlink():raise RuntimeError('fresh app effect path already exists')
    capture=source_capture(source,root/'source-input')
    extras={name:h.raw_file(Path(source)/path)[1] for name,path in {'uv_lock':'uv.lock','operations':'docs/full_project_execution/release/RELEASE_OPERATIONS.md'}.items()}
    for name,row in extras.items():h.copy_blob(root/('INPUT_'+name),h.raw_file(Path(row['path']),expected=row)[0])
    prepared=h.publish(root/('APP_INPUT_BINDING_'+tag+'.json'),{'manifest':manifest,'runtime':runtime_record,'root_identity':first['root_identity'],'prior_result':prior,'profiles':profiles,'builder':builder,'source':capture,'extras':extras,'copied_extras':{name:h.raw_file(root/('INPUT_'+name))[1] for name in extras},'legacy_sources':{name:h.raw_file(COMMON.parent/file)[1] for name,file in [('common','common.py'),('consumers','consumers.py'),('context','CONTEXT.json'),('config','MPh.ini')]}})
    environment=deps.dependency_environment(root)
    context=h.publish(root/('APP_CONTEXT_'+tag+'.json'),{'activity_kind':actor.APP_BUNDLE,'unit_root':str(root),'root_identity':first['root_identity'],'outside':str(root/'outside'),'environment':environment,'legacy_sources':json.loads(h.raw_file(Path(prepared['path']),expected=prepared)[0])['legacy_sources'],'commands':commands(root,str(python))})
    plan=h.publish(root/('CURRENT_BUILD_PLAN_'+tag+'.json'),{**actor.activity_scope({'activity_kind':actor.APP_BUNDLE}),'context':context,'commands':commands(root,str(python)),'version':VERSION,'target':TARGET,'administrative_python':{'path':str(Path(sys.executable).resolve()),'record':h.raw_file(Path(sys.executable).resolve(),maximum=268435456)[1],'product_runtime':False},'native_process_umask':'0077'})
    sources=actor.source_fingerprints(actor.APP_BUNDLE)
    preparedv=json.loads(h.raw_file(Path(prepared['path']),expected=prepared)[0])
    for row in preparedv['legacy_sources'].values():sources[row['path']]=row
    source_record=h.publish(root/('CURRENT_ACTOR_SOURCE_'+tag+'.json'),sources)
    system=h.publish(root/('APP_SYSTEM_'+tag+'.json'),{'sandbox':actor.system_record('/usr/bin/sandbox-exec'),'ps':actor.system_record('/bin/ps')})
    return h.publish(root/('ACTOR_CANDIDATE_'+tag+'.json'),{'status':'READY_APPLICATION_BUNDLE_MAIN_REVIEW_NOT_EXECUTION_APPROVED',**actor.activity_scope({'activity_kind':actor.APP_BUNDLE}),'root_identity':first['root_identity'],'prepared':prepared,'toolchain':system,'plan':plan,'source':source_record,'native_Popen':0})

class ApplicationLaunch:
    def __init__(self,root,owner,candidate,binding,plan,prepared,logsfd,port):self.root=Path(root);self.owner=owner;self.candidate=candidate;self.binding=binding;self.plan=plan;self.prepared=prepared;self.logsfd=logsfd;self.port=port;self.index=0;self.completed=[]
    def verify_inputs(self):
        p=self.prepared;deps.verify_inputs(self.root,p['manifest'],p['runtime']);verify_prior_profiles(p['profiles']);verify_source_input(p['source']);h=_helpers();h.raw_file(Path(p['builder']['path']),expected=p['builder'],maximum=268435456)
        for row in [p['prior_result'],*p['extras'].values(),*p['copied_extras'].values()]:h.raw_file(Path(row['path']),expected=row)
    def before_build_wheel(self,source,out,python,evidence):
        if self.index!=0 or [str(source),str(out),str(python),str(evidence)]!=[self.prepared['source']['snapshot_source'],str(self.root/'appwheel'),self.prepared['builder']['path'],str(self.root/'evidence')]:raise RuntimeError('app exact build API paths differ before effects')
        self.owner.remaining(600);self.verify_inputs();actor.verify_actor(self.candidate)
    @contextlib.contextmanager
    def temporary_source_directory(self):
        self.owner.remaining(600);p=self.root/'tmp/build-source';p.mkdir(mode=0o700,exist_ok=False);yield str(p)
    def capture(self,command,**kwargs):
        if self.index>=2 or kwargs!={'capture_output':True,'text':True} or command!=self.plan['commands'][self.index]['request_argv']:raise RuntimeError('two exact application captures differ')
        stage=self.plan['commands'][self.index];self.owner.remaining(stage['cap']);self.verify_inputs();actor.verify_actor(self.candidate);h=_helpers();ctx=json.loads(h.raw_file(Path(self.plan['context']['path']),expected=self.plan['context'])[0])
        runtime=json.loads(h.raw_file(Path(self.prepared['runtime']['path']),expected=self.prepared['runtime'])[0])
        argv=[runtime['base_python_record']['path'],'-I','-S','-B',str(Path(__file__).with_name('w26_application_stage.py')),'--context',self.plan['context']['path'],'--sha',self.plan['context']['sha256'],'--index',str(self.index),'--port',str(self.port)]
        result=actor.run_owned_stage(self.owner,self.logsfd,self.index,stage['label'],argv,self.root/'outside',ctx['environment'],stage['cap'],self.binding,self.plan,sandbox_path=self.root/'APP_SANDBOX.sb')
        self.completed.append(result);h.publish(self.root/'logs'/('PASS_'+str(self.index)+'.json'),result);self.index+=1
        return SimpleNamespace(returncode=result['rc'],stdout=h.raw_file(self.root/'logs'/result['stdout'])[0].decode(),stderr=h.raw_file(self.root/'logs'/result['stderr'])[0].decode())

def execute_bundle(root,candidate,binding,binding_record,*,began,admission_token=None):
    if admission_token is not actor._OWNER_TOKEN:raise RuntimeError('app cannot mint original owner from JSON')
    h=_helpers();root=Path(root);plan=json.loads(h.raw_file(Path(candidate['plan']['path']),expected=candidate['plan'])[0]);scope=actor.activity_scope(plan)
    if scope['activity_kind']!=actor.APP_BUNDLE or plan['version']!=VERSION or plan['target']!=TARGET:raise RuntimeError('closed application version/target scope differs')
    prepared=json.loads(h.raw_file(Path(candidate['prepared']['path']),expected=candidate['prepared'])[0]);owner=actor.RuntimeOwner(root,plan,token=actor._OWNER_TOKEN,began=began);actor._RETAINED[str(root)]=owner
    actor.verify_actor(candidate);owner.remaining(600)
    ctx=json.loads(h.raw_file(Path(plan['context']['path']),expected=plan['context'])[0])
    if plan['commands']!=commands(root,prepared['builder']['path']) or ctx!={'activity_kind':actor.APP_BUNDLE,'unit_root':str(root),'root_identity':candidate['root_identity'],'outside':str(root/'outside'),'environment':deps.dependency_environment(root),'legacy_sources':prepared['legacy_sources'],'commands':plan['commands']}:
        raise RuntimeError('closed app exact context/argv differs')
    tools=json.loads(h.raw_file(Path(candidate['toolchain']['path']),expected=candidate['toolchain'])[0])
    for row in tools.values():
        if actor.system_record(row['path'])!=row:raise RuntimeError('bound app system input changed')
    h.raw_file(Path(plan['administrative_python']['path']),expected=plan['administrative_python']['record'])
    # Read-only validation before once, directories, listener or original Popen.
    validator=ApplicationLaunch(root,owner,candidate,binding,plan,prepared,None,None);validator.verify_inputs()
    if time.monotonic()-began>=600:raise TimeoutError('app prepare cap before effects exhausted')
    h.publish(root/'UNIT_ONCE_GUARD.json',{'binding_first_record':binding_record,'nonce':binding['nonce'],'activity_kind':actor.APP_BUNDLE,'epoch':began,'budget':1800,'cleanup_reserve':300})
    for name in ['tmp','logs','evidence','outside','empty-home','appwheel','bundlewheelhouse','metadata','bundle']:(root/name).mkdir(mode=0o700,exist_ok=False)
    sandbox='(version 1)\n(allow default)\n(deny network*)\n(deny file-write*)\n'+''.join('(allow file-write* (subpath '+json.dumps(str(root/name))+'))\n' for name in ['tmp','logs','evidence','appwheel'])+'(allow file-write* (literal "/dev/null"))\n'
    h.copy_blob(root/'APP_SANDBOX.sb',sandbox.encode());logsfd=os.open(root/'logs',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW);old=actor.original.subprocess;launch=None
    actor.original.subprocess=SimpleNamespace(Popen=owner.popen,run=owner.diagnostic_run,TimeoutExpired=actor.subprocess.TimeoutExpired)
    try:
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0));listener.listen(1);listener.settimeout(.25);port=listener.getsockname()[1]
            with socket.create_connection(('127.0.0.1',port),timeout=1):positive,_=listener.accept();positive.close()
            h.publish(root/'evidence/OWNED_LISTENER_POSITIVE.json',{'host':'127.0.0.1','port':port,'actual_owned_parent_listener':True,'positive_connected':True})
            launch=ApplicationLaunch(root,owner,candidate,binding,plan,prepared,logsfd,port)
            with release._controlled_launch_scope(launch):
                built=release.build_wheel(Path(prepared['source']['snapshot_source']),root/'appwheel',Path(prepared['builder']['path']),root/'evidence')
                if launch.index!=2 or built['wheel_metadata']['version']!=VERSION:raise RuntimeError('actual application build identity/captures differs')
                wheel=root/'appwheel'/built['wheel'];h.raw_file(wheel);owner.remaining(180)
                release.compose_install_lock(root/'requirements-runtime.lock',wheel,TARGET,root/'requirements-application.lock')
                runtime_pins=release.parse_hash_requirements(root/'requirements-runtime.lock',TARGET)
                copied=[]
                for p in sorted((root/'wheelhouse').glob('*.whl')):
                    if release.wheel_metadata(p)['normalized_name'] in runtime_pins:
                        raw,record=h.raw_file(p);h.copy_blob(root/'bundlewheelhouse'/p.name,raw);copied.append(record)
                if len(copied)!=41:raise RuntimeError('runtime exactly41 wheel closure differs')
                h.copy_blob(root/'bundlewheelhouse'/wheel.name,h.raw_file(wheel)[0])
                manifest=release.create_bundle_manifest(root/'bundlewheelhouse',TARGET,root/'requirements-application.lock',root/'INPUT_uv_lock',root/'metadata')
                bundle=release.package_wheelhouse_bundle(root/'bundlewheelhouse',root/'requirements-application.lock',root/'INPUT_uv_lock',root/'metadata',root/'INPUT_operations',TARGET,root/'bundle/comsol-mcp-0.1.10-macos-arm64-offline.zip',root/'evidence',root/'evidence')
            try:accepted,_=listener.accept();accepted.close();raise RuntimeError('sandboxed app reached held listener')
            except socket.timeout:pass
        owner.remaining(180);launch.verify_inputs();actor.verify_actor(candidate)
        h.publish(root/'APPLICATION_BUNDLE_RESULT.json',{'status':'PASS_CURRENT_APPLICATION_BUNDLE_SCOPED_NOT_RELEASE','version':VERSION,'target':TARGET,'commands':launch.completed,'samples':owner.samples,'build':built,'manifest':manifest,'bundle':bundle,'outputs':[h.raw_file(p)[1] for p in sorted(root.rglob('*')) if p.is_file() and p.parent in [root/'appwheel',root/'metadata',root/'bundle']], 'application_install_transition_full_restore':'NOT_RUN','COMSOL_JVM_GUI':'NOT_RUN','physical_Intel_Windows':'NOT_RUN','independent_final_review':'NOT_RUN'})
        return root/'APPLICATION_BUNDLE_RESULT.json'
    except BaseException as exc:actor.retain_first_failure(owner,logsfd,exc,launch.completed if launch else [],stem='APPLICATION');raise
    finally:actor.original.subprocess=old;os.close(logsfd)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--prior-root',type=Path,required=True);p.add_argument('--source',type=Path,required=True);p.add_argument('--tag',default='app1');a=p.parse_args();print(prepare(a.root,a.prior_root,a.source,tag=a.tag))
