"""Production API/registry tests at an explicit SDK boundary, not native PASS."""
import ctypes as C
import gc
from types import SimpleNamespace
import weakref

import pytest
from comsol_mcp import _windows_parameter_publish as publish
from comsol_mcp._windows_owned_resources import registry_for


@pytest.mark.parametrize('mode',['success','replaced_function','negative','pending','malformed','raise_after_out','incomplete_iosb'])
def test_original_nt_relative_rename_and_unknown_owner(mode,monkeypatch):
    api=publish._API.__new__(publish._API);calls=[];closed=[];source=C.c_void_p(101);parent=C.c_void_p(202)
    api.k=SimpleNamespace(CloseHandle=lambda handle:closed.append(handle) or 1)
    def native(handle,iosb,buffer,length,kind):
        calls.append(kind);info=C.cast(buffer,C.POINTER(publish._RenameInfo)).contents
        assert handle is source and info.RootDirectory==202 and info.ReplaceIfExists==0 and kind==10
        name=C.string_at(C.addressof(buffer)+publish._RenameInfo.FileName.offset,info.FileNameLength).decode('utf-16-le')
        assert name=='目标-file' and length>=publish._RenameInfo.FileName.offset+info.FileNameLength
        status=iosb._obj;status.Status=0;status.Information=0
        if mode=='replaced_function':api.nt.NtSetInformationFile=lambda *a:pytest.fail('replacement invoked')
        if mode=='negative':return -1073741771
        if mode=='pending':return 0x103
        if mode=='malformed':return True
        if mode=='raise_after_out':raise OSError('actual SDK boundary wrote then raised')
        if mode=='incomplete_iosb':status.Status=0x7fffffff
        return 0
    api.nt=SimpleNamespace(NtSetInformationFile=native,RtlNtStatusToDosError=lambda status:183)
    monkeypatch.setattr(publish.C,'WinError',lambda code:OSError(code,'confirmed NT refusal'),raising=False)
    registry=registry_for(api);scope=registry.scope();entries=[registry.acquire(api,h,api.k.CloseHandle,mode='BOOL') for h in (source,parent)]
    if mode in ('success','replaced_function'):
        api.rename(source,parent,'目标-file');assert calls==[10]
        api.close(source);api.close(parent);assert len(closed)==2
    elif mode=='negative':
        with pytest.raises(OSError):api.rename(source,parent,'目标-file')
        assert not getattr(api,'_rename_unknown_record',None)
        api.close(source);api.close(parent);assert len(closed)==2
    else:
        with pytest.raises(publish.PublishError) as error:api.rename(source,parent,'目标-file')
        assert error.value.code=='EXECUTION_STATE_UNKNOWN'
        owner=api._rename_unknown_record;ref=weakref.ref(api);del api;gc.collect();assert ref() is owner['api']
        assert owner in publish._UNKNOWN_RENAME_OWNERS
        assert all(entry.state=='UNKNOWN' for entry in entries)
        assert len(registry.drain(scope))==2 and not closed
        for effect in (lambda:owner['api'].close(source),lambda:owner['api'].rename(source,parent,'again'),lambda:owner['api'].delete_owned(source),lambda:owner['api'].write(source,b'x'),lambda:owner['api']._open_relative(parent,'new')):
            with pytest.raises(publish.PublishError):effect()
        assert not closed and calls==[10]


def test_alias_rejected_without_native_effect():
    api=publish._API.__new__(publish._API)
    api.nt=SimpleNamespace(NtSetInformationFile=lambda *a:pytest.fail('native effect'))
    with pytest.raises(publish.PublishError):api.rename(101,202,'../foreign')


def test_standalone_unknown_keeps_original_api_closer_and_late_finally_handles(monkeypatch):
    api=publish._API.__new__(publish._API);closed=[]
    original_close=lambda handle:closed.append(handle) or 1
    api.k=SimpleNamespace(CloseHandle=original_close)
    def uncertain(*a):raise OSError('uncertain original NT call')
    api.nt=SimpleNamespace(NtSetInformationFile=uncertain,RtlNtStatusToDosError=lambda status:1)
    monkeypatch.setattr(publish,'__package__',None)
    with pytest.raises(publish.PublishError):api.rename(101,202,'target')
    record=api._rename_unknown_record;assert record['registry'] is None and record['original_close'] is original_close
    api.k.CloseHandle=lambda *a:pytest.fail('replacement closer called')
    with pytest.raises(publish.PublishError):api.close(303)
    assert record['original_handles']==[101,202,303] and record in publish._UNKNOWN_RENAME_OWNERS and not closed
