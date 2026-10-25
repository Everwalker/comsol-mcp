"""Original qualification consumers with explicitly inert OS export values.

No coordinator, issuer, reader, primary preparation or Gate body is replaced.
The SDK fixture owns all simulated process values; no PID is queried/adopted.
"""
from contextlib import contextmanager
from datetime import datetime,timezone
from dataclasses import replace
import ctypes as C
import hashlib
import json
import ntpath
from pathlib import Path
from types import SimpleNamespace as NS
import pytest

from comsol_mcp import _windows_t037 as win
from comsol_mcp import _windows_t037_child_evidence as e
from comsol_mcp import _windows_t037_acl as acl
from comsol_mcp import _windows_t037_resume as resume
from comsol_mcp import _windows_t037_qualification as q
from comsol_mcp import _windows_t037_qualification_run as run
from comsol_mcp import _windows_os_probe_protocol as wire
from comsol_mcp import _java_worker as java,_control_daemon as control
from tests.test_windows_suspended_ownership import _launch_api
from tests.test_windows_t037_connected_b import _QuerySDK
from tests.test_windows_t037_primary_policy import encode,child_from_actual_input
from tests.test_windows_prepared_launch import WINROOT,SYSTEMROOT
from tests.test_windows_host_preflight_owner import Function
from tests.test_windows_t037_qualification_original_owner import original_owner

from tests import _prepared_launch_software_fixture as _fixture_original
REAL_ISSUER=_fixture_original._REAL_NATIVE_ISSUER
REAL_RESUME=_fixture_original._REAL_NATIVE_RESUME
REAL_ACL=_fixture_original._REAL_NATIVE_ACL
BIRTH=(11644473600000+123456)*10000


def materialize_sdk(monkeypatch,env,*,output_fault=None,_provided=None):
    """One actual launch API and one complete original B reader on fake exports."""
    descriptor=bytes.fromhex(next(t.descriptor_hex for t in env.profile.finite_targets.files
        if t.path==env.profile.workspace_root))
    # Capture only the initialization/export seams from the existing SDK model.
    # Restoring the constructor wrappers prevents nested ACL-gate recursion.
    if _provided is None:
        with monkeypatch.context() as m:
            m.setattr(acl,'check_suspended_child_dacl',REAL_ACL)
            _,api,events,model=_launch_api(m,profile_override=env.profile,acl_descriptor=descriptor)
            query=_QuerySDK(m,api,env.profile,env.sdk)
            query_initializer=e.WindowsChildEvidenceApi.__init__
            native_child_constructor=__import__('comsol_mcp._windows_t037_child',fromlist=['NativeChildProcessApi']).NativeChildProcessApi
            acl_gate=acl.check_suspended_child_dacl
    else:
        api,events,model,query,query_initializer,native_child_constructor,acl_gate=_provided
    state=dict(birth=BIRTH,image=None,current=None,children=[],events=events,model=model,query=query,
        query_initializer=query_initializer,native_child_constructor=native_child_constructor,acl_gate=acl_gate)
    api._controller_c_software_state=state
    original_full=env.sdk.k.GetFileInformationByHandleEx
    def full(handle,kind,out,size):
        returned=original_full(handle,kind,out,size)
        path=env.sdk.handles[getattr(handle,'value',handle)]
        if state.get('directory_changed') and kind==0 and path==state['directory_changed']:
            from comsol_mcp._windows_parameter_publish import _BasicInfo
            C.cast(out,C.POINTER(_BasicInfo)).contents.changed+=1
        return returned
    env.sdk.k.GetFileInformationByHandleEx=Function(full)
    api.hash_image=lambda path:hashlib.sha256(env.Path(path).read_bytes()).hexdigest()
    base_image=api._open_image
    def image(path):
        state['image']=path
        result=list(base_image(path))
        fp=((1,2),len(env.Path(path).read_bytes()),10,12,1)
        result[0].source_fingerprint=lambda h:fp
        result[3]=fp
        return tuple(result)
    api._open_image=image
    api._hash_image_handle=lambda *_:hashlib.sha256(env.Path(state['image']).read_bytes()).hexdigest()
    api._environment_buffer=win._Win32LaunchApi._environment_buffer.__get__(api)
    api.k.GetSystemWindowsDirectoryW=lambda b,n:env._root(b,WINROOT)
    api.k.GetSystemDirectoryW=lambda b,n:env._root(b,SYSTEMROOT)
    api.process_times=lambda h:(10,None) if getattr(h,'value',h)==777 else (state['birth'],None if model['alive'] else state['birth']+100)
    api.process_image_path=lambda h:env.profile._launch_image('launcher').path if getattr(h,'value',h)==777 else state['image']
    api.poll_process=lambda h:None if model['alive'] else 0
    def wait(h,timeout,args):
        pending=state.pop('pending_probe',None)
        if pending is not None:pending()
        if model['alive']:raise TimeoutError('inert child has not reached its modeled exit')
        return 0
    api.wait_process=wait
    def process_times(h,birth,*tail):C.cast(birth,C.POINTER(e.U64)).contents.value=state['birth'];return 1
    def thread_times(h,birth,*tail):C.cast(birth,C.POINTER(e.U64)).contents.value=state['birth']+10;return 1
    query.process_times=process_times;query.thread_times=thread_times
    query.token_bytes=lambda kind,base,read=False:encode(child_from_actual_input(api,env.profile),kind,base)
    def process_name(h,flags,storage,used):
        from tests.test_windows_t037_connected_b import _text
        text=query.native if flags else state['image']
        C.cast(used,C.POINTER(e.U32)).contents.value=_text(storage,text);return 1
    query.process_name=process_name
    base_method=query.method
    def method(p,index,result,*types):
        if p.value==9204 and index==4:
            def get(this,name,flags,out,typ,flavor):
                value=C.cast(out,C.POINTER(e._Variant)).contents
                if name=='ProcessId':value.vt,value.data[0]=3,456
                elif name=='ExecutablePath':value.vt,value.data[0]=1,0
                else:
                    text=query.raw_command if name=='CommandLine' else '19700101000203.456000+000'
                    value.vt,value.data[0]=8,query.allocate(text)
                return 0
            return get
        return base_method(p,index,result,*types)
    query.method=method
    create=api.a.CreateProcessAsUserW
    def created(*args):
        returned=create(*args)
        child=next(reversed(api._retained_children.values()));state['current']=child;state['children'].append(child)
        query.payload=env.Path(state['image']).read_bytes()
        return returned
    api.a.CreateProcessAsUserW=created
    def execute_probe(child):
        inputs=child._launch_inputs
        if inputs.probe_input is not None:
            request=wire.decode_request(inputs.probe_input.frame,expected_nonce=inputs.purpose_owner.nonce,
                expected_recipe_sha256=inputs.purpose_owner.operation.recipe.sha256)
            rows=[]
            for record in request['records']:
                row=wire.empty_observation(record['index'],record['operation']);row['entered']=True
                if record['operation']=='READ_HASH':
                    raw=env.Path(record['path']).read_bytes()
                    row.update(phase='DONE',body_returned=True,read_count=len(raw),read_sha256=hashlib.sha256(raw).hexdigest(),eof_observed=True)
                    row['input'].update(acquisition='RETURNED',close='RETURNED')
                elif record['operation']=='CREATE_NEW':
                    payload=wire.create_payload(request['nonce'],record['index'])
                    if record['index']==1:
                        path=record['path'];actual=env.Path(path);actual.write_bytes(payload)
                        parent=env.backend.tree[ntpath.dirname(path)]
                        node=env.backend.add(path,False,payload,sd=parent['sd'])
                        env.sdk.nodes[ntpath.normcase(path)]=node;env.sdk.contents[ntpath.normcase(path)]=payload
                        state['directory_changed']=ntpath.normcase(ntpath.dirname(path))
                        row.update(phase='DONE',body_returned=True,write_count=len(payload),write_sha256=hashlib.sha256(payload).hexdigest())
                        row['output'].update(acquisition='RETURNED',close='RETURNED')
                    else:
                        row.update(phase='OPEN',exception_class='java.nio.file.AccessDeniedException')
                        row['output']['acquisition']='THREW'
                else:raise AssertionError('network export model is not selected in this leaf unit')
                rows.append(row)
            if output_fault=='extent':rows[0]['read_count']+=1
            reply=dict(version=1,nonce=request['nonce'],recipe_sha256=request['recipe_sha256'],frame_status='PARSED',exception_class='',
                stdin=dict(acquisition='RETURNED',close='RETURNED',close_exception_class=''),output_observation='UNVERIFIED_SELF_OUTPUT',records=rows)
            raw=wire.encode_response(reply)
            inputs.stdio[1].write(raw);inputs.stdio[1].flush();model['alive']=False
    def resumed(thread):
        events.append('RESUME')
        child=state['current']
        if output_fault=='directory_time':execute_probe(child)
        else:state['pending_probe']=lambda:execute_probe(child)
        return 1
    api.k.ResumeThread=resumed
    return api,state


def install_single_api(monkeypatch,env,factory,api,state):
    constructed=[]
    def construct(cls):
        actual=env.daemon._windows_t037_scm_runtime._profile_gate.activity[0].factory
        assert cls is win._Win32LaunchApi and not constructed and (factory is None or actual is factory)
        assert actual._api is None and actual._job is None
        constructed.append(actual)
        return api
    monkeypatch.setattr(win._Win32LaunchApi,'__new__',staticmethod(construct))
    monkeypatch.setattr(win._Win32LaunchApi,'__init__',lambda self:None)
    monkeypatch.setattr(e.WindowsChildEvidenceApi,'__init__',state['query_initializer'])
    monkeypatch.setattr('comsol_mcp._windows_t037_child.NativeChildProcessApi',state['native_child_constructor'])
    monkeypatch.setattr(acl,'check_suspended_child_dacl',state['acl_gate'])
    monkeypatch.setattr(resume,'issue_native_resume',REAL_ISSUER)
    monkeypatch.setattr(resume,'resume_native_child',REAL_RESUME)
    monkeypatch.setattr(control,'Path',env.Path)
    env.daemon.session_runtime_resolver=lambda runtime_id,*_:replace(env.runtime(),runtime_id=runtime_id)
    original_security=env.sdk.a.GetSecurityInfo
    def security(handle,kind,flags,owner,group,dacl,sacl,output):
        from comsol_mcp import _windows_t037_targets as targets
        address=C.addressof(C.cast(output,C.POINTER(C.c_void_p)).contents)
        matching=[]
        for reader in tuple(targets._READS.values()):
            holder=reader.mic.pending if flags==0x10 else getattr(reader.mic,'_finite_pending_sd',None)
            if holder is not None and C.addressof(holder.storage)==address:matching.append(reader)
        assert len(matching)==1
        env.sdk.api=matching[0].mic;env.sdk.path_api=matching[0].api
        return original_security(handle,kind,flags,owner,group,dacl,sacl,output)
    env.sdk.a.GetSecurityInfo=Function(security)
    normal=env.sdk.nt.NtCreateFile
    def relative(output,access,attributes,*args):
        value=C.cast(attributes,C.POINTER(__import__('comsol_mcp._windows_parameter_publish',fromlist=['_ObjectAttributes'])._ObjectAttributes)).contents
        name=value.ObjectName.contents;leaf=C.wstring_at(name.Buffer,name.Length//2)
        path=ntpath.join(env.sdk.handles[value.RootDirectory],leaf)
        if ntpath.normcase(path) not in env.sdk.nodes:
            return -1073741772
        return normal(output,access,attributes,*args)
    env.sdk.nt.NtCreateFile=Function(relative)


@contextmanager
def first_os_stage(monkeypatch,tmp_path,*,output_fault=None):
    """Internal complete first-stage unit, NOT full APPLY or live-C acceptance."""
    with original_owner(monkeypatch,tmp_path,fixed_probe=True,callback_hold_seconds=120) as value:
        env=value.env;op=value.operation
        env.factory._api=None;env.factory.close()
        api,state=materialize_sdk(monkeypatch,env,output_fault=output_fault)
        op.state='RUNNING';op.coordinator=run.OriginalQualificationRun.__new__(run.OriginalQualificationRun)
        run.OriginalQualificationRun.__init__(op.coordinator,op);op.coordinator.state='RUNNING'
        stage=op.coordinator.stages[0]
        install_single_api(monkeypatch,env,None,api,state)
        yield value,stage,api,state
