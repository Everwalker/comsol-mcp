"""Actual task helper + original production preflight owners, SDK standins only."""
import ctypes as C,gc,weakref
from types import SimpleNamespace
import pytest
from tools import windows_task_thread_owner as task
from tests.test_windows_host_preflight_owner import SDK,Function,install
from comsol_mcp._windows_t037_acl import TOKEN_STATISTICS


def sid(text):
    p=list(map(int,text.split('-')[1:]));return bytes((p[0],len(p)-2))+p[1].to_bytes(6,'big')+b''.join(x.to_bytes(4,'little') for x in p[2:])


class TokenSDK(SDK):
    def __init__(self,fault=None,prior=False):
        super().__init__();self.error=0;self.fault_mode=fault;self.next=400;self.thread=200 if prior else None
        self.tokens={100:dict(user=sid('S-1-5-18'),owner=sid('S-1-5-32-544'),tid=17,type=1,level=0),200:dict(user=sid('S-1-5-18'),owner=sid('S-1-5-32-544'),tid=22,type=2,level=2)}
        self.closed=[];self.attaches=0
        self.k.GetCurrentThread=Function(lambda:-2);self.k.GetCurrentProcessId=Function(lambda:812)
        self.a.OpenProcessToken=Function(self.process);self.a.OpenThreadToken=Function(self.open_thread);self.a.DuplicateTokenEx=Function(self.duplicate)
        self.a.GetTokenInformation=Function(self.info);self.a.SetTokenInformation=Function(self.set_owner)
        self.a.SetThreadToken=Function(self.attach);self.a.RevertToSelf=Function(self.revert)
        self.a.IsValidSid=Function(lambda p:1);self.a.GetLengthSid=Function(lambda p:8+4*C.string_at(p,8)[1])
    def process(self,process,access,out):
        assert access==0x0a;C.cast(out,C.POINTER(C.c_void_p))[0]=100;self.calls.append('OpenProcessToken');return 1
    def open_thread(self,thread,access,selfopen,out):
        self.calls.append('OpenThreadToken')
        if self.thread is None:self.error=1008;return 0
        self.next+=1;self.tokens[self.next]=self.tokens[self.thread];C.cast(out,C.POINTER(C.c_void_p))[0]=self.next;return 1
    def duplicate(self,h,access,attrs,level,kind,out):
        assert access==0x8c and attrs is None and level==kind==2
        self.tokens[300]={**self.tokens[self.address(h)],'tid':33,'type':2,'level':2};C.cast(out,C.POINTER(C.c_void_p))[0]=300;self.calls.append('DuplicateTokenEx')
        if self.fault_mode=='partial_duplicate':raise self.primary
        return True if self.fault_mode=='malformed_duplicate' else 0 if self.fault_mode=='false_duplicate' else 1
    def info(self,h,kind,buf,size,needed):
        token=self.tokens[self.address(h)];extent=C.sizeof(TOKEN_STATISTICS) if kind==10 else 40 if kind in (1,4) else 16
        C.cast(needed,C.POINTER(C.c_uint32))[0]=extent
        if buf is None:self.error=122;return 0
        if kind in (1,4):
            raw=token['user' if kind==1 else 'owner'];start=C.addressof(buf)+16;C.memmove(start,raw,len(raw));C.cast(buf,C.POINTER(C.c_void_p))[0]=start
        elif kind==10:
            st=TOKEN_STATISTICS();st.TokenId.LowPart=token['tid'];st.AuthenticationId.LowPart=29;st.TokenType=token['type'];st.ImpersonationLevel=token['level'];C.memmove(buf,C.byref(st),C.sizeof(st))
        else:C.memmove(buf,bytes(16),16)
        return 1
    def set_owner(self,h,kind,owner,size):
        assert self.address(h)==300 and kind==4;ptr=C.cast(owner,C.POINTER(C.c_void_p)).contents.value
        self.tokens[300]['owner']=C.string_at(ptr,8+4*C.string_at(ptr,8)[1]);self.calls.append('SetTokenOwner:duplicate')
        return 0 if self.fault_mode=='false_owner' else 1
    def attach(self,thread,h):
        assert thread is None;self.attaches+=1;self.calls.append('SetThreadToken');self.thread=self.address(h)
        if self.fault_mode=='restore_fail' and self.attaches==2:return 0
        return True if self.fault_mode=='malformed_attach' else 0 if self.fault_mode=='false_attach' else 1
    def revert(self):
        self.calls.append('RevertToSelf')
        if self.fault_mode=='restore_fail':return 0
        self.thread=None;return 1
    def close(self,h):
        self.closed.append(self.address(h));self.calls.append(('close',self.address(h)))
        return 0 if self.fault_mode=='close_fail' else 1


def setup(monkeypatch,fault=None,prior=False):
    sdk=TokenSDK(fault,prior);install(monkeypatch,sdk);monkeypatch.setattr(C,'get_last_error',lambda:sdk.error,raising=False)
    monkeypatch.setattr(task,'_UNKNOWN_THREAD_OWNERS',[]);return sdk


@pytest.mark.parametrize('prior',[False,True])
def test_duplicate_only_owner_thread_scope_restored_original(monkeypatch,prior):
    sdk=setup(monkeypatch,prior=prior);primary=dict(sdk.tokens[100])
    with task.task_thread_owner() as receipt:
        assert sdk.thread is not None and sdk.tokens[sdk.thread]['owner']==sdk.tokens[100]['user']
        assert sdk.tokens[100]==primary and receipt['UserSID']=='S-1-5-18' and not receipt['restored']
    assert receipt['restored'] and sdk.tokens[100]==primary and (sdk.tokens[sdk.thread] is sdk.tokens[200] if prior else sdk.thread is None)
    assert not task._UNKNOWN_THREAD_OWNERS and len(sdk.closed)==len(set(sdk.closed))


@pytest.mark.parametrize('fault',['partial_duplicate','malformed_duplicate','false_duplicate','false_owner','false_attach','malformed_attach','restore_fail','close_fail'])
def test_critical_faults_no_next_effects_original_owner_strong_gc(monkeypatch,fault):
    sdk=setup(monkeypatch,fault);primary=dict(sdk.tokens[100]);entered=[]
    with pytest.raises(BaseException):
        with task.task_thread_owner():entered.append(True)
    assert sdk.tokens[100]==primary
    if fault not in ('restore_fail','close_fail'):assert not entered
    if fault in ('partial_duplicate','malformed_duplicate','false_duplicate','false_attach','malformed_attach','restore_fail','close_fail'):
        assert len(task._UNKNOWN_THREAD_OWNERS)==1;record=task._UNKNOWN_THREAD_OWNERS[0];api=weakref.ref(record['api']);first=record['first_cause'];assert first is not None
        del record;gc.collect();assert api() is not None
        calls=list(sdk.calls)
        with pytest.raises(RuntimeError):
            with task.task_thread_owner():pytest.fail('unknown token reopened')
        assert sdk.calls==calls
        if fault=='partial_duplicate':assert first is sdk.primary and 300 not in sdk.closed
