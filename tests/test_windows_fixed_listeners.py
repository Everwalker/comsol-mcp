"""Software-only fixed listener integration; never native admission evidence."""
from dataclasses import replace
import copy
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from comsol_mcp import _java_worker as worker_module, _session_server as server, _windows_t037 as t037
from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._session_context import CanonicalSocket
from tests.test_session_server import _StartupProcess, _startup_runtime
from tests.test_windows_network_intent import network_profile_and_plan


def factory_for(profile=None):
    if profile is None:
        profile,_=network_profile_and_plan()
        from tests.test_windows_child_acl import _observer
        software_profile,*_=_observer()
        profile=replace(profile,windows_isolation=replace(profile.windows_isolation,
            acl_grants=software_profile.host_resource_profile.windows_isolation.acl_grants))
    from comsol_mcp._windows_job_limits import JobLimitsV1
    wrapper=t037.WindowsT037ProfileV2(profile,'d'*64,{},'SOFTWARE_INTENT_ONLY',
        job_limits=JobLimitsV1(4096,16384,4),wrapper_schema=t037.PROFILE_SCHEMA_V3)
    return t037.T037SessionProcessFactory(wrapper,'session')


def software_original_owner(factory,child,command,*,birth=None,role='worker'):
    """Negative-only receipt replacement on an actual SDK-boundary child.

    Always construct the valid original receipt first. Deliberate internal
    corruption below tests consumer refusal; it never creates birth authority.
    """
    original=child._api._g2_session_launch_owners[id(child)]
    owner=t037._Win32G2SessionLaunchOwner(api=original.api,process=child,handle=original.process_handle,
        birth_filetime_100ns=original.birth_filetime_100ns,argv=original.argv,
        job_handle=original.job_handle,profile=original.profile,role=original.role,
        image_path=original.image_path,image_sha256=original.image_sha256,
        session_factory=child._original_factory,seal=t037._G2_SESSION_LAUNCH_OWNER_SEAL)
    if factory is not child._original_factory:object.__setattr__(owner,'session_factory',factory)
    if role!='worker':object.__setattr__(owner,'role',role)
    if tuple(command)!=original.argv:object.__setattr__(owner,'argv',tuple(command))
    if birth is not None:object.__setattr__(owner,'birth_filetime_100ns',birth)
    child._api._g2_session_launch_owners[id(child)]=owner
    return owner


def test_factory_readonly_accessor_derives_same_profile_two_distinct_endpoints():
    factory=factory_for()
    intent=factory.network_intent
    assert (intent.server_listen_port,intent.worker_listen_port)==(32001,32002)
    assert factory.network_endpoint('server')==CanonicalSocket('127.0.0.1',32001)
    assert factory.network_endpoint('worker')==CanonicalSocket('127.0.0.1',32002)
    assert intent.verification_state=='NOT_VERIFIED'
    with pytest.raises(AttributeError):factory.network_intent=intent
    with pytest.raises(ExecutionContractError):factory.network_endpoint('other')
    with pytest.raises(ExecutionContractError):factory.preflight()


@pytest.mark.parametrize('fault',['profile_replaced','host_replaced','profile_source_changed','session_changed'])
def test_factory_accessor_rejects_mutated_original_binding(fault):
    factory=factory_for()
    if fault=='profile_replaced':factory.profile=replace(factory.profile)
    elif fault=='host_replaced':object.__setattr__(factory.profile,'host_resource_profile',replace(factory.profile.host_resource_profile))
    elif fault=='profile_source_changed':object.__setattr__(factory.profile,'source_sha256','f'*64)
    else:factory.session_id='another-session'
    with pytest.raises(ExecutionContractError):factory.network_endpoint('server')


@pytest.mark.parametrize('fault',['dict','proxy','profile_subclass','factory_subclass','uninitialized'])
def test_accessor_cannot_turn_wire_or_arbitrary_factory_profile_into_port_binding(fault):
    factory=factory_for()
    if fault=='dict':selected=factory.profile.host_resource_profile.as_dict()
    elif fault=='proxy':selected=NS(profile=factory.profile,network_intent=factory.network_intent)
    elif fault=='profile_subclass':
        class Profile(t037.WindowsT037ProfileV2):pass
        selected=t037.T037SessionProcessFactory(Profile(factory.profile.host_resource_profile,'d'*64,{},'SOFTWARE'),'session')
    elif fault=='factory_subclass':
        class Factory(t037.T037SessionProcessFactory):pass
        selected=Factory(factory.profile,'session')
    else:selected=t037.T037SessionProcessFactory.__new__(t037.T037SessionProcessFactory)
    with pytest.raises(ExecutionContractError):t037.T037SessionProcessFactory.network_endpoint(selected,'server')


def invalid_profile(fault):
    profile=factory_for().profile.host_resource_profile;iso=profile.windows_isolation;grants=list(iso.wfp_grants)
    if fault=='port0':grants[0]=replace(grants[0],remote_ports=(0,))
    elif fault=='same_listeners':grants[2]=replace(grants[2],local_ports=(32001,))
    elif fault=='range_reversed':grants[0]=replace(grants[0],local_port_range=(45000,40000))
    elif fault=='range_conflict':grants[0]=replace(grants[0],local_ports=(40001,))
    elif fault=='destination_range':grants[0]=replace(grants[0],local_port_range=None,remote_ports=(),remote_port_range=(40000,45000))
    elif fault=='extra_grant':grants.append(replace(grants[0],local_ports=(40001,),local_port_range=None))
    elif fault=='missing_flow':grants.pop()
    elif fault=='IPv6':grants[0]=replace(grants[0],address_family='IPV6',local_addresses=('::1/128',),remote_addresses=('::1/128',))
    elif fault=='wide_address':grants[0]=replace(grants[0],remote_addresses=('0.0.0.0/0',))
    elif fault=='wrong_image':grants[0]=replace(grants[0],application_path=grants[1].application_path)
    # Exercise consumer revalidation of a corrupted typed in-memory object;
    # normal schema construction already rejects many of these earlier.
    damaged=copy.copy(profile)
    object.__setattr__(damaged,'windows_isolation',replace(iso,wfp_grants=tuple(grants)))
    return damaged


@pytest.mark.parametrize('fault',['port0','same_listeners','range_reversed','range_conflict','destination_range',
                                 'extra_grant','missing_flow','IPv6','wide_address','wrong_image'])
@pytest.mark.parametrize('role',['server','worker'])
def test_invalid_intent_refuses_before_directory_compilation_or_process(monkeypatch,tmp_path,fault,role):
    factory=factory_for(invalid_profile(fault))
    monkeypatch.setattr(t037.T037SessionProcessFactory,'preflight',lambda self:None)  # software boundary only
    monkeypatch.setattr(t037.T037SessionProcessFactory,'__call__',lambda *a,**k:pytest.fail('Popen before validation'))
    monkeypatch.setattr(server,'create_server_directories',lambda *a,**k:pytest.fail('directories before validation'))
    monkeypatch.setattr(worker_module.subprocess,'run',lambda *a,**k:pytest.fail('javac before validation'))
    if role=='server':
        with pytest.raises((ExecutionContractError,ValueError)):
            server.OwnedServerLauncher(platform_name='darwin').start(None,'project','session',process_factory=factory)
    else:
        class UntouchedPaths:
            def validate(self):pytest.fail('runtime inspected before invalid intent refusal')
        worker=worker_module.PersistentJavaWorker(UntouchedPaths(),state_dir=tmp_path/'not-created',process_factory=factory)
        with pytest.raises((ExecutionContractError,ValueError)):worker.start()
        assert not worker.state_dir.exists()


def server_start(monkeypatch,tmp_path,reported_port=None,listener=None,factory=None):
    factory=factory_for() if factory is None else factory
    runtime=_startup_runtime(tmp_path);process=_StartupProcess();commands=[];reads=[]
    if reported_port is None:reported_port=str(factory.network_intent.server_listen_port)
    monkeypatch.setattr(t037.T037SessionProcessFactory,'preflight',lambda self:None)
    def launch(self,command,**options):
        commands.append(tuple(command))
        Path(command[command.index('-portfile')+1]).write_text(reported_port)
        return process
    monkeypatch.setattr(t037.T037SessionProcessFactory,'__call__',launch)
    def listeners(port):
        reads.append(port)
        return [(process.pid,listener or CanonicalSocket('127.0.0.1',port))]
    launcher=server.OwnedServerLauncher(process_factory=factory,platform_name='darwin',
        process_identity_reader=lambda pid:dict(alive=True,start_epoch_ms=123456),listener_reader=listeners,
        ready_timeout_s=0.02,poll_interval_s=0.001)
    return factory,runtime,process,commands,reads,launcher


def test_server_command_and_expected_endpoint_keep_original_factory_binding(monkeypatch,tmp_path):
    factory,runtime,process,commands,reads,launcher=server_start(monkeypatch,tmp_path)
    # A mutable instance method/port decoration cannot replace the class accessor.
    factory.server_port=65000;factory.network_endpoint=lambda role:CanonicalSocket('127.0.0.1',65000)
    handle=launcher.start(runtime,'project','session')
    assert commands[0][commands[0].index('-port')+1]=='32001'
    assert reads==[32001] and handle.endpoint==handle.expected_endpoint==CanonicalSocket('127.0.0.1',32001)
    assert handle.network_intent.profile_sha256==factory.profile.sha256 and handle.launch_argv==commands[0]
    assert handle.process is process and not process.mutation_calls


@pytest.mark.parametrize('reported',['32002','0','','True','65536','-1'])
def test_server_misreported_port_retains_unknown_child_without_foreign_listener_query(monkeypatch,tmp_path,reported):
    factory,runtime,process,commands,reads,launcher=server_start(monkeypatch,tmp_path,reported_port=reported)
    with pytest.raises(server.OwnedServerError) as caught:launcher.start(runtime,'project','session')
    assert caught.value.uncertain and caught.value.handle.process is process
    assert caught.value.handle.expected_endpoint==CanonicalSocket('127.0.0.1',32001)
    assert caught.value.handle.endpoint is None and not reads and not process.mutation_calls


@pytest.mark.parametrize('address',['::1','0.0.0.0','127.0.0.2'])
def test_server_different_listener_address_is_never_adopted(monkeypatch,tmp_path,address):
    factory,runtime,process,commands,reads,launcher=server_start(monkeypatch,tmp_path,listener=CanonicalSocket(address,32001))
    with pytest.raises(server.OwnedServerError) as caught:launcher.start(runtime,'project','session')
    assert caught.value.uncertain and caught.value.handle.process is process and not process.mutation_calls


def worker_start(monkeypatch,tmp_path,fault=None,factory=None):
    factory=factory_for() if factory is None else factory
    root=tmp_path/'runtime';jdk=tmp_path/'jdk'
    (root/'bin').mkdir(parents=True);(root/'apiplugins').mkdir();(jdk/'bin').mkdir(parents=True)
    (root/'bin/comsolclientpath.txt').write_text('fixture.jar\n');(root/'apiplugins/fixture.jar').write_bytes(b'software')
    for name in ['java','javac']:(jdk/'bin'/name).write_bytes(b'software')
    paths=worker_module.JavaWorkerPaths(root,jdk,project_root=tmp_path)
    worker=worker_module.PersistentJavaWorker(paths,state_dir=tmp_path/'worker',process_factory=factory)
    from tests.test_windows_suspended_ownership import _launch_api
    _base,api,events,model=_launch_api(monkeypatch)
    api.close_job(300)  # Retire the fixture's old Job before binding this actual profile.
    from tests.test_windows_job_limits import SDK
    sdk=SDK()  # A fresh SDK Job really starts with zero limits, then is configured once.
    api.k.CreateJobObjectW=sdk.CreateJobObjectW
    api.k.QueryInformationJobObject=sdk.QueryInformationJobObject
    api.k.SetInformationJobObject=sdk.SetInformationJobObject
    state=dict(birth=(11644473600000+123456)*10000+789,exited=None,reads=[],producer_finished=False)
    mutations=[];processes=[]
    def times(handle):
        if state['producer_finished']:state['reads'].append(handle)
        return (10,None) if handle==777 else (state['birth'],state['exited'])
    api.process_times=times
    raw_close=api.close_handle
    def native_close(handle):
        if state['producer_finished'] and handle==200:mutations.append(('close',handle))
        raw_close(handle);return 1
    api.k.CloseHandle=native_close
    api.close_handle=t037._Win32ReadOnlyApi.close_handle.__get__(api)
    original_terminate=api.terminate_process
    def terminate(handle,code):
        mutations.append(('terminate',handle,code));return original_terminate(handle,code)
    api.terminate_process=terminate
    original_image=api._open_image
    api._open_image=lambda path:(*original_image(path)[:4],path)
    api._hash_image_handle=lambda *args:factory.profile.worker_image_sha256
    api.process_image_path=lambda handle:state['image_path']
    api._software_sdk=state
    api._software_child_model=model;api._software_handle_events=events
    factory._api=api;factory._job=api.create_job(profile=factory.profile)
    def software_resume(child,capability):
        child.note_resume_attempt();child._resume_epoch+=1;return 1
    monkeypatch.setattr('comsol_mcp._windows_t037_resume.issue_native_resume',lambda *a:object())
    monkeypatch.setattr('comsol_mcp._windows_t037_resume.resume_native_child',software_resume)
    commands=[];compile_calls=[];health_calls=[];listener_reads=[]
    monkeypatch.setattr(t037.T037SessionProcessFactory,'preflight',lambda self:None)
    def compile(command,**kw):
        if Path(command[0]).name=='javac' and command[1:]!=['-version']:compile_calls.append(command)
        else:assert command[1:]==['-version']
        return NS(returncode=0,stdout='',stderr='')
    monkeypatch.setattr(worker_module.subprocess,'run',compile)
    def launch(self,command,**options):
        commands.append(tuple(command));endpoint=Path(command[command.index('--endpoint-file')+1])
        if fault=='original_birth_differs':state['birth']-=10000
        state['image_path']=command[0]
        process=api.create_suspended_appcontainer_child(command,options,profile=self.profile,
            job=self._job,role='worker',session_factory=self)
        state['producer_finished']=True
        process.software_sdk=state;process.mutation_calls=mutations;processes.append(process)
        ready=dict(type='ready',host='127.0.0.1',port=factory.network_intent.worker_listen_port,pid=process.pid,process_start_epoch_ms=123456,
            generation=1,instance_id='software-instance',token=options['env']['COMSOL_MCP_WORKER_TOKEN'])
        if fault in ('wrong_port','port0','string_port','bool_port'):ready['port']={'wrong_port':32001,'port0':0,'string_port':'32002','bool_port':True}[fault]
        elif fault in ('IPv6','wide','localhost','empty_host'):ready['host']={'IPv6':'::1','wide':'0.0.0.0','localhost':'localhost','empty_host':''}[fault]
        elif fault=='wrong_pid':ready['pid']+=1
        elif fault=='wrong_birth':ready['process_start_epoch_ms']+=1
        elif fault=='wrong_generation':ready['generation']=2
        elif fault=='wrong_token':ready['token']='other-token'
        elif fault=='missing_host':ready.pop('host')
        endpoint.write_text(json.dumps(ready));return process
    monkeypatch.setattr(t037.T037SessionProcessFactory,'__call__',launch)
    monkeypatch.setattr(worker_module,'process_identity',lambda pid:dict(alive=True,start_epoch_ms=123456))
    def listeners(port,**kwargs):
        process=processes[0]
        listener_reads.append(port)
        address='::1' if fault=='listener_IPv6' else '127.0.0.1'
        pid=process.pid+1 if fault=='listener_wrong_pid' else process.pid
        return [(pid,CanonicalSocket(address,port))]
    monkeypatch.setattr(server,'listener_rows',listeners)
    def health(**kw):health_calls.append(worker.endpoint);return dict(status='HEALTHY')
    monkeypatch.setattr(worker,'health',health)
    return factory,worker,processes,commands,compile_calls,health_calls,listener_reads


def test_full_startup_ready_and_current_os_match_but_original_handle_birth_differs(monkeypatch,tmp_path):
    factory,worker,processes,commands,compiled,health,reads=worker_start(monkeypatch,tmp_path,'original_birth_differs')
    with pytest.raises(worker_module.JavaWorkerError) as caught:worker.start()
    process=processes[0]
    ready=json.loads((worker.state_dir/'worker_endpoint.json').read_text())
    owner=t037._native_g2_session_launch_owner(process)
    assert ready['process_start_epoch_ms']==worker_module.process_identity(process.pid)['start_epoch_ms']==123456
    assert owner.birth_filetime_100ns//10000-11644473600000==123455
    assert process.software_sdk['reads']==[owner.process_handle]
    assert caught.value.execution_state_unknown and worker._process is process
    assert worker._port is None and worker._generation is None and not health and not reads and not process.mutation_calls
    with pytest.raises(worker_module.JavaWorkerError):worker.start()
    with pytest.raises(worker_module.JavaWorkerError):worker.close()
    with pytest.raises(worker_module.JavaWorkerError):worker._request({'type':'health'},timeout_s=1)
    assert len(commands)==len(compiled)==1 and not process.mutation_calls


def test_worker_fixed_listener_command_ready_and_original_binding(monkeypatch,tmp_path):
    factory,worker,processes,commands,compiled,health,reads=worker_start(monkeypatch,tmp_path)
    assert worker.start()['status']=='HEALTHY'
    process=processes[0]
    assert commands[0][commands[0].index('--port')+1]=='32002'
    assert worker.endpoint==('127.0.0.1',32002) and health==[worker.endpoint] and reads==[32002]
    assert worker._network_intent.profile_sha256==factory.profile.sha256
    assert worker._launch_argv==commands[0] and worker._process is process and not process.mutation_calls
    owner=t037._native_g2_session_launch_owner(process)
    assert worker._fixed_process is process and worker._fixed_launch_owner is owner
    assert process.software_sdk['reads']==[owner.process_handle,owner.process_handle]
    assert owner.birth_filetime_100ns%10000==789  # Compare exact FILETIME, floor only for ready/OS ms.


@pytest.mark.parametrize('fault',['missing_owner','decorative_metadata','foreign_factory','wrong_role','wrong_argv',
    'wrapper_return','job_replaced','profile_replaced','api_replaced','handle_replaced','closed_process'])
def test_full_startup_requires_original_private_owner_not_pid_metadata(monkeypatch,tmp_path,fault):
    factory,worker,processes,commands,compiled,health,reads=worker_start(monkeypatch,tmp_path)
    original=t037.T037SessionProcessFactory.__call__;returned=[];created_owners=[]
    def launch(self,command,**options):
        child=original(self,command,**options)
        owner=t037._native_g2_session_launch_owner(child);created_owners.append(owner)
        if fault=='missing_owner':child._api._g2_session_launch_owners.clear()
        elif fault=='decorative_metadata':
            child._api._g2_session_launch_owners[id(child)]=NS(**{name:getattr(owner,name) for name in owner.__slots__})
        elif fault=='foreign_factory':
            other=t037.T037SessionProcessFactory(self.profile,self.session_id,api=child._api);other._job=self._job
            software_original_owner(other,child,command)
        elif fault=='wrong_role':software_original_owner(self,child,command,role='server')
        elif fault=='wrong_argv':software_original_owner(self,child,[*command,'--forged'])
        elif fault=='wrapper_return':
            child=t037._Win32ChildProcess(child._api,child._handle,child.pid,command)
        elif fault=='job_replaced':self._job=object()
        elif fault=='profile_replaced':self.profile=replace(self.profile)
        elif fault=='api_replaced':self._api=object()
        elif fault=='handle_replaced':child._handle=object()
        elif fault=='closed_process':child._closed=True
        returned.append(child);return child
    monkeypatch.setattr(t037.T037SessionProcessFactory,'__call__',launch)
    monkeypatch.setattr(worker_module.socket,'create_connection',lambda *a,**kw:pytest.fail('RPC before original owner validation'))
    with pytest.raises(worker_module.JavaWorkerError) as caught:worker.start()
    process=processes[0]
    ready=json.loads((worker.state_dir/'worker_endpoint.json').read_text())
    assert ready['pid']==process.pid and ready['process_start_epoch_ms']==worker_module.process_identity(process.pid)['start_epoch_ms']
    assert caught.value.execution_state_unknown and worker._fixed_process is worker._process is returned[0]
    # Original sealed owner/child remain alive even when a same-PID wrapper is returned.
    assert created_owners[0].process is process and not process.mutation_calls
    assert not health and not reads and worker._port is None and worker._generation is None
    with pytest.raises(worker_module.JavaWorkerError):worker.start()
    with pytest.raises(worker_module.JavaWorkerError):worker.close()
    with pytest.raises(worker_module.JavaWorkerError):worker._request({'type':'health'},timeout_s=1)
    assert len(commands)==len(compiled)==1 and not process.mutation_calls


def test_original_owner_producer_error_retains_factory_return_before_ready_or_rpc(monkeypatch,tmp_path):
    factory,worker,processes,commands,compiled,health,reads=worker_start(monkeypatch,tmp_path)
    def unavailable(child):
        assert child is processes[0]
        raise OSError('software private original-owner producer failure')
    monkeypatch.setattr(t037,'_native_g2_session_launch_owner',unavailable)
    with pytest.raises(worker_module.JavaWorkerError) as caught:worker.start()
    process=processes[0]
    assert caught.value.execution_state_unknown and worker._fixed_process is worker._process is process
    assert worker._fixed_launch_owner is None and not process.software_sdk['reads']
    assert not reads and not health and not process.mutation_calls
    with pytest.raises(worker_module.JavaWorkerError):worker.start()
    with pytest.raises(worker_module.JavaWorkerError):worker.close()
    assert len(commands)==len(compiled)==1 and not process.mutation_calls


@pytest.mark.parametrize('fault',['birth_differs_below_ms','birth_bool','birth_zero','times_none','times_list',
    'times_extra','exited','sdk_exception'])
def test_original_handle_readback_is_strict_and_required_before_rpc(monkeypatch,tmp_path,fault):
    factory,worker,processes,commands,compiled,health,reads=worker_start(monkeypatch,tmp_path)
    times=factory._api.process_times
    def observed(handle):
        birth,exited=times(handle)
        if not factory._api._software_sdk['producer_finished']:return birth,exited
        if fault=='birth_differs_below_ms':return birth+1,None
        if fault=='birth_bool':return True,None
        if fault=='birth_zero':return 0,None
        if fault=='times_none':return None
        if fault=='times_list':return [birth,None]
        if fault=='times_extra':return birth,None,None
        if fault=='exited':return birth,birth+1
        raise OSError('software original GetProcessTimes failure')
    factory._api.process_times=observed
    with pytest.raises(worker_module.JavaWorkerError) as caught:worker.start()
    process=processes[0]
    assert caught.value.execution_state_unknown and worker._fixed_process is worker._process is process
    assert process.software_sdk['reads']==[process._handle] and not health and not reads and not process.mutation_calls
    with pytest.raises(worker_module.JavaWorkerError):worker.start()
    with pytest.raises(worker_module.JavaWorkerError):worker.close()
    assert process.software_sdk['reads']==[process._handle] and len(commands)==1 and not process.mutation_calls


@pytest.mark.parametrize('fault',['wrapper_after_capture','owner_after_capture','job_after_capture',
    'original_handle_birth_drift','current_os_birth_drift'])
def test_listener_observation_cannot_replace_captured_child_or_original_birth(monkeypatch,tmp_path,fault):
    factory,worker,processes,commands,compiled,health,reads=worker_start(monkeypatch,tmp_path)
    original=server.listener_rows;replacement=[]
    def observe(port,**kwargs):
        rows=original(port,**kwargs)
        process=processes[0]
        if fault=='wrapper_after_capture':
            wrapper=t037._Win32ChildProcess(process._api,process._handle,process.pid,commands[0])
            worker._process=wrapper;replacement.append(wrapper)
        elif fault=='owner_after_capture':replacement.append(software_original_owner(factory,process,commands[0]))
        elif fault=='job_after_capture':factory._job=object()
        elif fault=='original_handle_birth_drift':process.software_sdk['birth']+=1
        else:monkeypatch.setattr(worker_module,'process_identity',lambda pid:dict(alive=True,start_epoch_ms=123457))
        return rows
    monkeypatch.setattr(server,'listener_rows',observe)
    monkeypatch.setattr(worker_module.socket,'create_connection',lambda *a,**kw:pytest.fail('RPC after original owner/birth drift'))
    with pytest.raises(worker_module.JavaWorkerError) as caught:worker.start()
    process=processes[0]
    captured=worker._fixed_launch_owner
    assert caught.value.execution_state_unknown and captured.process is worker._fixed_process is process
    assert captured.process_handle is process._handle and reads==[32002] and not health and not process.mutation_calls
    if fault=='wrapper_after_capture':assert worker._process is replacement[0] and worker._process is not process
    if fault=='owner_after_capture':assert captured is not replacement[0]
    reads_before=list(process.software_sdk['reads'])
    with pytest.raises(worker_module.JavaWorkerError):worker.start()
    with pytest.raises(worker_module.JavaWorkerError):worker.close()
    with pytest.raises(worker_module.JavaWorkerError):worker._request({'type':'health'},timeout_s=1)
    assert len(commands)==1 and process.software_sdk['reads']==reads_before and not process.mutation_calls


@pytest.mark.parametrize('fault',['wrong_port','port0','string_port','bool_port','IPv6','wide','localhost','empty_host',
                                 'missing_host','wrong_pid','wrong_birth','wrong_generation','wrong_token',
                                 'listener_IPv6','listener_wrong_pid'])
def test_worker_ready_mismatch_preserves_unknown_no_rpc_restart_or_kill(monkeypatch,tmp_path,fault):
    factory,worker,processes,commands,compiled,health,reads=worker_start(monkeypatch,tmp_path,fault)
    with pytest.raises(worker_module.JavaWorkerError) as caught:worker.start()
    process=processes[0]
    assert caught.value.execution_state_unknown and worker._process is process
    assert worker._port is None and worker._generation is None and not health and not process.mutation_calls
    if fault not in ('listener_IPv6','listener_wrong_pid'):assert not reads
    with pytest.raises(worker_module.JavaWorkerError):worker.start()
    with pytest.raises(worker_module.JavaWorkerError):worker.close()
    assert len(commands)==len(compiled)==1 and worker._process is process and not process.mutation_calls


def test_worker_T037_does_not_adopt_endpoint_json_or_mutated_factory(monkeypatch,tmp_path):
    factory,worker,processes,commands,compiled,health,reads=worker_start(monkeypatch,tmp_path)
    worker.state_dir.mkdir();cache=worker.state_dir/'worker_endpoint.json';cache.write_text('{}')
    with pytest.raises(worker_module.JavaWorkerError):worker.start()
    assert not commands and not compiled and not health and cache.read_text()=='{}'
    cache.unlink();worker._process_factory=factory_for()
    with pytest.raises(worker_module.JavaWorkerError):worker.start()
    assert not commands and not compiled and not health


def test_ordinary_nonT037_still_requests_port_zero_and_ignores_decorative_intent(monkeypatch,tmp_path):
    factory,worker,processes,commands,compiled,health,reads=worker_start(monkeypatch,tmp_path)
    def ordinary(command,**options):
        return t037.T037SessionProcessFactory.__call__(factory,command,**options)
    ordinary.network_intent=factory.network_intent
    worker=worker_module.PersistentJavaWorker(worker.paths,state_dir=tmp_path/'ordinary',process_factory=ordinary)
    monkeypatch.setattr(worker,'health',lambda **kw:dict(status='HEALTHY'))
    worker.start()
    process=processes[0]
    assert commands[0][commands[0].index('--port')+1]=='0' and not reads
    dirs=server._server_directories(tmp_path/'uncreated')
    argv=server.build_server_command(dirs,platform_name='darwin',validate_launcher=False,process_factory=ordinary)
    assert argv[argv.index('-port')+1]=='0'


@pytest.mark.parametrize('factory',[{},[],NS(network_intent={'worker_listen_port':65000})])
def test_noncallable_factory_refuses_before_worker_temporary_directory(monkeypatch,factory):
    monkeypatch.setattr(worker_module.tempfile,'mkdtemp',lambda **kw:pytest.fail('temporary directory before invalid factory rejection'))
    with pytest.raises(worker_module.JavaWorkerError):worker_module.PersistentJavaWorker(None,process_factory=factory)


@pytest.mark.parametrize('fault',['expected_endpoint','port','factory','profile'])
def test_mutable_worker_decorations_do_not_replace_original_listener_binding(monkeypatch,tmp_path,fault):
    factory,worker,processes,commands,compiled,health,reads=worker_start(monkeypatch,tmp_path)
    original=t037.T037SessionProcessFactory.__call__
    def launch(self,command,**options):
        child=original(self,command,**options)
        if fault=='expected_endpoint':worker._expected_endpoint=('127.0.0.1',65000)
        elif fault=='port':
            file=worker.state_dir/'worker_endpoint.json';ready=json.loads(file.read_text())
            ready['port']=65000;file.write_text(json.dumps(ready));worker._expected_endpoint=('127.0.0.1',65000)
        elif fault=='factory':worker._process_factory=factory_for()
        else:factory.profile=replace(factory.profile)
        return child
    monkeypatch.setattr(t037.T037SessionProcessFactory,'__call__',launch)
    with pytest.raises(worker_module.JavaWorkerError) as caught:worker.start()
    process=processes[0]
    assert caught.value.execution_state_unknown and worker._process is process
    assert not reads and not health and not process.mutation_calls


def test_valid_network_intent_does_not_unlock_existing_preflight_or_create_default_directory(monkeypatch):
    factory=factory_for()
    assert factory.network_intent.verification_state=='NOT_VERIFIED'
    monkeypatch.setattr(worker_module.tempfile,'mkdtemp',lambda **kw:pytest.fail('T037 temporary directory before admission'))
    class Untouched:
        def validate(self):pytest.fail('runtime work before real preflight refusal')
    worker=worker_module.PersistentJavaWorker(Untouched(),process_factory=factory)
    assert not worker.state_dir.exists()
    with pytest.raises(ExecutionContractError) as caught:worker.start()
    assert caught.value.code==t037.ERROR_POLICY_UNVERIFIED and worker._process is None
    assert not worker.state_dir.exists()
    with pytest.raises(ExecutionContractError):server.OwnedServerLauncher().start(None,'project','session',process_factory=factory)


def test_both_commands_follow_one_factory_and_changed_profile_without_port_constants(monkeypatch,tmp_path):
    host=factory_for().profile.host_resource_profile;grants=list(host.windows_isolation.wfp_grants)
    grants[0]=replace(grants[0],remote_ports=(32111,));grants[1]=replace(grants[1],local_ports=(32111,))
    grants[2]=replace(grants[2],local_ports=(32112,))
    factory=factory_for(replace(host,windows_isolation=replace(host.windows_isolation,wfp_grants=tuple(grants))))
    factory,runtime,child,commands,reads,launcher=server_start(monkeypatch,tmp_path/'server',factory=factory)
    handle=launcher.start(runtime,'project','session')
    factory,worker,processes,argv,compiled,health,listeners=worker_start(monkeypatch,tmp_path/'worker',factory=factory)
    worker.start()
    process=processes[0]
    assert commands[0][commands[0].index('-port')+1]=='32111'
    assert argv[0][argv[0].index('--port')+1]=='32112'
    assert handle.network_intent==worker._network_intent==factory.network_intent
    assert worker._original_process_factory is factory and reads==[32111] and listeners==[32112]


def test_mutated_worker_port_and_decorative_fields_cannot_redirect_rpc(monkeypatch,tmp_path):
    factory,worker,processes,commands,compiled,health,reads=worker_start(monkeypatch,tmp_path)
    worker.start()
    process=processes[0]
    worker._port=65000;worker._expected_endpoint=('127.0.0.1',65000)
    worker._network_intent={'worker_listen_port':65000,'state':'VERIFIED'}
    monkeypatch.setattr(worker_module.socket,'create_connection',lambda *a,**kw:pytest.fail('foreign port RPC attempted'))
    with pytest.raises(worker_module.JavaWorkerError) as caught:worker._request({'type':'health'},timeout_s=1)
    assert caught.value.execution_state_unknown and worker._process is process and not process.mutation_calls
