import pytest
from tests.test_windows_network_intent import transferred_getter_sdk

@pytest.mark.parametrize('fault',['CreatePipe','SetHandleInformation','CloseHandle:102','SetNamedPipeHandleState','InitializeProcThreadAttributeList','UpdateProcThreadAttribute','CreateProcessW','DeleteProcThreadAttributeList','CloseHandle:101','CloseHandle:104','CloseHandle:106','ReadFile:103','ReadFile:105','WaitForSingleObject','GetExitCodeProcess','CloseHandle:103','CloseHandle:105','CloseHandle:202','CloseHandle:201'])
def test_real_getter_sdk_first_unknown_roots_and_stops(monkeypatch,fault):
    from comsol_mcp import _windows_t037_network as n, _windows_owned_resources as owned
    state=transferred_getter_sdk(monkeypatch);fake=state.query_fake;fake.mode='exception:'+fault
    with pytest.raises(owned.NativeResourceError):n.read_windows_ipv4_tcp_dynamic_range()
    owner=n._dynamic_range_owner;q=owner.process_owner
    assert q.state=='UNKNOWN' and q.error is not None and q.calls and q.calls[-1]['state']=='ENTERED'
    assert fake.log[-1]==fault and not state.closed
    if fault=='CreateProcessW':assert q.calls[-1]['holders'][-3] is not None
    snapshot=(list(fake.log),len(state.created),list(state.closed),state.reads)
    with pytest.raises(owned.NativeResourceError):n.read_windows_ipv4_tcp_dynamic_range()
    with pytest.raises(owned.NativeResourceError):owner.recover_owned(owner)
    assert snapshot==(fake.log,len(state.created),state.closed,state.reads)

@pytest.mark.parametrize('mode',['nodata','overflow'])
def test_real_getter_deadline_or_overflow_keeps_process(monkeypatch,mode):
    from comsol_mcp import _windows_t037_network as n, _windows_owned_resources as owned
    state=transferred_getter_sdk(monkeypatch);fake=state.query_fake;fake.mode=mode
    with pytest.raises(owned.NativeResourceError):n.read_windows_ipv4_tcp_dynamic_range()
    owner=n._dynamic_range_owner;q=owner.process_owner
    assert q.state=='UNKNOWN' and not state.closed and 'WaitForSingleObject' not in fake.log
    before=list(fake.log)
    with pytest.raises(owned.NativeResourceError):owner.recover_owned(owner)
    assert fake.log==before


@pytest.mark.parametrize('api,result',[('CreatePipe',0),('CreatePipe',True),('CreatePipe',None),('CreateProcessW',0),('CreateProcessW',True),('WaitForSingleObject',False),('WaitForSingleObject',1.0),('WaitForSingleObject',258),('GetExitCodeProcess',True),('GetExitCodeProcess',0)])
def test_raw_returns_are_latched_after_original_outputs(monkeypatch,api,result):
    from comsol_mcp import _windows_t037_network as n, _windows_owned_resources as owned
    state=transferred_getter_sdk(monkeypatch);f=state.query_fake;k=state.kernel;original=getattr(k,api)
    def replace(*args):original(*args);return result
    monkeypatch.setattr(k,api,replace)
    with pytest.raises(owned.NativeResourceError):n.read_windows_ipv4_tcp_dynamic_range()
    owner=n._dynamic_range_owner;q=owner.process_owner
    assert q.state=='UNKNOWN' and not state.closed and q.calls[-1]['raw'] is result
    if api=='CreatePipe':
        rd,wr,sa=q.calls[-1]['holders'];assert rd.value and wr.value and len(q.handles)==2
    if api=='CreateProcessW':
        pi=q.calls[-1]['holders'][3];assert pi.process==201 and pi.thread==202
    calls=list(f.log)
    with pytest.raises(owned.NativeResourceError):owner.recover_owned(owner)
    assert f.log==calls

@pytest.mark.parametrize('mode',['truezero','badcount','invalidbool','baderror','partialsecond'])
def test_read_result_eof_and_partial_data_are_not_invented(monkeypatch,mode):
    from comsol_mcp import _windows_t037_network as n, _windows_owned_resources as owned
    state=transferred_getter_sdk(monkeypatch);f=state.query_fake;k=state.kernel;original=k.ReadFile
    def read(h,buf,size,count,over):
        if mode=='partialsecond' and h==103:return original(h,buf,size,count,over)
        f.log.append('injected:'+str(h));f.error=0
        if mode=='truezero':return 1
        if mode=='badcount':f.error=109;count._obj.value=1;return 0
        if mode=='invalidbool':return True
        if mode=='baderror':f.error=5;return 0
        raise OSError('second stream unknown')
    monkeypatch.setattr(k,'ReadFile',read)
    with pytest.raises(owned.NativeResourceError):n.read_windows_ipv4_tcp_dynamic_range()
    q=n._dynamic_range_owner.process_owner;assert q.state=='UNKNOWN' and not state.closed
    assert 'WaitForSingleObject' not in f.log
    if mode=='partialsecond':assert bytes(q.outputs['stdout']) and not q.eof

def test_known_nonzero_closes_all_and_refuses_semantically(monkeypatch):
    from comsol_mcp import _windows_t037_network as n
    state=transferred_getter_sdk(monkeypatch);state.query_fake.exit=1
    with pytest.raises(Exception) as e:n.read_windows_ipv4_tcp_dynamic_range()
    assert e.value.code=='T037_NETWORK_INTENT_INVALID'
    assert state.closed==[903,902,901,900] and n._dynamic_range_owner is None

def test_known_success_preserves_order_and_eof(monkeypatch):
    from comsol_mcp import _windows_t037_network as n
    state=transferred_getter_sdk(monkeypatch);out=n.read_windows_ipv4_tcp_dynamic_range();log=state.query_fake.log
    assert out.source_port_range==(40000,45000) and out.verification_state=='NOT_VERIFIED'
    assert state.query_fake.owner.eof=={'stdout','stderr'} and state.query_fake.owner.raw_wait==0 and state.query_fake.owner.exit_value==0
    assert log.index('WaitForSingleObject')>log.index('ReadFile:105')
    assert log.index('GetExitCodeProcess')>log.index('WaitForSingleObject')
    assert [x for x in log if x.startswith('CloseHandle:')]==['CloseHandle:102','CloseHandle:101','CloseHandle:104','CloseHandle:106','CloseHandle:103','CloseHandle:105','CloseHandle:202','CloseHandle:201']


def test_nonblocking_no_data_is_not_eof_then_broken_pipe_is(monkeypatch):
    from comsol_mcp import _windows_t037_network as n
    state=transferred_getter_sdk(monkeypatch);f=state.query_fake;k=state.kernel;original=k.ReadFile;seen=set()
    def read(h,buf,size,count,over):
        if h not in seen:
            seen.add(h);f.log.append('no-data:'+str(h));f.error=232;return 0
        return original(h,buf,size,count,over)
    monkeypatch.setattr(k,'ReadFile',read)
    out=n.read_windows_ipv4_tcp_dynamic_range();q=f.owner
    assert out.source_port_range==(40000,45000) and q.eof=={'stdout','stderr'}
    assert 'no-data:103' in f.log and 'no-data:105' in f.log and q.raw_wait==0


def test_python311_metadata_does_not_gate_native_query(monkeypatch):
    from comsol_mcp import _windows_t037_network as n
    from types import SimpleNamespace
    state=transferred_getter_sdk(monkeypatch);monkeypatch.setattr(n,'_sys',SimpleNamespace(version_info=(3,11,9)),raising=False)
    result=n.read_windows_ipv4_tcp_dynamic_range()
    assert result.source_port_range==(40000,45000) and len(state.queries)==1 and n._dynamic_range_owner is None

def test_attribute_list_bad_size_is_unknown_before_update(monkeypatch):
    from comsol_mcp import _windows_t037_network as n, _windows_owned_resources as owned
    state=transferred_getter_sdk(monkeypatch);f=state.query_fake;k=state.kernel;original=k.InitializeProcThreadAttributeList
    def init(attr,count,flags,need):
        raw=original(attr,count,flags,need)
        if attr is not None:need._obj.value=129
        return raw
    monkeypatch.setattr(k,'InitializeProcThreadAttributeList',init)
    with pytest.raises(owned.NativeResourceError):n.read_windows_ipv4_tcp_dynamic_range()
    assert n._dynamic_range_owner.process_owner.state=='UNKNOWN' and 'UpdateProcThreadAttribute' not in f.log and not state.closed


def test_revalidation_consumer_uses_real_getter_and_detects_drift(monkeypatch):
    from comsol_mcp import _windows_t037_network as n
    from comsol_mcp._windows_t037 import WindowsT037ProfileV2
    from tests.test_windows_network_intent import network_profile_and_plan
    state=transferred_getter_sdk(monkeypatch);state.query_fake.output=state.query_fake.output.replace(b'5001',b'5002')
    profile,_=network_profile_and_plan();intent=n.derive_session_network_intent(WindowsT037ProfileV2(profile,'d'*64,{},'SOFTWARE_ONLY'))
    with pytest.raises(Exception) as exc:n.revalidate_source_port_range(intent)
    assert exc.value.code=='T037_NETWORK_INTENT_INVALID' and len(state.queries)==1 and n._dynamic_range_owner is None


@pytest.mark.parametrize('handle,result',[(102,0),(101,True),(104,None),(106,False),(103,0),(105,True),(202,None),(201,0)])
def test_each_native_close_invalid_return_fences_following_calls(monkeypatch,handle,result):
    from comsol_mcp import _windows_t037_network as n, _windows_owned_resources as owned
    state=transferred_getter_sdk(monkeypatch);f=state.query_fake;k=state.kernel;original=k.CloseHandle
    def close(h):
        if h==handle:f.log.append('close-invalid:'+str(h));return result
        return original(h)
    monkeypatch.setattr(k,'CloseHandle',close)
    with pytest.raises(owned.NativeResourceError):n.read_windows_ipv4_tcp_dynamic_range()
    owner=n._dynamic_range_owner;q=owner.process_owner
    assert q.state=='UNKNOWN' and f.log[-1]=='close-invalid:'+str(handle) and not state.closed
    snap=list(f.log)
    with pytest.raises(owned.NativeResourceError):owner.recover_owned(owner)
    assert f.log==snap

def test_signaled_original_exit259_is_known_nonzero(monkeypatch):
    from comsol_mcp import _windows_t037_network as n
    state=transferred_getter_sdk(monkeypatch);state.query_fake.exit=259
    with pytest.raises(Exception) as exc:n.read_windows_ipv4_tcp_dynamic_range()
    q=state.query_fake.owner
    assert exc.value.code=='T037_NETWORK_INTENT_INVALID' and q.raw_wait==0 and q.exit_value==259 and q.state=='RELEASED'
    assert state.closed==[903,902,901,900] and n._dynamic_range_owner is None
    assert state.query_fake.log.count('WaitForSingleObject')==1

def test_nonempty_stderr_known_failure_after_close(monkeypatch):
    import ctypes as C
    from comsol_mcp import _windows_t037_network as n
    state=transferred_getter_sdk(monkeypatch);f=state.query_fake;k=state.kernel;old=k.ReadFile;sent=[]
    def read(h,buf,size,count,over):
        if h==105 and not sent:
            sent.append(True);f.log.append('stderr-data');C.memmove(buf,b'error',5);count._obj.value=5;f.error=0;return 1
        return old(h,buf,size,count,over)
    monkeypatch.setattr(k,'ReadFile',read)
    with pytest.raises(Exception) as exc:n.read_windows_ipv4_tcp_dynamic_range()
    assert exc.value.code=='T037_NETWORK_INTENT_INVALID' and f.owner.state=='RELEASED' and n._dynamic_range_owner is None
    assert state.closed==[903,902,901,900]

@pytest.mark.parametrize('value',[2,-1,-(1<<31),(1<<31)-1])
def test_all_win32_bool_success_values_including_read_and_closers(monkeypatch,value):
    from comsol_mcp import _windows_t037_network as n
    state=transferred_getter_sdk(monkeypatch);k=state.kernel;fake=state.query_fake
    names=('CreatePipe','SetHandleInformation','SetNamedPipeHandleState','InitializeProcThreadAttributeList','UpdateProcThreadAttribute','CreateProcessW','ReadFile','GetExitCodeProcess','CloseHandle')
    for name in names:
        old=getattr(k,name)
        def wrap(*args,original=old):
            raw=original(*args)
            return value if type(raw) is int and raw==1 else raw
        monkeypatch.setattr(k,name,wrap)
    result=n.read_windows_ipv4_tcp_dynamic_range();q=fake.owner
    assert result.source_port_range==(40000,45000) and q.state=='RELEASED'
    assert any(r['raw']==value and r['function'] is not None for r in q.calls)
    assert q.eof=={'stdout','stderr'} and state.closed==[903,902,901,900]

@pytest.mark.parametrize('raw',[False,True,1.0,None,1<<31,-(1<<31)-1])
def test_invalid_read_bool_never_treated_as_eof_or_success(monkeypatch,raw):
    from comsol_mcp import _windows_t037_network as n, _windows_owned_resources as owned
    state=transferred_getter_sdk(monkeypatch);f=state.query_fake;k=state.kernel
    def read(*args):f.log.append('bad-read');return raw
    monkeypatch.setattr(k,'ReadFile',read)
    with pytest.raises(owned.NativeResourceError):n.read_windows_ipv4_tcp_dynamic_range()
    q=n._dynamic_range_owner.process_owner
    assert q.state=='UNKNOWN' and not q.eof and f.log[-1]=='bad-read' and not state.closed

@pytest.mark.parametrize('first',['sdk_exception','invalid_return'])
def test_query_owner_reentry_raises_exact_first_cause_without_sdk(monkeypatch,first):
    from comsol_mcp import _windows_t037_network as n, _windows_owned_resources as owned
    state=transferred_getter_sdk(monkeypatch);f=state.query_fake;k=state.kernel
    if first=='sdk_exception':f.mode='exception:ReadFile:103'
    else:
        def bad(*args):f.log.append('bad-return');return True
        monkeypatch.setattr(k,'ReadFile',bad)
    with pytest.raises(owned.NativeResourceError):n.read_windows_ipv4_tcp_dynamic_range()
    owner=n._dynamic_range_owner;q=owner.process_owner;original=q.error
    assert original is not None and owner.operation_error is original
    snapshot=(list(f.log),len(q.calls),q.pending,list(state.closed))
    with pytest.raises(BaseException) as again:q.call(lambda:pytest.fail('late SDK'),())
    assert again.value is original and q.error is original and owner.operation_error is original
    with pytest.raises(BaseException) as again:q.unknown('secondary error')
    assert again.value is original and q.error is original and owner.operation_error is original
    q.clock=lambda:pytest.fail('clock used after first unknown')
    for repeat in (q.run,q.execute):
        with pytest.raises(BaseException) as again:repeat('ignored','ignored',{})
        assert again.value is original and q.error is original and owner.operation_error is original
    assert snapshot==(f.log,len(q.calls),q.pending,state.closed)

@pytest.mark.parametrize('raw',[0,False,True,1.0,None,1<<31,-(1<<31)-1])
def test_bool_invalid_or_zero_refuses_before_next_sdk(monkeypatch,raw):
    from comsol_mcp import _windows_t037_network as n, _windows_owned_resources as owned
    state=transferred_getter_sdk(monkeypatch);f=state.query_fake;k=state.kernel
    def bad(*args):f.log.append('bad-bool');return raw
    monkeypatch.setattr(k,'SetNamedPipeHandleState',bad)
    with pytest.raises(owned.NativeResourceError):n.read_windows_ipv4_tcp_dynamic_range()
    q=n._dynamic_range_owner.process_owner
    assert q.state=='UNKNOWN' and f.log[-1]=='bad-bool' and not state.closed
    assert 'InitializeProcThreadAttributeList' not in f.log


def test_signaled_exit259_never_satisfies_admission_revalidation(monkeypatch):
    from comsol_mcp import _windows_t037_network as n
    from comsol_mcp._windows_t037 import WindowsT037ProfileV2
    from tests.test_windows_network_intent import network_profile_and_plan
    state=transferred_getter_sdk(monkeypatch);state.query_fake.exit=259
    profile,_=network_profile_and_plan();intent=n.derive_session_network_intent(WindowsT037ProfileV2(profile,'d'*64,{},'SOFTWARE_ONLY'))
    with pytest.raises(Exception) as exc:n.revalidate_source_port_range(intent)
    assert exc.value.code=='T037_NETWORK_INTENT_INVALID' and state.query_fake.owner.state=='RELEASED'
    assert state.query_fake.log.count('WaitForSingleObject')==1 and state.closed==[903,902,901,900]
