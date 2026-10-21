import ctypes as C
import hashlib,gc,weakref
import pytest
from pathlib import Path
from tests.test_windows_host_preflight_owner import SDK,install,Function
from comsol_mcp import _windows_service_sid as leaf,_windows_parameter_publish as paths,_windows_host_preflight_owner as owned

class BackupSDK(SDK):
    def __init__(self):
        super().__init__();self.raw=leaf.canonical({'schema':leaf.BACKUP_SCHEMA,'public':'synthetic'})
        self.offset=0;self.read_fault=None;self.k.ReadFile=Function(self.read)
    def info(self,h,out):
        result=super().info(h,out);row=C.cast(out,C.POINTER(paths._FileInfo)).contents
        row.links=1;row.size_low=0 if row.attributes&16 else len(self.raw)
        if self.read_fault=='identity' and self.offset:row.write.low=1
        return result
    def read(self,h,buffer,size,count,overlapped):
        assert self.address(h) in self.handles
        self.calls.append(('read',size));raw=self.raw[self.offset:self.offset+size]
        C.memmove(buffer,raw,len(raw));self.offset+=len(raw)
        C.cast(count,C.POINTER(C.c_uint32))[0]=size+1 if self.read_fault=='count' else 0 if self.read_fault=='short' else len(raw)
        if self.read_fault=='io':raise self.primary
        return False if self.read_fault=='bool' else 0 if self.read_fault=='false' else -1

def read(sdk):return leaf.read_backup('C:/host/private/backup',hashlib.sha256(sdk.raw).hexdigest())

def test_original_backup_full_chain_pinned_read_and_eof(monkeypatch):
    before={id(owner) for owner in owned.retained_original_owners()}
    sdk=BackupSDK();install(monkeypatch,sdk);result=read(sdk)
    assert result['document']['public']=='synthetic'
    assert len([r for r in sdk.calls if isinstance(r,tuple) and r[0]=='read'])==2
    assert sdk.open_count==4 and not [owner for owner in owned.retained_original_owners() if id(owner) not in before]
    assert all(x[0]!='write' for x in sdk.calls if isinstance(x,tuple))

@pytest.mark.parametrize('fault',['count','short','io','bool','false','identity'])
def test_original_backup_read_fault_refused_before_authority(monkeypatch,fault):
    before={id(owner) for owner in owned.retained_original_owners()}
    sdk=BackupSDK();sdk.read_fault=fault;install(monkeypatch,sdk)
    with pytest.raises(Exception):read(sdk)
    roots=[r for r in owned.retained_original_owners() if r.first_cause and id(r) not in before]
    for root in roots:owned.cleanup_original(root.api)

@pytest.mark.parametrize('raw',[b'{"x":1,"x":1}',b'{ "x":1}',b'{"password":"public"}',b'{"x":NaN}'])
def test_actual_backup_canonical_secret_duplicate_negative(monkeypatch,raw):
    sdk=BackupSDK();sdk.raw=raw;install(monkeypatch,sdk)
    with pytest.raises(Exception):read(sdk)


def test_backup_after_write_fault_close_unknown_gc_original_closer(monkeypatch):
    sdk=BackupSDK();sdk.read_fault='io';sdk.close_result=0;install(monkeypatch,sdk)
    with pytest.raises(Exception) as caught:read(sdk)
    root=caught.value._original_preflight_owner;api=root.api;ref=weakref.ref(api)
    sdk.k.CloseHandle=Function(lambda *args:(_ for _ in ()).throw(AssertionError('replacement closer used')))
    del api;gc.collect();assert ref() is root.api and root.registry.unresolved()
    assert root.first_cause is sdk.primary
    sdk.close_result=1;owned.cleanup_original(root.api)
    assert root.retired and not root.registry.unresolved()
