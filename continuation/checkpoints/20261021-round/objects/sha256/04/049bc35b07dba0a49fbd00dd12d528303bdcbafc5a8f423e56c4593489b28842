"""Real public inspector and production transport receipts, software SDK/socket seam."""
import errno,json,threading
import pytest
from tools.test_t050_checkpoint_cost_cycle import _environment
from comsol_mcp._java_worker import PersistentJavaWorker,JavaWorkerError


def test_public_inspection_reads_parameter_names_without_per_parameter_values(tmp_path,monkeypatch):
    daemon,worker,service,project,ref,call,saves=_environment(tmp_path,monkeypatch)
    parameters=worker.models['m'].param();calls=[];original=parameters.get
    for i in range(4096):parameters.set(f't050_p{i:04d}',f'{i+1}[mm]',f'baseline{i}')
    def tracked(name):calls.append(name);return original(name)
    monkeypatch.setattr(parameters,'get',tracked)
    try:
        result=call('model.inspect',{},'names-only',ref);assert result['success'],result
        assert len(result['data']['structure']['parameters'])==4097
        assert not calls
        value=call('parameter.get',{'names':['t050_p0000'],'evaluate':False},'explicit-value',ref)
        assert value['success'],value
        assert value['data']['parameters'][0]['expression']=='1[mm]' and calls and set(calls)=={'t050_p0000'}
    finally:daemon.close()


class Transport:
    def __init__(self,phase):self.phase=phase;self.writes=0;self.reads=0;self.body={}
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def settimeout(self,value):pass
    def makefile(self,mode):return self
    def write(self,raw):
        self.writes+=1
        if self.writes==2 and self.phase=='request_write':raise OSError(errno.EPIPE,'software transport write failed')
        if self.writes==2:self.body=json.loads(raw)
    def flush(self):pass
    def readline(self):
        self.reads+=1
        if self.reads==1:
            if self.phase=='authentication':raise OSError(errno.ECONNRESET,'software auth read failed')
            if self.phase=='authentication_rejected':return b'{"ok":false,"error":"software-auth-rejected"}\n'
            if self.phase=='malformed_auth':return b'not-valid-json\n'
            if self.phase=='malformed_auth_type':return b'[]\n'
            return b'{"ok":true}\n'
        if self.phase=='malformed_reply':return b'not-valid-json\n'
        if self.phase=='malformed_reply_type':return b'[]\n'
        if self.phase=='reply_read':raise OSError(errno.ECONNRESET,'software reply read failed')
        if self.phase=='timeout':raise TimeoutError(errno.ETIMEDOUT,'software reply timeout')
        return json.dumps({'ok':False,'status':'FAILED','type':'call','request_id':self.body['request_id'],'generation':(0 if self.phase=='invalid_generation' else 2),'error':'software typed SDK read error'}).encode()+b'\n'


def real_transport_worker():
    worker=object.__new__(PersistentJavaWorker)
    worker._port=2001;worker._token='SOFTWARE_NOT_A_REAL_WORKER_CREDENTIAL'
    worker._known_requests={};worker._operation_context=threading.local();worker._on_request_event=None
    worker._generation=2;worker._next_generation=3
    return worker

@pytest.mark.parametrize('phase',['connect','authentication','request_write','reply_read','timeout','correlated_sdk_failure','authentication_rejected','malformed_auth','malformed_reply','invalid_generation','malformed_auth_type','malformed_reply_type'])
def test_actual_public_inspect_preserves_production_transport_unknown_receipts(tmp_path,monkeypatch,phase):
    daemon,worker,service,project,ref,call,saves=_environment(tmp_path,monkeypatch)
    real=real_transport_worker();real.state_dir=tmp_path/'transport-state';recorded=[]
    def connect(*args,**kwargs):
        if phase=='connect':raise OSError(errno.EADDRNOTAVAIL,'software ephemeral exhaustion')
        return Transport(phase)
    monkeypatch.setattr('comsol_mcp._java_worker.socket.create_connection',connect)
    # Only socket/SDK boundary is stand-in. Real PersistentJavaWorker.submit,
    # _request, event emitter, public daemon/adapters and durable store run.
    def param():
        def event(row):
            recorded.append(row)
            worker._active_event_callback(row)
        with real.operation_context(worker._active_operation_id,on_request_event=event):
            reply=real.submit('call',{'handle':'software-model','method':'param','args':[],'generation':2},rpc_timeout_s=1)
            raise JavaWorkerError('software correlated SDK failure',reply=reply)
    monkeypatch.setattr(worker.models['m'].java,'param',param)
    try:
        result=call('model.inspect',{},'transport-inspect',ref)
        assert not result['success'] and len(recorded)==2,result
        job=daemon.store.operation_job(result['execution']['operation_id'])
        if phase=='correlated_sdk_failure':
            assert result['error']['code']=='MODEL_INSPECTION_UNAVAILABLE' and result['error']['safe_retry'] is True
            assert job['status']=='FAILED' and recorded[-1]['phase']=='observed' and recorded[-1]['status']=='FAILED'
        else:
            assert result['error']['code']=='EXECUTION_STATE_UNKNOWN' and result['error']['safe_retry'] is False,result
            assert job['status']=='UNKNOWN',job
            assert recorded[0]['phase']=='submitted' and recorded[-1]['status']=='UNKNOWN'
            assert recorded[0]['request_id']==recorded[-1]['request_id'] and recorded[0]['request_hash']==recorded[-1]['request_hash']
            details=result['error']['details']
            assert details['request_id']==recorded[0]['request_id']
            if phase not in {'authentication_rejected','malformed_auth','malformed_reply','invalid_generation','malformed_auth_type','malformed_reply_type'}:
                assert details['transport_stage']==('reply_read' if phase=='timeout' else phase)
                assert details['errno'] in {errno.EADDRNOTAVAIL,errno.ECONNRESET,errno.EPIPE,errno.ETIMEDOUT}
        persisted=daemon.store.events(job['job_id'],limit=100)
        assert [row['metadata']['phase'] for row in persisted if row['event']=='worker_request']==[row['phase'] for row in recorded]
    finally:daemon.close()


@pytest.mark.parametrize("phase",["authentication_rejected","connect","malformed_reply","timeout"])
def test_transport_failure_never_rewrites_prior_known_terminal_reply(monkeypatch,phase):
    worker=real_transport_worker();original={'status':'SUCCEEDED','request_id':'original','original_marker':'immutable'}
    worker._known_requests['original']=dict(original)
    def connect(*a,**k):
        if phase=='connect':raise OSError(errno.EADDRNOTAVAIL,'software transport unavailable')
        return Transport(phase)
    monkeypatch.setattr('comsol_mcp._java_worker.socket.create_connection',connect)
    with pytest.raises(JavaWorkerError) as error:worker.submit('call',{'handle':'model','method':'param'},request_id='original')
    assert error.value.execution_state_unknown and error.value.reply['request_id']=='original'
    assert worker._known_requests['original']==original
