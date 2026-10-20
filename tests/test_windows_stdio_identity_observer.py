"""Production stdio adapter with controlled SDK standins; no native Win PASS."""
import ctypes as C,importlib.util,os,sys
from pathlib import Path
from types import SimpleNamespace
import pytest
from tests.test_windows_host_preflight_owner import SDK,Function,install
from comsol_mcp import _host_scoped_provider as provider,_windows_parameter_publish as paths


class FullSDK(SDK):
    def __init__(self,mode):
        super().__init__();self.mode=mode;self.count=0;self.borrowed=900
        self.handles[900]=(False,22);self.handles[901]=(False,23)
        self.k.GetFileInformationByHandleEx=Function(self.full)
    def full(self,h,kind,out,size):
        value=self.address(h);directory,inode=self.handles[value]
        if kind==18:
            v=C.cast(out,C.POINTER(paths._FullIdInfo)).contents;v.volume=(1<<61)+7
            full=(1<<120)+inode
            if value==900 and self.mode=='wrong_full_id':full+=1<<80
            if value==900 and self.mode=='wrong_volume':v.volume+=1<<40
            for n,b in enumerate(full.to_bytes(16,'little')):v.file_id[n]=b
        elif kind==0:
            v=C.cast(out,C.POINTER(paths._BasicInfo)).contents;v.attributes=16 if directory else 128;v.created=5;v.written=7;v.changed=8
            if value==900:
                self.count+=1
                if self.mode=='change_time' and self.count==2:v.changed=9
                if self.mode=='reparse':v.attributes|=0x400
                if self.mode=='wrong_directory':v.attributes=16
            if value==900 and self.mode=='replace_callback':
                self.k.GetFileInformationByHandleEx=Function(lambda *a:pytest.fail('mutable replacement must not be used'))
        elif kind==1:
            v=C.cast(out,C.POINTER(paths._StandardInfo)).contents;v.size=0 if directory else 1;v.links=1;v.directory=directory
        return True if self.mode=='malformed_getter' else 1


def bind(monkeypatch,mode):
    sdk=FullSDK(mode);install(monkeypatch,sdk)
    info=SimpleNamespace(st_dev=(1<<61)+7,st_ino=(1<<120)+22,st_size=1,st_mtime_ns=7,st_ctime_ns=8)
    calls=[0]
    def descriptor(fd):
        assert fd==37;calls[0]+=1
        return 901 if mode=='reuse_fd' and calls[0]>1 else 901 if mode=='foreign_fd' else 900
    monkeypatch.setitem(sys.modules,'msvcrt',SimpleNamespace(get_osfhandle=descriptor))
    monkeypatch.setattr(provider,'os',SimpleNamespace(name='nt',fstat=lambda fd:info))
    return sdk,info


@pytest.mark.parametrize('mode',['positive','replace_callback','foreign_fd','wrong_full_id','wrong_volume','change_time','reparse','wrong_directory','reuse_fd','malformed_getter'])
def test_same_native_domain_borrowed_fd_not_acquired_or_closed(monkeypatch,mode):
    sdk,info=bind(monkeypatch,mode)
    if mode in ('positive','replace_callback'):
        value=provider._assert_opened_path_security(Path('C:/host/private/config'),37,info,directory=False)
        assert value['identity']['device']==7 and value['identity']['inode']==22  # legacy metadata retained
    else:
        with pytest.raises(provider.ExecutionContractError):provider._assert_opened_path_security(Path('C:/host/private/config'),37,info,directory=False)
    closed=[x[1] for x in sdk.calls if isinstance(x,tuple) and x[0]=='close']
    assert 900 not in closed and 901 not in closed and len(closed)==len(set(closed))
