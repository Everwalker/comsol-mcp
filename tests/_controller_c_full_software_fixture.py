"""Closed normal APPLY/Controller fixture with inert SDK/transport exports.

The real recipe, native-constructor trusted loader, Store, purpose coordinator,
A/B/primary preparer, one-use issuer/consumer, normal launcher/Worker and public
Controller bodies are used. Synthetic exports are not Windows qualification.
"""
from contextlib import contextmanager,ExitStack
from dataclasses import replace
from datetime import datetime,timezone
import ctypes as C
import hashlib
import io
import json
import ntpath
import os
import struct
import subprocess
import sys
from types import SimpleNamespace as NS
import pytest

from comsol_mcp import _windows_t037 as win,_windows_t037_qualification as q
from comsol_mcp import _windows_t037_qualification_run as qualification
from comsol_mcp import _windows_t037_child_evidence as evidence
from comsol_mcp import _windows_t037_child as child_source,_windows_t037_acl as acl
from comsol_mcp import _windows_os_probe_protocol as wire,_windows_t037_events as event_source
from comsol_mcp import _java_worker as java,_session_server as server,_control_daemon as control
from comsol_mcp import _windows_host_change as changes,_windows_t037_native as native
from comsol_mcp import _windows_worker_bootstrap as prepared,_windows_t037_resume as resume
from comsol_mcp._windows_owned_resources import registry_for
from comsol_mcp._session_context import CanonicalSocket
from tests._controller_c_software_fixture import materialize_sdk,install_single_api,REAL_ISSUER,REAL_RESUME,REAL_ACL,BIRTH
from tests.test_windows_t037_qualification_original_owner import make_plan,bind_token_sdk,bind_directory_security_sdk
from tests.test_windows_t037_primary_policy import child_from_actual_input,encode
from tests.test_windows_t037_connected_b import _text,_sid
from tests.test_windows_service_sid_native import SDK as ServiceSDK
from tests.test_windows_host_preflight_owner import Function
from tests import test_windows_t037_events as event_fixture
from tests import _prepared_launch_software_fixture as original
from tests import test_windows_scm_prebirth as scm
from tests.test_windows_host_change import _grant


class EchoSocket:
    """Only the parent's ordinary socket constructor/exports are simulated."""
    selected=None
    next_fd=20000
    def __init__(self,*args):
        self.runtime=type(self).selected;assert self.runtime is not None
        self.fd=type(self).next_fd;type(self).next_fd+=1
        self.bound=None;self.received=b'';self.closed=False
    def fileno(self):return self.fd
    def settimeout(self,value):assert value>0
    def bind(self,address):
        from comsol_mcp._windows_t037_network import derive_session_network_intent
        intent=derive_session_network_intent(self.runtime.env.profile)
        assert address==(intent.bind_address,intent.server_listen_port)
        self.bound=address
    def getsockname(self):return self.bound
    def listen(self,n):assert n==1
    def accept(self):
        state=self.runtime.network
        child=state['current'];self.runtime.make_network_records(child,state,complete=False)
        result=EchoSocket();result.received=state['echo_payload']
        return result,('127.0.0.1',state['echo_port'])
    def recv(self,n):value=self.received[:n];self.received=self.received[n:];return value
    def sendall(self,value):
        state=self.runtime.network
        assert value==state['echo_payload']
        self.runtime.make_network_records(state['current'],state,complete=True)
    def close(self):self.closed=True;self.fd=-1


class RpcSocket:
    """Actual Worker auth/submit/status parser consumes these inert I/O bytes."""
    def __init__(self,runtime,endpoint):self.runtime=runtime;self.endpoint=endpoint;self.lines=[]
    def __enter__(self):return self
    def __exit__(self,*_):return False
    def settimeout(self,value):pass
    def makefile(self,mode):assert mode=='rwb';return self
    def write(self,raw):
        value=json.loads(raw)
        if 'token' in value and 'type' not in value:
            self.lines.append(dict(ok=True));return len(raw)
        self.lines.append(self.runtime.rpc(value));return len(raw)
    def flush(self):pass
    def readline(self):return (json.dumps(self.lines.pop(0))+'\n').encode()


class FullSdk:
    def __init__(self,env):
        self.env=env;self.apis={};self.states={};self.by_factory={};self.project_factories=[]
        self.network=None;self.echoes=[];self.events=[];self.clock=event_fixture.NOW
        self.rpc_requests=[];self.worker_record=None;self.connected=False;self.generation=1
        self.instance='inert-original-vendor-instance';self.observed_version='6.4.0.293'
        self.sdk_calls=[]

    def prepare_api(self,key,*,existing=None):
        m=self.env.monkeypatch
        api,state=materialize_sdk(m,self.env,_provided=existing)
        state['records']=[];state['pending_probe']=None;state['key']=key;state['sdk_reads']=[]
        state['model']['alive']=True
        self.apis[key]=api;self.states[id(api)]=state
        query=state['query']
        original_create=type(api.a).CreateProcessAsUserW.__get__(api.a)
        class PI(C.Structure):
            _fields_=[('process',evidence.PTR),('thread',evidence.PTR),('pid',evidence.U32),('thread_id',evidence.U32)]
        def create(*args):
            returned=original_create(*args)
            row=C.cast(args[-1],C.POINTER(PI)).contents
            index=len(state['records']);row.process=200+index*2;row.thread=201+index*2
            row.pid=456+index;row.thread_id=9+index
            child=next(reversed(api._retained_children.values()))
            query.raw_command=args[2].value
            record=dict(child=child,pid=row.pid,thread_id=row.thread_id,handle=row.process,
                thread=row.thread,birth=BIRTH+10000*index,image=args[1],alive=True,assigned=False,
                start_ms=123456+index,inputs=child._launch_inputs)
            state['records'].append(record);state['current_record']=record;state['current']=child
            if key=='project':
                api._prepared_processes.append(child);api._prepared_commands.append(tuple(child.args))
                api._software_sdk['producer_finished']=True
            state['children'].append(child);state['image']=args[1]
            query.payload=self.env.Path(args[1]).read_bytes()
            return returned
        api.a.CreateProcessAsUserW=create
        state['model']['_software_acl_api'].process_id=lambda handle:next(r['pid'] for r in state['records'] if r['handle']==getattr(handle,'value',handle))
        def row(handle):
            h=getattr(handle,'value',handle)
            return next(x for x in state['records'] if h in (x['handle'],x['thread']))
        def process_times(handle):
            state['sdk_reads'].append(('process_times',getattr(handle,'value',handle)))
            return (BIRTH-100,None) if getattr(handle,'value',handle)==777 else (row(handle)['birth'],None if row(handle)['alive'] else row(handle)['birth']+100)
        def process_image(handle):
            state['sdk_reads'].append(('process_image',getattr(handle,'value',handle)))
            return self.env.profile._launch_image('launcher').path if getattr(handle,'value',handle)==777 else row(handle)['image']
        def poll(handle):
            state['sdk_reads'].append(('poll',getattr(handle,'value',handle)))
            return None if row(handle)['alive'] else 0
        api.process_times=process_times;api.process_image_path=process_image;api.poll_process=poll
        def wait(handle,timeout,args):
            state['sdk_reads'].append(('wait',getattr(handle,'value',handle)))
            record=row(handle)
            if record['inputs'].probe_input is not None and record['alive']:
                if state['key']=='files':self.make_file_records(record['child'],state)
                else:raise TimeoutError('original echo parent has not returned its bytes')
            if record['alive']:raise TimeoutError('inert original child still live')
            return 0
        api.wait_process=wait
        def terminate(handle,code):
            callback=getattr(self,'source_before_terminate',None)
            if callback is not None:callback(api,handle,code)
            state['sdk_reads'].append(('terminate',getattr(handle,'value',handle)))
            row(handle)['alive']=False;state['events'].append(('terminate',getattr(handle,'value',handle)))
            if key=='project':state['model']['alive']=any(r['alive'] for r in state['records'])
        api.terminate_process=terminate
        def assign(job,handle):row(handle)['assigned']=True;state['events'].append('assign-job');return 1
        api.k.AssignProcessToJobObject=assign
        original_limits_query=api.k.QueryInformationJobObject
        def limits_query(*args):
            returned=original_limits_query(*args)
            attempt=getattr(state.get('current'),'_resume_attempt',None)
            if (key=='files' and getattr(self.env,'_source_runtime_stop_after_consume',False)
                    and attempt is not None and attempt.state=='CONSUMED' and not state.get('scm_stop_called')):
                from comsol_mcp import _windows_t037_broker as broker
                runtime=self.env.daemon._windows_t037_scm_runtime
                assert runtime._broker._api.handler(broker.SERVICE_CONTROL_STOP,0,None,None)==0
                state['scm_stop_called']=True
            return returned
        api.k.QueryInformationJobObject=limits_query
        api.is_process_in_job=lambda handle,job:row(handle)['assigned']
        api._job_process_ids=lambda job:[x['pid'] for x in state['records'] if x['alive'] and x['assigned']]
        def times(handle,birth,*_):
            C.cast(birth,C.POINTER(evidence.U64)).contents.value=row(handle)['birth'];return 1
        def thread_times(handle,birth,*_):
            C.cast(birth,C.POINTER(evidence.U64)).contents.value=row(handle)['birth']+10;return 1
        query.process_times=times;query.thread_times=thread_times
        query.pid=lambda handle:row(handle)['pid']
        def child_policy(handle,kind,out,size):
            assert row(handle) is state['current_record'] and kind==13 and size==4
            query.calls.append('policy')
            C.cast(out,C.POINTER(evidence.U32))[0]=1;return 1
        query.policy=child_policy
        def open_token(handle,access,out):
            assert access==8 and row(handle) is state['current_record']
            query.opens+=1;query.stat_reads=0
            C.cast(out,C.POINTER(evidence.PTR)).contents.value=900+query.opens;return 1
        query.open_token=open_token
        def token_bytes(kind,base,read=False):
            token=child_from_actual_input(api,self.env.profile)
            index=len(state['records'])-1
            return encode(replace(token,token_id=3003+index,modified_id=8007+index),kind,base)
        query.token_bytes=token_bytes
        original_method=query.method
        def method(pointer,index,result,*types):
            if pointer.value==9204 and index==4:
                def get(this,name,flags,out,typ,flavor):
                    value=C.cast(out,C.POINTER(evidence._Variant)).contents;record=state['current_record']
                    if name=='ProcessId':value.vt,value.data[0]=3,record['pid']
                    elif name=='ExecutablePath':value.vt,value.data[0]=1,0
                    else:
                        text=(query.raw_command if name=='CommandLine' else
                            datetime.fromtimestamp(record['start_ms']/1000,timezone.utc).strftime('%Y%m%d%H%M%S.%f+000'))
                        value.vt,value.data[0]=8,query.allocate(text)
                    return 0
                return get
            return original_method(pointer,index,result,*types)
        query.method=method
        original_initialize=state['query_initializer']
        def initialize(reader,owner_api=None):
            original_initialize(reader,owner_api)
            reader.k.GetThreadId=lambda handle:row(handle)['thread_id']
            reader.k.GetProcessIdOfThread=lambda handle:row(handle)['pid']
        state['query_initializer']=initialize
        from comsol_mcp._windows_t037_child import NativeChildProcessApi
        # Keep the actual bounded NativeChildProcessApi/Toolhelp reader and
        # registry close. Only the kernel exports and their raw row are inert.
        parent_exports=NS();snapshots={};snapshot_seq=[0x9900]
        def snapshot(flags,pid):
            assert (flags,pid)==(2,0);snapshot_seq[0]+=1
            snapshots[snapshot_seq[0]]=0;state['sdk_reads'].append(('toolhelp_open',snapshot_seq[0]))
            return snapshot_seq[0]
        def process_row(handle,out,*,first):
            handle=getattr(handle,'value',handle)
            index=0 if first else snapshots[handle]+1;snapshots[handle]=index
            if index>=len(state['records']):return 0
            item=C.cast(out,C.POINTER(child_source.PROCESSENTRY32W)).contents
            assert item.dwSize==C.sizeof(item)
            item.th32ProcessID=state['records'][index]['pid'];item.th32ParentProcessID=os.getpid()
            state['sdk_reads'].append(('toolhelp_row',item.th32ProcessID));return 1
        def close_snapshot(handle):
            handle=getattr(handle,'value',handle);assert handle in snapshots
            snapshots.pop(handle);state['sdk_reads'].append(('toolhelp_close',handle));return 1
        parent_exports.GetCurrentProcessId=Function(os.getpid)
        parent_exports.GetProcessId=Function(lambda handle:row(handle)['pid'])
        parent_exports.CreateToolhelp32Snapshot=Function(snapshot)
        parent_exports.Process32FirstW=Function(lambda h,out:process_row(h,out,first=True))
        parent_exports.Process32NextW=Function(lambda h,out:process_row(h,out,first=False))
        parent_exports.CloseHandle=Function(close_snapshot);parent_exports.GetLastError=Function(lambda:18)
        state['parent_exports']=parent_exports;state['parent_snapshots']=snapshots
        def resumed(thread):
            state['events'].append('RESUME')
            fault=getattr(self.env,'_source_file_resume_error',None)
            if key=='files' and fault is not None:
                raise fault  # Exact inert SDK entry; no coordinator/issuer replacement.
            record=row(thread);inputs=record['inputs'];command=inputs.argv
            if inputs.probe_input is None:
                Path=self.env.Path
                if '--endpoint-file' in command:
                    ready=dict(type='ready',host='127.0.0.1',port=record['child']._original_factory.network_intent.worker_listen_port,
                        pid=record['pid'],process_start_epoch_ms=record['start_ms'],generation=1,instance_id=self.instance,
                        token=inputs.environment['COMSOL_MCP_WORKER_TOKEN'])
                    Path(command[command.index('--endpoint-file')+1]).write_text(json.dumps(ready))
                    self.worker_record=record
                elif '-portfile' in command:
                    Path(command[command.index('-portfile')+1]).write_text(str(record['child']._original_factory.network_intent.server_listen_port))
            return 1
        api.k.ResumeThread=resumed
        self.install_legacy_token_exports(api,state)
        return api,state

    def install_legacy_token_exports(self,api,state):
        original_info=api.a.GetTokenInformation
        strings={}
        def info(token,kind,buffer,capacity,used):
            h=getattr(token,'value',token)
            if h!=58:return original_info(token,kind,buffer,capacity,used)
            base=C.addressof(buffer) if buffer is not None else 0
            if kind==31:
                raw=struct.pack('<Q',base+8)+_sid(self.env.profile.appcontainer_sid)
            else:
                snapshot=child_from_actual_input(api,self.env.profile)
                raw=encode(snapshot,kind,base)
            C.cast(used,C.POINTER(evidence.U32))[0]=len(raw)
            if buffer is None:state['query'].last_error=122;return 0
            assert capacity>=len(raw);C.memmove(buffer,raw,len(raw));return 1
        def text(pointer,out):
            raw=C.string_at(pointer,8);count=raw[1]
            value='S-'+str(raw[0])+'-'+str(int.from_bytes(raw[2:8],'big'))
            tail=C.string_at(pointer,8+4*count)
            value+=''.join('-'+str(int.from_bytes(tail[8+i*4:12+i*4],'little')) for i in range(count))
            storage=C.create_unicode_buffer(value);address=C.addressof(storage);strings[address]=storage
            C.cast(out,C.POINTER(evidence.PTR))[0]=address;return 1
        api.a.GetTokenInformation=info
        api.a.IsValidSid=lambda pointer:1
        api.a.GetLengthSid=lambda pointer:8+4*C.string_at(pointer,2)[1]
        api.a.ConvertSidToStringSidW=text
        old_free=api.k.LocalFree
        def free(pointer):
            key=getattr(pointer,'value',pointer)
            if key in strings:strings.pop(key);return None
            return old_free(pointer)
        api.k.LocalFree=free
        api.read_token=win._Win32ReadOnlyApi.read_token.__get__(api)

    def request(self,child):
        owner=child._launch_inputs.purpose_owner
        return wire.decode_request(child._launch_inputs.probe_input.frame,expected_nonce=owner.nonce,
            expected_recipe_sha256=owner.operation.recipe.sha256)

    def finish_response(self,child,state,request,rows):
        reply=dict(version=1,nonce=request['nonce'],recipe_sha256=request['recipe_sha256'],frame_status='PARSED',exception_class='',
            stdin=dict(acquisition='RETURNED',close='RETURNED',close_exception_class=''),output_observation='UNVERIFIED_SELF_OUTPUT',records=rows)
        raw=wire.encode_response(reply);child._launch_inputs.stdio[1].write(raw);child._launch_inputs.stdio[1].flush()
        next(x for x in state['records'] if x['child'] is child)['alive']=False

    def make_file_records(self,child,state):
        request=self.request(child);rows=[]
        for record in request['records']:
            obs=wire.empty_observation(record['index'],record['operation']);obs['entered']=True
            if record['operation']=='READ_HASH':
                raw=self.env.Path(record['path']).read_bytes()
                obs.update(phase='DONE',body_returned=True,read_count=len(raw),read_sha256=hashlib.sha256(raw).hexdigest(),eof_observed=True)
                obs['input'].update(acquisition='RETURNED',close='RETURNED')
            elif record['index']==1:
                payload=wire.create_payload(request['nonce'],record['index']);path=record['path']
                self.env.Path(path).write_bytes(payload);parent=self.env.backend.tree[ntpath.dirname(path)]
                node=self.env.backend.add(path,False,payload,sd=parent['sd'])
                self.env.sdk.nodes[ntpath.normcase(path)]=node;self.env.sdk.contents[ntpath.normcase(path)]=payload
                state['directory_changed']=ntpath.normcase(ntpath.dirname(path))
                obs.update(phase='DONE',body_returned=True,write_count=len(payload),write_sha256=hashlib.sha256(payload).hexdigest())
                obs['output'].update(acquisition='RETURNED',close='RETURNED')
            else:
                obs.update(phase='OPEN',exception_class='java.nio.file.AccessDeniedException');obs['output']['acquisition']='THREW'
            rows.append(obs)
        self.finish_response(child,state,request,rows)

    def make_network_records(self,child,state,*,complete):
        request=self.request(child);payload=wire.echo_payload(request['nonce'],0)
        state['echo_payload']=payload;state['echo_port']=40001
        if not complete:return
        rows=[];digest=hashlib.sha256(payload).hexdigest()
        for record in request['records']:
            obs=wire.empty_observation(record['index'],'TCP_ECHO');obs['entered']=True
            obs['socket'].update(acquisition='RETURNED',close='RETURNED')
            obs['local_endpoint']=dict(address=b'\x7f\0\0\1',port=40001+record['index'])
            if record['index']==0:
                obs.update(phase='DONE',body_returned=True,echo_matches=True,read_count=len(payload),write_count=len(payload),
                    read_sha256=digest,write_sha256=digest,remote_endpoint=dict(address=record['address'],port=record['port']))
            else:
                obs.update(phase='CONNECT',exception_class='java.net.ConnectException')
                stage=child._launch_inputs.purpose_owner.stage
                policy=self.policy_snapshot(stage.owner.profile)
                block=next(r for r in policy['filters'] if r['action_type']==0x1001 and
                    r['layer_key']=='{C38D57D1-05A7-4C33-904F-7FBCEEE60E82}')
                layer=next(r for r in policy['layers'] if r['layer_key']==block['layer_key'])
                event=event_fixture._make_event(timestamp=self.clock+1,app_id=b'public-synthetic-app-id',
                    user_sid=_sid(self.env.profile.service_account_sid),package_sid=_sid(self.env.profile.appcontainer_sid),
                    local_port=40002,remote_port=record['port'],filter_id=block['filter_id'],layer_id=layer['layer_id'])
                self.events.append(event)
            rows.append(obs)
        self.finish_response(child,state,request,rows)

    def rpc(self,request):
        self.rpc_requests.append(dict(request));kind=request['type']
        if kind=='health':return dict(ok=True,status='HEALTHY',generation=self.generation,instance_id=self.instance,
            connected=self.connected,server=self.server_endpoint() if self.connected else '')
        if kind=='shutdown':
            self.worker_record['alive']=False;return dict(ok=True,status='SHUTTING_DOWN')
        if kind=='connect':self.connected=True;self.generation+=1
        elif kind=='disconnect':self.connected=False;self.generation+=1
        else:raise AssertionError('unapproved inert Worker command '+kind)
        return dict(ok=True,status='SUCCEEDED',request_id=request['request_id'],generation=self.generation,
            result=dict(connected=self.connected,generation=self.generation,instance_id=self.instance,
                server=self.server_endpoint() if self.connected else '',engine_version=self.observed_version))

    def server_endpoint(self):
        from comsol_mcp._windows_t037_network import derive_session_network_intent
        intent=derive_session_network_intent(self.env.profile)
        return intent.bind_address+':'+str(intent.server_listen_port)

    def install_policy_sdk(self):
        from tests._controller_c_wfp_sdk import install
        install(self)

    def install_event_sdk(self):
        def initialize(api):
            exported=event_fixture._FixtureApi()
            exported.net_events=self.events
            api._software_event_exports=exported
            for name in ('FwpmEngineOpen0','FwpmEngineClose0','FwpmEngineGetOption0','FwpmNetEventCreateEnumHandle0',
                         'FwpmNetEventEnum2','FwpmNetEventDestroyEnumHandle0','FwpmFreeMemory0'):
                setattr(api,name,Function(getattr(exported,name)))
            def appid(path,out):
                raw=C.create_string_buffer(b'public-synthetic-app-id')
                blob=event_source.FWP_BYTE_BLOB(23,C.cast(raw,C.POINTER(C.c_ubyte)))
                pointer=C.pointer(blob);exported.allocations.extend((raw,blob,pointer))
                C.cast(out,C.POINTER(C.POINTER(event_source.FWP_BYTE_BLOB)))[0]=pointer;return 0
            api.FwpmGetAppIdFromFileName0=Function(appid)
            def clock(out):
                self.clock+=100
                value=event_source._filetime_from_value(self.clock)
                C.memmove(out,C.byref(value),C.sizeof(value))
            api._get_system_time=Function(clock)
            api._is_valid_sid=Function(lambda pointer:1)
            api._get_length_sid=Function(lambda pointer:8+4*C.string_at(pointer,2)[1])
            api.sdk_metadata=(('fixture','SOFTWARE_ONLY_INERT_WFP_EXPORTS'),)
        self.env.monkeypatch.setattr(event_source.CtypesWindowsNetEventApi,'__init__',initialize)

    def install_dispatch(self,*,project_api,project_state):
        env=self.env;m=env.monkeypatch
        self.states[id(project_api)]=project_state;self.apis['project']=project_api
        from comsol_mcp import _windows_t037_targets as targets
        def current_factory():
            participant,thread=env.daemon._windows_t037_scm_runtime._profile_gate.activity
            factory=participant.factory
            assert env.daemon._session_t037_process_factories[participant.key] is factory
            return factory
        def construct(cls):
            factory=current_factory();owner=q.require_purpose(factory)
            key=(owner.stage.kind if owner.purpose is q.Purpose.QUALIFY_OS else
                 'vendor' if owner.purpose is q.Purpose.QUALIFY_VENDOR else 'project')
            api=self.apis[key]
            assert cls is win._Win32LaunchApi and factory._api is None and factory._job is None
            assert id(factory) not in self.by_factory
            self.by_factory[id(factory)]=(api,self.states[id(api)])
            if key=='network':self.network=self.states[id(api)]
            if key=='project':env.factory=factory;self.project_factories.append(factory)
            return api
        m.setattr(win._Win32LaunchApi,'__new__',staticmethod(construct))
        m.setattr(win._Win32LaunchApi,'__init__',lambda api:None)
        m.setattr(child_source,'NativeChildProcessApi',original._REAL_PARENT_QUERY_CLASS)
        m.setattr(child_source,'os',NS(**dict(vars(os),name='nt')))
        def parent_dll(name,**options):
            assert name=='kernel32'
            return self.states[id(current_factory()._api)]['parent_exports']
        m.setattr(child_source,'C',NS(**dict(vars(C),WinDLL=parent_dll)))
        def evidence_api(api,owner_api=None):
            assert owner_api in self.apis.values()
            self.states[id(owner_api)]['query_initializer'](api,owner_api)
        m.setattr(evidence.WindowsChildEvidenceApi,'__init__',evidence_api)
        def dacl(profile,process,token,pid,api):
            return self.states[id(api)]['acl_gate'](profile,process,token,pid,api)
        m.setattr(acl,'check_suspended_child_dacl',dacl)
        m.setattr(resume,'issue_native_resume',REAL_ISSUER);m.setattr(resume,'resume_native_child',REAL_RESUME)
        # Parent socket and Worker transport exports only; all original protocol
        # and owned I/O/issuer bodies remain in production.
        EchoSocket.selected=self
        m.setattr(qualification.socket,'socket',EchoSocket)
        m.setattr(java.socket,'create_connection',lambda address,**options:RpcSocket(self,address))
        def identity(pid):
            records=[r for state in self.states.values() for r in state.get('records',[]) if r['pid']==pid]
            # Qualifier uses one Server/Worker API; PROJECT is not born until
            # its qualifier children have exited. Select the actual live PID row.
            live=[r for r in records if r['alive']]
            record=live[-1] if live else records[-1] if records else None
            return dict(alive=bool(record and record['alive']),start_epoch_ms=record['start_ms'] if record else None)
        self.identity=identity
        m.setattr(java,'process_identity',identity)
        def listeners(port,**options):
            return [(row['pid'],CanonicalSocket('127.0.0.1',port)) for state in self.states.values()
                    for row in state.get('records',[]) if row['alive'] and row['assigned'] and
                    port==row['child']._original_factory.network_endpoint(row['child']._launch_role).port]
        self.listeners=listeners;m.setattr(server,'listener_rows',listeners)


def bind_host_apply_sdk(env,driver):
    """Normal SCM subject/parser + userenv call, with concrete inert SDK leaves."""
    m=env.monkeypatch
    service=ServiceSDK(3);driver.service_sdk=service
    service.account=env.profile.windows_policy.service_start_name
    service.argv=list(env.installer.intent.argv)
    service.image_path=service.argv[0]
    service.binary=subprocess.list2cmdline(service.argv)
    from comsol_mcp import _windows_service_sid_native as service_source
    def config(handle,buffer,capacity,used):
        values=[('binary',service.binary),('account',service.account),('load_group',''),
                ('display','Bounded source-only service'),('dependencies','\0')]
        required=C.sizeof(service_source.Config)+sum(len((value+'\0').encode('utf-16-le')) for _,value in values)
        assert required<=8192
        C.cast(used,C.POINTER(evidence.U32))[0]=required
        if buffer is None:return 0
        assert capacity>=required
        row=service_source.Config.from_buffer(buffer);row.service_type=16;row.start_type=3;row.error_control=1;row.tag=0
        offset=C.sizeof(service_source.Config)
        for field,value in values:
            raw=(value+'\0').encode('utf-16-le');C.memmove(C.addressof(buffer)+offset,raw,len(raw))
            setattr(row,field,C.addressof(buffer)+offset);offset+=len(raw)
        assert offset==required
        return 1
    service.a.QueryServiceConfigW=Function(config)
    def lookup(system,account,out,count,domain,domain_count,use):
        text=env.profile.service_sid if account.startswith('NT SERVICE') else env.profile.service_account_sid
        raw=_sid(text)
        C.cast(count,C.POINTER(evidence.U32))[0]=len(raw)
        C.cast(domain_count,C.POINTER(evidence.U32))[0]=1
        C.cast(use,C.POINTER(evidence.U32))[0]=1
        if out is None:return 0
        C.memmove(out,raw,len(raw));domain.value='';return 1
    service.a.LookupAccountNameW=Function(lookup)
    keep=[];user_calls=[];driver.userenv_calls=user_calls
    def sid(text):
        raw=_sid(text);holder=C.create_string_buffer(raw);keep.append(holder);return C.addressof(holder)
    def convert(text,out):C.cast(out,C.POINTER(evidence.PTR))[0]=sid(text);return 1
    def create(name,display,description,caps,count,out):
        assert name==display==description==env.profile.windows_policy.appcontainer_profile_name
        assert count==len(env.profile.capability_sids)
        assert all(caps[i].attributes==4 for i in range(count))
        journal=env.daemon.store.db.execute("SELECT metadata FROM job_events WHERE event='HostControlMutationJournalV2' ORDER BY id DESC LIMIT 1").fetchone()
        assert json.loads(journal[0])['event']=='APPLY_STEP_INTENT'
        # Original ProfileEffect/native attempt is rooted by the actual daemon
        # before this first SDK call; no source/validator/constructor bypass.
        effect=list(env.daemon._retained_appcontainer_profile_effects.values())[-1]
        effect.require_rooted(effect.api)
        assert effect._native_attempt.entered
        user_calls.append('CreateAppContainerProfile')
        C.cast(out,C.POINTER(evidence.PTR))[0]=sid(env.profile.appcontainer_sid);return 0
    userenv=NS(CreateAppContainerProfile=Function(create))
    service.a.ConvertStringSidToSidW=Function(convert)
    service.a.FreeSid=Function(lambda pointer:user_calls.append('FreeSid'))
    service.a.IsValidSid=Function(lambda pointer:1)
    service.a.GetLengthSid=Function(lambda pointer:8+4*C.string_at(pointer,2)[1])
    service.a.OpenThreadToken=Function(lambda thread,rights,as_self,out:0)
    service.k.GetCurrentThread=Function(lambda:C.c_void_p(-2).value)
    service.k.GetLastError=Function(lambda:1008)
    service.k.CloseHandle=Function(lambda handle:1)
    # Raw filesystem security bytes; the original preflight/reader owners root
    # and parse their actual output slots and release them through captured SDK.
    def security(handle,kind,flags,owner,group,dacl,sacl,out):
        key=env.sdk.handles[getattr(handle,'value',handle)]
        from tests.test_windows_t037_targets import label_descriptor,label_ace
        raw=label_descriptor(label_ace(0x2000)) if flags==0x10 else env.sdk.nodes[key]['sd']
        storage=C.create_string_buffer(raw);pointer=C.addressof(storage);env.sdk.buffers[pointer]=(storage,raw)
        C.cast(out,C.POINTER(evidence.PTR))[0]=pointer;env.sdk.sd_slots.append(pointer);return 0
    env.sdk.a.GetSecurityInfo=Function(security)
    # Source/profile loader and file observers keep their own concrete path SDK.
    def dll(name,**options):
        name=name.casefold().removesuffix('.dll')
        if name=='shell32':return service.shell
        if name=='userenv':return userenv
        if name=='advapi32':return env.sdk.a
        if name=='ntdll':return env.sdk.nt
        if name=='kernel32':return env.sdk.k
        raise AssertionError('no other DLL SDK boundary is permitted: '+name)
    m.setattr(C,'WinDLL',dll,raising=False)
    def initialize(adapter):
        policy=native.CtypesWindowsPolicyApi.__new__(native.CtypesWindowsPolicyApi)
        policy._advapi,policy._kernel32=service.a,service.k
        adapter.api=policy;adapter._advapi,adapter._kernel32=service.a,service.k
        adapter._fwpu=NS();registry_for(adapter)
    m.setattr(changes.CtypesWindowsWfpMutationApi,'__init__',initialize)
    m.setattr(changes,'os',NS(**dict(vars(os),name='nt')))
    m.setattr(changes,'socket',NS(gethostname=lambda:env.profile.host_resource_profile.host_id))
    bind_token_sdk(m,env);bind_directory_security_sdk(m,env)
    return service



def bind_current_process_sdk(env,driver):
    """Run both original current-process observers; only Win32 exports are fake."""
    from comsol_mcp import _windows_appcontainer_lifecycle as lifecycle
    from comsol_mcp import _platform_process as platform_source
    import sys
    m=env.monkeypatch;source=driver.apis['files'];calls=[]
    driver.current_process_sdk_calls=calls
    handle_value=0x770001
    def open_process(access,inherit,pid):
        assert pid==os.getpid() and not inherit
        calls.append('OpenProcess');return handle_value
    def times(handle,birth,ended,kernel,user):
        assert getattr(handle,'value',handle)==handle_value
        birth._obj.dwLowDateTime=(BIRTH-100)&0xffffffff;birth._obj.dwHighDateTime=(BIRTH-100)>>32
        ended._obj.dwLowDateTime=0;ended._obj.dwHighDateTime=0
        calls.append('GetProcessTimes');return 1
    def image(handle,flags,storage,used):
        assert getattr(handle,'value',handle)==handle_value and flags==0
        storage.value=env.profile._launch_image('launcher').path;used._obj.value=len(storage.value);return 1
    def job(handle,original,out):assert original is None;out._obj.value=0;return 1
    def code(handle,out):out._obj.value=259;return 1
    def close(handle):assert getattr(handle,'value',handle) in (handle_value,55);calls.append('CloseHandle');return 1
    kernel=NS(OpenProcess=Function(open_process),GetProcessTimes=Function(times),
        QueryFullProcessImageNameW=Function(image),IsProcessInJob=Function(job),GetExitCodeProcess=Function(code),
        CloseHandle=Function(close),GetCurrentProcess=Function(lambda:1),LocalFree=source.k.LocalFree)
    def open_token(process,access,out):
        assert getattr(process,'value',process) in (1,handle_value) and access==8
        C.cast(out,C.POINTER(evidence.PTR))[0]=55;calls.append('OpenProcessToken');return 1
    def initialize(api):
        api.C=C;api.W=source.W;api.k=kernel
        api.a=NS(OpenProcessToken=Function(open_token),GetTokenInformation=source.a.GetTokenInformation,
            IsValidSid=source.a.IsValidSid,GetLengthSid=source.a.GetLengthSid,
            ConvertSidToStringSidW=source.a.ConvertSidToStringSidW)
        api.volume_snapshot=lambda path:dict(volume_guid_path='SOFTWARE_VOLUME',filesystem='NTFS',
            serial_number=1,flags=8,total_bytes=1024,caller_available_bytes=512,free_bytes=512)
    m.setattr(win._Win32ReadOnlyApi,'__init__',initialize)
    def observer_initialize(observer,*,platform_name=None,api=None):
        return original._REAL_WINDOWS_OBSERVER_INIT(observer,platform_name=platform_name or 'windows',
            api=win._Win32ReadOnlyApi() if api is None else api)
    m.setattr(win.WindowsT037Observer,'__init__',observer_initialize)
    # Independent process_identity executes its real open/query/close body on
    # the same fixed-width values; no identity row or native owner is supplied.
    sdk_dll=C.WinDLL
    global_kernel=env.sdk.k
    old_close=global_kernel.CloseHandle
    for name in ('OpenProcess','GetProcessTimes','GetExitCodeProcess'):
        setattr(global_kernel,name,getattr(kernel,name))
    global_kernel.CloseHandle=Function(lambda handle:close(handle) if getattr(handle,'value',handle)==handle_value else old_close(handle))
    m.setattr(lifecycle,'os',NS(**dict(vars(os),name='nt')))
    m.setattr(lifecycle,'sys',NS(**dict(vars(sys),platform='win32')))


def prepare_controller(env,launcher):
    """Actual public session.start is reached only after original successful APPLY."""
    m=env.monkeypatch
    original_project=(env.api,env.events,env.model,env.query,evidence.WindowsChildEvidenceApi.__init__,
        child_source.NativeChildProcessApi,acl.check_suspended_child_dacl)
    env.factory._api=None;env.factory.close()
    driver=FullSdk(env);env._full_c_software_driver=driver
    # Real machine-identity producer and original PyHKEY/Detach/Close owner;
    # the installer intent was frozen against these public inert SDK values.
    from types import ModuleType
    machine=ModuleType('winreg');machine.HKEY_LOCAL_MACHINE=0x80000002;machine.REG_SZ=1
    machine.calls=[];machine.handles={}
    class Key:
        def __init__(self,value):self.value=value;self.detached=False
        def __int__(self):return self.value
        def Detach(self):
            assert not self.detached;self.detached=True;machine.calls.append(('detach',self.value));return self.value
    def open_key(root,path):
        assert root==machine.HKEY_LOCAL_MACHINE and path==r'SOFTWARE\Microsoft\Cryptography'
        key=Key(0x8800+len(machine.calls));machine.handles[key.value]=key;machine.calls.append(('open',key.value));return key
    def query_key(handle,name):
        assert machine.handles[handle].detached and name=='MachineGuid';machine.calls.append(('query',handle))
        return original._C_SOFTWARE_MACHINE_GUID,machine.REG_SZ
    def close_key(handle):
        assert type(handle) is int and machine.handles[handle].detached
        machine.handles.pop(handle);machine.calls.append(('close',handle))
    machine.OpenKey=open_key;machine.QueryValueEx=query_key;machine.CloseKey=close_key
    m.setitem(sys.modules,'winreg',machine);driver.machine_sdk=machine
    m.setattr(win,'platform',NS(node=lambda:original._C_SOFTWARE_COMPUTER))
    m.setattr(win,'windows_host_fingerprint',original._REAL_HOST_FINGERPRINT)
    for key in ('files','network','vendor'):driver.prepare_api(key)
    project_api,project_state=driver.prepare_api('project',existing=original_project)
    # Missing leaf queries for this qualification's finite marker names are
    # actual failed NTSTATUS+NULL outputs; no exception alone proves absence.
    install_single_api(m,env,None,driver.apis['files'],driver.states[id(driver.apis['files'])])
    # Install the multi-factory selector after all concrete SDK objects exist.
    driver.install_policy_sdk();driver.install_event_sdk()
    driver.install_dispatch(project_api=project_api,project_state=project_state)
    bind_host_apply_sdk(env,driver)
    bind_current_process_sdk(env,driver)
    from comsol_mcp import _managed_stop_origin as stop_origin
    m.setattr(control,'Path',env.Path);m.setattr(stop_origin,'Path',env.Path)
    env.daemon.session_runtime_resolver=lambda runtime_id,*args:replace(env.runtime(),runtime_id=runtime_id)
    env.daemon.session_server_launcher=server.OwnedServerLauncher(
        platform_name='win32',process_identity_reader=driver.identity,
        listener_reader=driver.listeners,ready_timeout_s=2.0,poll_interval_s=0.001)
    runtime=env.daemon._windows_t037_scm_runtime
    future=env.daemon._submit_scm_host_control(runtime,phase='APPLY',plan=env.qualification_plan,
        authorization_ref='qualification-controller')
    receipt=future.result(timeout=240)
    live=qualification.require_live_c(env.daemon,runtime_id='fixture-runtime',live=True)
    assert live.operation.plan is env.qualification_plan and receipt.plan_sha256==env.qualification_plan.sha256
    assert driver.userenv_calls.count('CreateAppContainerProfile')==1
    assert all(stage.accepted and stage.io.state=='RETIRED_SUBMITTED' for stage in live.run.stages)
    assert live.run.vendor_closed and live.run.worker._process is None and live.run.server.retired
    assert {r['kind'] for r in live.run.vendor_calls}=={'connect','disconnect'}
    # The original test's exact Server owner/observers remain its PROJECT SDK
    # model. Normal production launcher/Controller bodies and real issuer are used.
    env.daemon.session_server_launcher=launcher
    (env.tmp_path/'full-c-source-software.json').write_text(json.dumps(dict(
        scope='ORIGINAL_APPLY_COORDINATOR_SOFTWARE_ONLY_NOT_WINDOWS_QUALIFICATION',
        recipe_sha256=live.recipe.sha256,plan_sha256=live.plan.sha256,source_sha256=live.source_sha256,
        operation_id=live.operation.admission.operation_id,job_id=live.operation.admission.job_id,
        C_native='NOT_RUN',issuer_source=REAL_ISSUER.__module__,consumer_source=REAL_RESUME.__module__,
        roles=['server','worker'],os_stages=[s.kind for s in live.run.stages],
        original_qualifier_children=[dict(pid=c.pid,birth=c.birth_filetime_100ns,closed=c._closed,
            resume_state=c._resume_attempt.state) for s in live.run.stages for c in [s.child]]),indent=1)+'\n')
    return env.daemon,env.profile.host_resource_profile.project_id,driver.project_factories
