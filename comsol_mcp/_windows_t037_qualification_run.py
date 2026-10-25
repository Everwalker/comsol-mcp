"""Original bounded qualification execution. No observation dictionary is authority.

Only the admitted APPLY coordinator creates these stages and transient parents.
All SDK, file, transport and vendor operations run outside Gate/issuer/Store locks.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import ipaddress
import ntpath
import os
import socket
import threading
import time
from uuid import uuid4

from . import _windows_t037_qualification as q
from . import _windows_os_probe_protocol as wire
from ._execution_contract import ExecutionContractError
from ._windows_owned_resources import registry_for

_SEAL=object()
_RUNS={}
_STAGES={}
_LIVE={}


def fail(message): q._fail('T037_QUALIFICATION_UNVERIFIED',message)


class OriginalOsStage:
    def __init__(self,run,kind,*,seal):
        if seal is not _SEAL or kind not in ('files','network'): fail('original fixed OS stage required')
        self.run,self.kind,self._seal=run,kind,seal
        self.factory=None;self.owner=None;self.io=None;self.inputs=None;self.child=None
        self.listener=None;self.response=None;self.parents=[];self.event_api=None;self.events=None
        self.frame=None;self.request=None;self.start=None;self.end=None;self.accepted=False
        _STAGES[id(self)]=self

    def check(self,operation):
        if (self._seal is not _SEAL or _STAGES.get(id(self)) is not self
                or self.run.operation is not operation or _RUNS.get(id(operation)) is not self.run
                or self not in self.run.stages or self.run.state!='RUNNING'):
            fail('same original running fixed stage required')
        operation.check()  # pure active original owner; phase entry/issuer recheck live admission


class OriginalEchoParent:
    """One original loopback listener and one returned connection, never fd adoption."""
    def __init__(self,stage):
        self.stage=stage;self.thread=threading.get_ident();self.creator=socket.socket
        self.calls=[];self.slots=[];self.listener=None;self.connection=None;self.closed=False
        self.first_error=None;self.registry=registry_for(self);self.peer=None;self.echo=None

    def check(self):
        self.stage.check(self.stage.run.operation)
        if (self.thread!=threading.get_ident() or self.stage.listener is not self
                or self.first_error is not None or registry_for(self) is not self.registry
                or self.registry.owner is not self): fail('original echo parent/thread changed')

    def call(self,obj,name,*args):
        self.check();r=dict(object=obj,function=getattr(obj,name),arguments=args,entered=False,returned=None)
        self.calls.append(r);r['entered']=True
        try:
            r['returned']=r['function'](*args);self.check();return r['returned']
        except BaseException as error:
            self.first_error=error;raise self.stage.run.operation.unknown(error)

    def own(self,value,slot):
        slot['returned']=value  # Strong original SDK return precedes fileno/type/transfer work.
        if not isinstance(value,socket.socket): fail('original standard Socket return required')
        slot['closer']=value.close
        def close(fd):
            self.check()
            if slot.get('entry') is None or slot['entry'].value!=fd or slot['close_entered']:
                fail('original socket close binding changed')
            slot['close_entered']=True;slot['close_return']=slot['closer']();self.check()
            if slot['close_return'] is not None or value.fileno()!=-1: fail('socket close not confirmed')
        slot['entry']=self.registry.acquire(self,value,close,mode='VOID',label='qualification_echo',
            domain='qualification_parent_socket',keepalive=(slot,value,slot['closer']),value_reader=lambda v:v.fileno())
        return value

    def prepare(self,address,port):
        self.check();slot=dict(function=self.creator,arguments=(socket.AF_INET,socket.SOCK_STREAM),
            entered=False,returned=None,closer=None,entry=None,close_entered=False,close_return=None)
        self.slots.append(slot)
        try:
            slot['entered']=True;slot['returned']=self.creator(*slot['arguments']);self.check()
            self.listener=self.own(slot['returned'],slot)
            self.call(self.listener,'settimeout',2.0)
            self.call(self.listener,'bind',(address,port))
            if self.call(self.listener,'getsockname')!=(address,port): fail('actual parent bind tuple changed')
            self.call(self.listener,'listen',1)
        except BaseException as error:
            self.first_error=error;raise self.stage.run.operation.unknown(error)

    def serve(self,index):
        self.check()
        slot=dict(function=self.listener.accept,arguments=(),entered=False,returned=None,
            closer=None,entry=None,close_entered=False,close_return=None)
        self.slots.append(slot)
        try:
            slot['entered']=True;slot['raw_return']=slot['function']();self.check()
            raw=slot['raw_return']
            if type(raw) is not tuple or len(raw)!=2: fail('original accept return malformed')
            self.connection=self.own(raw[0],slot);self.peer=raw[1]
            if (type(self.peer) is not tuple or len(self.peer)!=2 or self.peer[0]!='127.0.0.1'
                    or type(self.peer[1]) is not int or not 1<=self.peer[1]<=65535): fail('actual echo peer malformed')
            payload=wire.echo_payload(self.stage.owner.nonce,index)
            actual=b'';deadline=time.monotonic()+2.0
            while len(actual)<len(payload):
                remaining=deadline-time.monotonic()
                if remaining<=0: fail('finite original echo budget exhausted')
                self.call(self.connection,'settimeout',remaining)
                part=self.call(self.connection,'recv',len(payload)-len(actual))
                if type(part) is not bytes or not part: fail('complete original echo payload unavailable')
                actual+=part
            if actual!=payload: fail('original echo request differs')
            if self.call(self.connection,'sendall',payload) is not None: fail('echo send not confirmed')
            self.echo=actual
        except BaseException as error:
            self.first_error=error;raise self.stage.run.operation.unknown(error)

    def close(self):
        self.check()
        if self.closed: fail('original echo close is one-shot')
        try:
            for slot in reversed(self.slots): slot['entry'].release_owned(self)
            if self.registry.unresolved(): fail('echo handles remain owned')
            self.closed=True
        except BaseException as error:
            self.first_error=error;raise self.stage.run.operation.unknown(error)


class OriginalParentReadback:
    """The existing noncreating reader plus its actual last NT output/status."""
    def __init__(self,stage,path):
        from ._windows_t037_targets import NativeTargetReader
        self.stage,self.path=stage,path
        self.reader=NativeTargetReader.__new__(NativeTargetReader)
        NativeTargetReader.__init__(self.reader,stage.factory)
        self.fact=None;self.missing=None;self.error=None;self.retired=False

    def read(self):
        self.stage.check(self.stage.run.operation)
        try:
            self.fact=self.reader.read(self.path,directory=False,content=True)
        except ExecutionContractError as error:
            self.error=error
            from ._windows_t037_targets import _OriginalRelativeOpen
            call=getattr(self.reader.api,'_last_relative_open',None)
            if (type(call) is not _OriginalRelativeOpen or error is not call.error
                    or not call.known_absent(self.reader.api,ntpath.basename(self.path))):
                raise self.stage.run.operation.unknown(error)
            self.missing=call
        except BaseException as error:
            self.error=error;raise self.stage.run.operation.unknown(error)
        return self

    def close(self):
        self.stage.check(self.stage.run.operation)
        if self.retired: fail('original parent reader cannot finish twice')
        try:
            from ._windows_appcontainer_gate import _factory_activity_scope
            with _factory_activity_scope(self.stage.factory):
                self.reader.finish()
            if not self.reader.retired or any(e.seq>self.reader.scope.seq for e in registry_for(self.stage.factory).unresolved()):
                fail('original parent reader retirement unknown')
            self.retired=True
        except BaseException as error:
            self.error=error;raise self.stage.run.operation.unknown(error)


class OriginalQualificationRun:
    def __init__(self,operation):
        if operation.coordinator is not self or id(operation) in _RUNS: fail('original APPLY coordinator required')
        self.operation=operation;self.thread=threading.get_ident();self.state='NEW';self.first_error=None
        self.stages=(OriginalOsStage(self,'files',seal=_SEAL),OriginalOsStage(self,'network',seal=_SEAL))
        self.vendor_factory=None;self.server=None;self.worker=None;self.vendor_children=[]
        self.runtime=None;self.vendor_calls=[];self.vendor_replies=[];self.vendor_closed=False
        self.live_c=None
        _RUNS[id(operation)]=self

    def check(self):
        if (_RUNS.get(id(self.operation)) is not self or self.operation.coordinator is not self
                or self.thread!=threading.get_ident() or self.first_error is not None
                or self.state not in ('RUNNING','READY')): fail('original coordinator lifetime changed')
        self.operation.check(live=True)

    def runtime_inputs(self,factory):
        from ._java_worker import JavaWorkerPaths
        from ._control_daemon import Path
        op=self.operation;e=op.enrollment
        runtime=e.daemon._resolve_session_runtime(op.recipe.runtime_id,e.profile.host_resource_profile.project_id,
            factory.session_id,Path(e.profile.workspace_root).resolve(strict=True))
        if runtime.runtime_id!=op.recipe.runtime_id: fail('original runtime id changed')
        paths=JavaWorkerPaths(runtime.installation_root,
            runtime.java_executable.parent.parent,runtime.preferences_dir,
            project_root=Path(e.profile.workspace_root),platform_name='nt')
        return runtime,paths

    def parent(self,stage,path):
        value=OriginalParentReadback.__new__(OriginalParentReadback)
        stage.parents.append(value)  # Before constructor/first reader acquisition.
        from ._windows_appcontainer_gate import _factory_activity_scope
        with _factory_activity_scope(stage.factory):
            value.__init__(stage,path)
            return value.read()

    def run_stage(self,stage):
        self.check();op=self.operation
        factory=op.new_factory(q.Purpose.QUALIFY_OS,_control_stage=stage)
        stage.factory=factory;stage.owner=q.require_purpose(factory)
        runtime,paths=self.runtime_inputs(factory)
        profile=factory.profile;manifest=profile.prepared_launch
        from ._windows_t037_targets import require_contract
        from ._windows_t037_network import derive_session_network_intent
        targets={role:t for t in require_contract(profile).files for role in t.roles}
        nonce=stage.owner.nonce
        if stage.kind=='files':
            pin=manifest.jdk_files['java']
            allowed=ntpath.join(targets['temp_leaf'].path,wire.create_basename(nonce,1))
            denied=ntpath.join(targets['code_rx'].path,wire.create_basename(nonce,2))
            records=[wire.read_hash_record(0,pin['source_path'],pin['size']+1),
                dict(index=1,operation='CREATE_NEW',path=allowed),dict(index=2,operation='CREATE_NEW',path=denied)]
            for path in (allowed,denied):
                before=self.parent(stage,path)
                if before.missing is None: fail('finite fresh marker already exists')
                before.close()
        else:
            intent=derive_session_network_intent(profile);address=ipaddress.ip_address(intent.bind_address).packed
            records=[dict(index=0,operation='TCP_ECHO',address=address,port=intent.server_listen_port),
                     dict(index=1,operation='TCP_ECHO',address=address,port=intent.worker_listen_port)]
            stage.listener=OriginalEchoParent(stage)
            stage.listener.prepare(intent.bind_address,intent.server_listen_port)
            from ._windows_t037_events import CtypesWindowsNetEventApi
            stage.event_api=CtypesWindowsNetEventApi.__new__(CtypesWindowsNetEventApi)
            CtypesWindowsNetEventApi.__init__(stage.event_api)
            stage.start=stage.event_api.system_time_filetime()
        stage.request=dict(version=1,nonce=nonce,recipe_sha256=op.recipe.sha256,records=records)
        stage.frame=wire.encode_request(stage.request)
        stage.io=op.prepare_probe_io(factory,stage.frame)
        factory.read_launch_os_roots()
        from ._windows_t037_launch_inputs import build_os_probe_inputs,activate_launch_inputs
        stage.inputs=build_os_probe_inputs(factory,paths,frame=stage.frame,stdio=stage.io.files)
        inputs=stage.inputs
        with activate_launch_inputs(factory,inputs):
            stage.child=factory(inputs.argv,executable=inputs.executable,cwd=inputs.cwd,
                env=dict(inputs.environment),stdin=inputs.stdio[0],stdout=inputs.stdio[1],stderr=inputs.stdio[2],
                text=False,start_new_session=True,creationflags=0)
        stage.io.bind_returned_child(stage.child)
        if stage.listener is not None: stage.listener.serve(0)
        code=stage.child.wait(timeout=5.0)
        if type(code) is not int or code!=0: fail('fixed probe natural exit not successful')
        stage.child.close()
        from ._windows_t037_b_observation import settle_original_child_b
        settle_original_child_b(stage.child)
        stage.response=stage.io.capture_response()
        if stage.kind=='files':
            for path in (allowed,denied):
                self.parent(stage,path).close()
            self.validate_files(stage)
        else:
            stage.end=stage.event_api.system_time_filetime()
            from ._windows_t037_events import observe_recent_classify_drop_events
            stage.events=observe_recent_classify_drop_events(stage.inputs.executable,window_seconds=30,_api=stage.event_api)
            self.validate_network(stage)
            stage.listener.close()
        factory.close()
        stage.io.retire_submitted()
        stage.accepted=True

    @staticmethod
    def base_response(stage):
        response=stage.response.check()
        if (stage.response.exit_code!=0 or stage.response.stderr or response['frame_status']!='PARSED'
                or response['exception_class'] or response['stdin']!=dict(
                    acquisition='RETURNED',close='RETURNED',close_exception_class='')):
            fail('complete parsed original stdin/output/exit required')
        for row in response['records']:
            if not row['entered'] or any(row[name]['close']=='THREW' or row[name]['close_exception_class']
                    for name in ('input','output','socket')): fail('operation or original resource close unverified')
        return response['records']

    def validate_files(self,stage):
        rows=self.base_response(stage);pin=stage.owner.profile.prepared_launch.jdk_files['java']
        read,created,denied=rows
        if (not read['body_returned'] or read['exception_class'] or read['phase']!='DONE'
                or read['read_count']!=pin['size'] or read['read_sha256']!=pin['sha256']
                or not read['eof_observed'] or read['limit_reached']
                or read['input']['acquisition']!='RETURNED' or read['input']['close']!='RETURNED'):
            fail('complete finite prepared file read differs')
        payload=wire.create_payload(stage.owner.nonce,1);parent=stage.parents[2]
        if (not created['body_returned'] or created['exception_class'] or created['phase']!='DONE'
                or created['output']['acquisition']!='RETURNED' or created['output']['close']!='RETURNED'
                or created['write_count']!=len(payload) or created['write_sha256']!=hashlib.sha256(payload).hexdigest()
                or parent.fact is None or parent.fact['content_sha256']!=hashlib.sha256(payload).hexdigest()
                or parent.fact['fingerprint'][1]!=len(payload)):
            fail('actual finite created-file extent/hash/close differs')
        if (denied['body_returned'] or denied['phase']!='OPEN'
                or denied['exception_class'] not in ('java.nio.file.AccessDeniedException','java.io.FileNotFoundException')
                or denied['output']['acquisition']!='THREW' or denied['output']['close']!='NOT_ENTERED'
                or stage.parents[3].missing is None): fail('protected finite create denial plus actual noncreating absence required')

    def validate_network(self,stage):
        from ._windows_t037_network import derive_session_network_intent
        from ._windows_t037_events import (EventCorrelationExpectation,diagnose_event_correlation,
            _filetime_value)
        intent=derive_session_network_intent(stage.owner.profile)
        rows=self.base_response(stage);allowed,denied=rows
        expected=wire.echo_payload(stage.owner.nonce,0);digest=hashlib.sha256(expected).hexdigest()
        def local(row):
            value=row['local_endpoint']
            if (value is None or value['address']!=b'\x7f\x00\x00\x01'
                    or not intent.source_port_range[0]<=value['port']<=intent.source_port_range[1]):
                fail('actual source tuple is missing/outside original approved finite range')
            return value
        a=local(allowed);d=local(denied)
        if (not allowed['body_returned'] or allowed['exception_class'] or allowed['echo_matches'] is not True
                or allowed['read_count']!=len(expected) or allowed['write_count']!=len(expected)
                or allowed['read_sha256']!=digest or allowed['write_sha256']!=digest
                or allowed['socket']['close']!='RETURNED'
                or allowed['remote_endpoint']!=dict(address=b'\x7f\x00\x00\x01',port=intent.server_listen_port)
                or stage.listener.peer!=('127.0.0.1',a['port']) or stage.listener.echo!=expected):
            fail('original complete finite echo/tuple/extent/hash differs')
        if (denied['body_returned'] or denied['phase']!='CONNECT' or denied['remote_endpoint'] is not None
                or not denied['exception_class'] or denied['socket']['close']!='RETURNED'):
            fail('only original connect-stage failure with known socket close may be correlated')
        # Reuse the exact configured native catalog reader and semantic guard.
        from ._windows_t037_targets import FinitePolicyApi,require_owned_policy
        from ._windows_t037_wfp import guid_key
        from ._windows_t037_effective import observe_effective_wfp
        from ._windows_owned_resources import attach_registry
        api=FinitePolicyApi.__new__(FinitePolicyApi);stage.policy_api=api
        FinitePolicyApi.__init__(api,stage.owner.profile)
        attach_registry(api,registry_for(stage.factory));scope=registry_for(stage.factory).scope()
        snap=api.observe_wfp_policy_snapshot();selected=require_owned_policy(stage.owner.profile,snap,api)
        from types import SimpleNamespace
        effective=observe_effective_wfp(stage.owner.profile,SimpleNamespace(
            observe_wfp_policy_snapshot=lambda:snap,observe_application_id=api.observe_application_id))
        expected_keys={guid_key(k) for k in stage.owner.profile.windows_policy.wfp_filter_keys}
        if set(effective.matched_filter_keys)!=expected_keys: fail('actual owned WFP semantics changed')
        start,end=_filetime_value(stage.start),_filetime_value(stage.end)
        app=api.observe_application_id(stage.inputs.executable)
        appbytes=bytes.fromhex(stage.events.app_id_bytes_hex or '')
        if (not appbytes or app.get('type')!=12 or app.get('size')!=len(appbytes)
                or app.get('sha256')!=hashlib.sha256(appbytes).hexdigest()):
            fail('original event App-ID bytes differ from the independent actual policy SDK digest')
        candidates=[]
        for kind,key,row in selected:
            if kind!='Filter' or row['action_type']!=0x1001: continue
            layers=[l for l in snap['layers'] if guid_key(l['layer_key'])==guid_key(row['layer_key'])]
            if len(layers)!=1: fail('actual owned filter layer identity absent')
            expectation=EventCorrelationExpectation(row['filter_id'],layers[0]['layer_id'],0,6,
                '127.0.0.1',d['port'],'127.0.0.1',intent.worker_listen_port,'OUT',start,end,
                appbytes,stage.owner.profile.appcontainer_sid)
            result=diagnose_event_correlation(stage.events,expectation)
            if result.state=='MATCHED_DIAGNOSTIC_ONLY': candidates.append((row,result))
        if len(candidates)!=1: fail('one exact original tuple/time/image/AppC/owned-filter drop required')
        fresh=api.observe_wfp_policy_snapshot()
        if require_owned_policy(stage.owner.profile,fresh,api)!=selected: fail('owned filter identity/semantics drifted')
        errors=registry_for(stage.factory).drain(scope)
        if errors: raise errors[0][1]
        stage.event_match=candidates[0]
        stage.owned_policy=selected

    def rpc(self,worker,kind,payload):
        request='qualification-'+uuid4().hex
        call=dict(worker=worker,kind=kind,payload=dict(payload),request_id=request,reply=None)
        self.vendor_calls.append(call)
        reply=worker.submit(kind,payload,request_id=request,rpc_timeout_s=5.0,queue_timeout_s=5.0)
        deadline=time.monotonic()+5.0
        while reply.get('status') in ('QUEUED','RUNNING'):
            if time.monotonic()>=deadline: fail('original vendor RPC did not reach a bounded terminal state')
            reply=worker.status(request,timeout_s=1.0)
        call['reply']=reply;self.vendor_replies.append(reply)
        if (reply.get('request_id')!=request or reply.get('status')!='SUCCEEDED'
                or reply.get('ok') is not True or type(reply.get('result')) is not dict):
            fail('original vendor RPC result not a correlated success')
        return reply['result']

    def run_vendor(self):
        self.check();op=self.operation;e=op.enrollment
        if op.recipe.vendor_roles!=('server','worker'):
            fail('complete Server/Worker qualification requires both explicitly approved vendor roles')
        factory=op.new_factory(q.Purpose.QUALIFY_VENDOR);self.vendor_factory=factory
        self.runtime,paths=self.runtime_inputs(factory)
        # Keep actual launcher/Worker roots before any new directory/child/RPC.
        launcher=e.daemon.session_server_launcher;self.launcher=launcher
        self.server=launcher.start(self.runtime,e.profile.host_resource_profile.project_id,
            factory.session_id,process_factory=factory)
        identity=launcher.verify(self.server)
        if identity.pid!=self.server.process.pid or self.server.endpoint is None: fail('original Server identity unavailable')
        self.vendor_children.append(self.server.process)
        from ._java_worker import PersistentJavaWorker
        state=self.runtime.session_state_root/('qualification-worker-'+factory.session_id)
        self.worker=PersistentJavaWorker.__new__(PersistentJavaWorker)
        PersistentJavaWorker.__init__(self.worker,paths,state_dir=state,process_factory=factory)
        self.worker.start(startup_timeout_s=30.0)  # normal finite startup budget, including original preflight
        self.vendor_children.append(self.worker._process)
        result=self.rpc(self.worker,'connect',dict(host=self.server.endpoint.address,
            port=self.server.endpoint.port,encrypted=False,user='',password=''))
        if (result.get('connected') is not True or result.get('server')!=str(self.server.endpoint.address)+':'+str(self.server.endpoint.port)
                or result.get('engine_version')!=op.recipe.comsol_version
                or type(result.get('generation')) is not int or not result.get('instance_id')):
            fail('actual connected vendor version/epoch does not match the approved runtime')
        detached=self.rpc(self.worker,'disconnect',{})
        if (detached.get('connected') is not False or detached.get('instance_id')!=result['instance_id']
                or type(detached.get('generation')) is not int or detached['generation']<=result['generation']):
            fail('original vendor disconnect epoch not confirmed')
        self.worker.close()
        if any(getattr(self.worker,n,None) is not None for n in (
                '_process','_fixed_process','_fixed_launch_owner','_startup_unknown','_retirement_unknown')):
            fail('original vendor Worker retirement unknown')
        outcome=launcher.stop(self.server,timeout_s=5.0)
        if outcome.get('exit_confirmed') is not True or not self.server.retired: fail('original Server retirement unknown')
        from ._windows_t037_b_observation import settle_original_child_b
        for child in self.vendor_children:
            if (not child._closed or type(child.returncode) is not int
                    or child._process_entry.state!='RELEASED' or child._thread_entry.state!='RELEASED'):
                fail('original vendor child entries are not retired')
            settle_original_child_b(child)
        factory.close()
        self.vendor_closed=True

    def execute(self):
        if self.first_error is not None: raise self.first_error
        if self.state!='NEW': fail('original qualification execution is one-shot')
        self.state='RUNNING'
        try:
            self.check()
            # Qualification never stops or adopts an earlier participant.
            from ._windows_appcontainer_gate import _require_terminal
            for participant in tuple(self.operation.enrollment.gate.participants.values()):
                _require_terminal(participant)
            for stage in self.stages: self.run_stage(stage)
            self.run_vendor()
            self.state='READY'
            self.live_c=OriginalLiveC(self,seal=_SEAL)
            self.operation.live_c=self.live_c
            _LIVE[id(self.operation.enrollment.runtime)]=self.live_c
            return self.live_c
        except BaseException as error:
            self.first_error=error;self.state='UNKNOWN';raise self.operation.unknown(error)


class OriginalLiveC:
    def __init__(self,run,*,seal):
        if seal is not _SEAL or _RUNS.get(id(run.operation)) is not run: fail('original complete coordinator required')
        self.run=run;self.operation=run.operation;self._seal=seal
        self.profile=run.operation.enrollment.profile
        self.plan=run.operation.plan;self.recipe=run.operation.recipe
        self.runtime=run.operation.enrollment.runtime;self.gate=run.operation.enrollment.gate
        self.manifest_sha256=q.recipes.manifest_digest(self.profile.prepared_launch)
        self.source_sha256=self.profile.source_sha256;self.profile_sha256=self.profile.sha256
        self.policy_sha256=q.recipes.digest(run.stages[1].owned_policy)
        self.current_policy_reads=[];self.validation_error=None

    def check(self,*,runtime_id=None,live=True):
        op=self.operation;e=op.enrollment;r=self.run
        if (self._seal is not _SEAL
                or q.recipes.digest(r.stages[1].owned_policy)!=self.policy_sha256
                or _LIVE.get(id(self.runtime)) is not self or op.live_c is not self
                or _RUNS.get(id(op)) is not r or op.coordinator is not r or r.live_c is not self
                or r.state!='READY' or r.first_error is not None or not r.vendor_closed
                or not all(s.accepted and s.io.state=='RETIRED_SUBMITTED' for s in r.stages)
                or self.profile is not e.profile or self.runtime is not e.runtime or self.gate is not e.gate or op.state!='READY'
                or self.profile.source_sha256!=self.source_sha256 or self.profile.sha256!=self.profile_sha256
                or q.recipes.manifest_digest(self.profile.prepared_launch)!=self.manifest_sha256
                or runtime_id is not None and runtime_id!=self.recipe.runtime_id): fail('live C original scope changed')
        # Scope/copy refusals above cannot poison the original C. Once its
        # own revalidation has failed, even pure reentry returns that same
        # first error before any new loader/SDK/admission observation.
        if self.validation_error is not None:
            raise self.validation_error
        try:
            e.check(live=live)
            if live:
                row=e.daemon.store.get_operation(op.admission.operation_id)
                job=e.daemon.store.job(op.admission.job_id)
                if (row is None or job is None or row['status']!='SUCCEEDED' or job['status']!='SUCCEEDED'
                        or row['operation']!='host.change.apply' or job['operation_id']!=row['operation_id']
                        or row['metadata'].get('host_control',{}).get('admission_id')!=op.admission.admission_id
                        or row['metadata'].get('host_control',{}).get('plan_sha256')!=self.plan.sha256):
                    fail('live C is unavailable before the original APPLY terminal commit')
            from ._windows_appcontainer_gate import _require_terminal
            for stage in r.stages:
                _require_terminal(stage.owner.participant);stage.response.terminal_data()
                if any(not p.retired for p in stage.parents): fail('original finite parent readback remains owned')
                if stage.listener is not None and (not stage.listener.closed or stage.listener.registry.unresolved()):
                    fail('original echo parent remains owned')
            _require_terminal(r.vendor_factory._profile_participant)
            if live:
                self._check_owned_policy()
        except BaseException as error:
            if self.validation_error is None:
                self.validation_error=error
            raise
        return self


    def _check_owned_policy(self):
        from ._windows_t037_targets import FinitePolicyApi,require_owned_policy
        from ._windows_t037_effective import observe_effective_wfp
        from types import SimpleNamespace
        if len(self.current_policy_reads)>=4096:
            fail('finite C current-observation budget exhausted')
        api=FinitePolicyApi.__new__(FinitePolicyApi)
        self.current_policy_reads.append(api)  # exact owner before constructor/first SDK call
        try:
            FinitePolicyApi.__init__(api,self.profile)
            registry=registry_for(api);scope=registry.scope()
            snapshot=api.observe_wfp_policy_snapshot()
            selected=require_owned_policy(self.profile,snapshot,api)
            observe_effective_wfp(self.profile,SimpleNamespace(
                observe_wfp_policy_snapshot=lambda:snapshot,observe_application_id=api.observe_application_id))
            if q.recipes.digest(selected)!=self.policy_sha256:
                fail('original finite owned policy identity/semantics changed after qualification')
            errors=registry.drain(scope)
            if errors:raise errors[0][1]
            if registry.unresolved():fail('original current policy reader retirement unverified')
        except BaseException as error:
            self.validation_error=error
            raise


def require_live_c(daemon,*,runtime_id=None,live=True):
    runtime=getattr(daemon,'_windows_t037_scm_runtime',None)
    value=_LIVE.get(id(runtime))
    if type(value) is not OriginalLiveC or value.operation.enrollment.daemon is not daemon:
        fail('original applicable C has not been produced')
    return value.check(runtime_id=runtime_id,live=live)
