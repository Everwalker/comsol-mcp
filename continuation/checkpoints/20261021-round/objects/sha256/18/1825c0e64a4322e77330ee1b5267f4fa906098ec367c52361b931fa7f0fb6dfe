"""Actual public hostread admission; original registered containers, software SDK seam."""
from pathlib import Path
import pytest
from tests.test_artifact_preview import _new_daemon,_register,_png
from comsol_mcp._session_context import SessionRuntimeConfig,SessionRuntimeContext,SessionEndpointIdentity
from comsol_mcp._session_lifecycle import new_lifecycle_record
from comsol_mcp._execution_contract import SessionLedger
from comsol_mcp._execution_service import ExecutionService
from comsol_mcp._managed_backend import ManagedBackend

class NoSDK:
    def health(self):raise AssertionError('hostread must not call SDK health')
    def close(self):pass

@pytest.fixture
def lane(tmp_path):
    daemon,project,root=_new_daemon(tmp_path)
    (root/'inputs/a.png').write_bytes(_png(1,1,[bytes([255,0,0,255])]))
    artifact=_register(daemon,project,'inputs/a.png')
    worker=NoSDK();service=ExecutionService(SessionLedger('s','server',permissions={'inspect'}),worker,project_root=root)
    backend=ManagedBackend(tmp_path/'lane',daemon.store,service=service,worker=worker,project_root=root)
    backend.endpoint_key='127.0.0.1:2040';backend.worker_identity={'worker_instance_id':'w','connection_epoch':1,'server_instance_id':'server','endpoint':'127.0.0.1:2040'}
    config=SessionRuntimeConfig('fixture','6.4.0.293',tmp_path,tmp_path/'NOT_EXECUTED-java',(tmp_path/'NOT_LOADED.jar',),tmp_path,tmp_path)
    ctx=SessionRuntimeContext(project,'s',root,config,SessionEndpointIdentity('127.0.0.1',2040,1),backend=backend,worker_instance_id='w',worker=worker,service=service,client=worker,client_connected=True)
    daemon.session_registry.register(ctx);daemon._session_backends[(project,'s')]=backend;daemon._session_worker_handles[(project,'s')]=worker
    daemon.session_lifecycle.save(new_lifecycle_record(project_id=project,session_id='s',state='CONNECTED',runtime_id='fixture',endpoint={'host':'127.0.0.1','port':2040},client_state='CONNECTED',server_state='SHARED',server_ownership='shared',worker_instance_id='w',worker_epoch=1,server_instance_id='server'))
    daemon._default_backend.endpoint_key=None
    def read(op='artifact.preview',body=None):
        args={'project_id':project,'artifact_id':artifact}
        if op=='artifact.preview':args['options']={'schema_version':1,'kind':'image','max_width':128,'max_height':128}
        args.update(body or {})
        return daemon.dispatch({'operation':'operation_call','arguments':{'operation_id':op,'arguments':args},'execution':{'project_id':project}})
    yield daemon,project,root,backend,ctx,read
    daemon.close()

@pytest.mark.parametrize('operation',['artifact.preview','artifact.verify','artifact.inspect','artifact.list'])
def test_actual_registered_project_lane_hostreads_without_default_or_sdk(lane,operation):
    daemon,project,root,backend,ctx,read=lane
    if operation=='artifact.list':
        result=daemon.dispatch({'operation':'operation_call','arguments':{'operation_id':operation,'arguments':{'project_id':project}},'execution':{'project_id':project}})
    else:result=read(operation)
    assert result['success'],result

@pytest.mark.parametrize('damage',['retired','disconnected','root','workerUUID','epoch','server','remote','table','worker','lifecycle','project','session-injection'])
def test_real_project_lane_invalid_identity_refuses_without_sdk(lane,damage):
    daemon,project,root,backend,ctx,read=lane
    if damage=='retired':ctx.client_retired=True
    elif damage=='disconnected':ctx.client_connected=False
    elif damage=='root':backend.project_root=root.parent
    elif damage=='workerUUID':backend.worker_identity['worker_instance_id']='other'
    elif damage=='epoch':backend.worker_identity['connection_epoch']=2
    elif damage=='server':backend.worker_identity['server_instance_id']='other'
    elif damage=='remote':backend.endpoint_key='192.0.2.3:2040'
    elif damage=='table':daemon._session_backends[(project,'s')]=object()
    elif damage=='worker':daemon._session_worker_handles[(project,'s')]=object()
    elif damage=='lifecycle':
        row=daemon.session_lifecycle.get(project,'s');row['state']='UNKNOWN';daemon.session_lifecycle.save(row)
    extra={'project_id':'other'} if damage=='project' else {'session_id':'s'} if damage=='session-injection' else None
    result=read(body=extra);assert not result['success'],result

@pytest.mark.parametrize('damage',['endpoint','table','epoch','workerUUID'])
def test_post_read_original_binding_recheck_refuses_midflight(lane,monkeypatch,damage):
    from comsol_mcp import _artifact_preview_io
    daemon,project,root,backend,ctx,read=lane;original=_artifact_preview_io.preview_registered_artifact
    def changed(*a,**kw):
        result=original(*a,**kw)
        if damage=='endpoint':backend.endpoint_key='127.0.0.1:2041'
        elif damage=='table':daemon._session_backends[(project,'s')]=object()
        elif damage=='epoch':ctx.worker_epoch=2
        elif damage=='workerUUID':ctx.worker_instance_id='other'
        return result
    monkeypatch.setattr(_artifact_preview_io,'preview_registered_artifact',changed)
    result=read();assert not result['success'],result

@pytest.mark.parametrize('endpoint',['127.0.0.1:2041','192.0.2.4:2041'])
def test_multiple_registered_lanes_merge_only_verified_local_host(lane,endpoint,tmp_path):
    daemon,project,root,backend,ctx,read=lane
    worker=NoSDK();service=ExecutionService(SessionLedger('s2','server2',permissions={'inspect'}),worker,project_root=root)
    other=ManagedBackend(tmp_path/'lane2',daemon.store,service=service,worker=worker,project_root=root)
    host=endpoint.rsplit(':',1)[0];other.endpoint_key=endpoint;other.worker_identity={'worker_instance_id':'w2','connection_epoch':1,'server_instance_id':'server2','endpoint':endpoint}
    context=SessionRuntimeContext(project,'s2',root,ctx.runtime,SessionEndpointIdentity(host,2041,1),backend=other,worker_instance_id='w2',worker=worker,service=service,client=worker,client_connected=True)
    daemon.session_registry.register(context);daemon._session_backends[(project,'s2')]=other;daemon._session_worker_handles[(project,'s2')]=worker
    daemon.session_lifecycle.save(new_lifecycle_record(project_id=project,session_id='s2',state='CONNECTED',runtime_id='fixture',endpoint={'host':host,'port':2041},client_state='CONNECTED',server_state='SHARED',server_ownership='shared',worker_instance_id='w2',worker_epoch=1,server_instance_id='server2'))
    result=read();assert result['success'] is endpoint.startswith('127.'),result


def test_foreign_registered_backend_never_supplies_project_host_proof(lane):
    daemon,project,root,backend,ctx,read=lane
    daemon.session_registry.remove(project,'s',expected=ctx)
    daemon._session_backends.pop((project,'s'))
    daemon._session_worker_handles.pop((project,'s'))
    # Existing own lifecycle is still authoritative; foreign/default cannot replace it.
    daemon._session_backends[('foreign','s')]=backend
    daemon._default_backend.endpoint_key='127.0.0.1:9999'
    result=read();assert not result['success'],result


def test_malformed_persisted_session_cannot_enable_default_fallback(lane):
    daemon,project,root,backend,ctx,read=lane
    with daemon.store.lock:
        daemon.store.db.execute("INSERT INTO sessions(session_id,metadata) VALUES(?,?)",('broken','{broken'))
    result=read();assert not result['success'],result


def add_stopped_history(lane, *, uncertain=False):
    daemon,project,root,backend,ctx,read=lane
    # The real lifecycle producer persists both versions in the real session
    # store. This completed historical lane has no registered live context,
    # retained child handle, or backend Worker; it cannot attest an endpoint.
    row=new_lifecycle_record(project_id=project,session_id='old',state='CONNECTED',runtime_id='fixture',endpoint={'host':'127.0.0.1','port':2000},client_state='CONNECTED',server_state='SHARED',server_ownership='shared',worker_instance_id='old-worker',worker_epoch=1,server_instance_id='old-server')
    row=daemon.session_lifecycle.save(row)
    terminal=daemon._updated_session_lifecycle(row,state='UNKNOWN' if uncertain else 'STOPPED',client_state='UNKNOWN' if uncertain else 'RETIRED',server_state='STOPPED',server_ownership='unknown',server_process_identity=None,server_instance_id=None)
    daemon.session_lifecycle.save(terminal,expected_revision=row['revision'])


def test_real_stopped_retired_history_does_not_block_new_connected_lane(lane):
    add_stopped_history(lane)
    result=lane[-1]();assert result['success'],result


def test_unknown_history_is_not_filtered_by_connected_lane(lane):
    add_stopped_history(lane,uncertain=True)
    result=lane[-1]();assert not result['success'],result


def test_only_stopped_history_never_uses_default_endpoint_fallback(lane):
    daemon,project,root,backend,ctx,read=lane
    add_stopped_history(lane)
    daemon.session_registry.remove(project,'s',expected=ctx)
    daemon._session_backends.pop((project,'s'));daemon._session_worker_handles.pop((project,'s'))
    # Remove the current session's registration entirely; only proven old
    # history remains. Even a loopback default cannot supply its proof.
    with daemon.store.lock:daemon.store.db.execute('DELETE FROM sessions WHERE session_id=?',('s',))
    daemon._default_backend.endpoint_key='127.0.0.1:9999'
    result=read();assert not result['success'],result


def test_stopped_history_with_retained_unresolved_worker_is_not_filtered(lane):
    daemon,project,root,backend,ctx,read=lane
    add_stopped_history(lane)
    daemon._session_worker_handles[(project,'old')]=object()
    result=read();assert not result['success'],result
