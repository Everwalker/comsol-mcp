"""SOFTWARE_ONLY closed SD/MIC and raw ABI controls; no Windows call."""
import ctypes as C
import struct
import sys
from types import SimpleNamespace

import pytest

from comsol_mcp import _windows_scm_publisher_native as n

SERVICE='S-1-5-80-1-2-3-4-5'
USER='S-1-5-21-100-200-300-1001'
HOME=r'C:\ProgramData\ComsolMCP_Test\service-state'
PROFILE=r'C:\ProgramData\ComsolMCP_Test\t037.json'


def sid(value):return n.e.b._sid_bytes(value)


def descriptor(*,directory,reader=USER,principal=SERVICE,mask=0x1301BF,reader_flags=0,owner=n.e.SY,group=n.e.SY,extra=()):
    flags=3 if directory else 0
    rows=[(0,flags,0x1F01FF,n.e.SY),(0,flags,0x1F01FF,n.e.BA),(0,flags,mask,principal)]
    if reader:rows.append((0,reader_flags,0x1200A0 if directory else 0x120089,reader))
    rows.extend(extra)
    entries=b''.join(struct.pack('<BBHI',k,f,8+len(sid(s)),m)+sid(s) for k,f,m,s in rows)
    acl=struct.pack('<BBHHH',2,0,8+len(entries),len(rows),0)+entries
    o,g=sid(owner),sid(group)
    return struct.pack('<BBHIIII',1,0,0x9004,20,20+len(o),0,20+len(o)+len(g))+o+g+acl


def mic(level=8192,mask=1,kind=17,flags=0):
    s=sid('S-1-16-'+str(level));entry=struct.pack('<BBHI',kind,flags,8+len(s),mask)+s
    acl=struct.pack('<BBHHH',2,0,8+len(entry),1,0)+entry
    return struct.pack('<BBHIIII',1,0,0x8010,0,0,20,0)+acl


@pytest.mark.parametrize('reader',[None,USER])
@pytest.mark.parametrize('directory',[False,True])
def test_closed_state_and_endpoint_template(reader,directory):
    raw=descriptor(directory=directory,reader=reader)
    assert n.template(raw,SERVICE,directory=directory,reader=reader)==reader


@pytest.mark.parametrize('change',[
    dict(reader_flags=3),dict(owner=USER),dict(group=USER),dict(principal='S-1-5-80-9-8-7-6-5'),
    dict(mask=0x1F01FF),dict(mask=0x10000000),dict(mask=0x1000000),dict(reader='S-1-15-2-1'),
    dict(reader='S-1-1-0'),dict(extra=((0,0,0x1200A0,'S-1-5-21-1-2-3-1002'),)),
    dict(extra=((1,0,0x1F01FF,USER),)),dict(extra=((0,3,0x1301BF,SERVICE),))])
def test_closed_home_template_rejects_ambiguous_or_extra_policy(change):
    with pytest.raises(n.PublisherError):n.template(descriptor(directory=True,**change),SERVICE,directory=True)


@pytest.mark.parametrize('level',[8192,12288,16384])
def test_actual_label_parser_nw_only_medium_or_higher(level):assert n.label(mic(level))==level


@pytest.mark.parametrize('raw',[b'',mic(4096),mic(mask=3),mic(mask=5),mic(kind=0),mic(flags=3),mic()[:-1]])
def test_label_missing_low_nr_nx_or_bounds_never_become_observation(raw):
    with pytest.raises(n.PublisherError):n.label(raw)


def test_native_constructor_binds_closed_signatures_without_invoking_any_sdk(monkeypatch):
    class Function:
        def __call__(self,*a):pytest.fail('SDK call in constructor')
    class Library:
        def __init__(self,name):self.name=name
        def __getattr__(self,name):
            assert name in n.SPECS[self.name]
            return Function()
    monkeypatch.setattr(n,'os',SimpleNamespace(name='nt'))
    monkeypatch.setattr(C,'WinDLL',lambda name,**kw:Library(name),raising=False)
    monkeypatch.setattr(C,'get_last_error',lambda:0,raising=False)
    monkeypatch.setitem(sys.modules,'msvcrt',SimpleNamespace(get_osfhandle=lambda _:1))
    api=n._Api()
    assert set(api.sdk)==set(n.EXPORTS)
    assert api.sdk['NtSetInformationFile'].argtypes==[n.H,C.POINTER(n.p._IoStatusBlock),n.H,n.D,n.B]
    assert api.sdk['LookupAccountSidW'].restype is n.B
    assert not any(x in api.sdk for x in ('SetSecurityInfo','AdjustTokenPrivileges','CreateServiceW','DeleteService'))


@pytest.mark.parametrize('operation',['GetSecurityInfo','ConvertStringSecurityDescriptorToSecurityDescriptorW'])
@pytest.mark.parametrize('outcome',['failure','lost','opaque'])
def test_written_allocation_is_not_owned_without_normalized_original_success(monkeypatch,operation,outcome):
    monkeypatch.setattr(n,'_OWNERS',{})
    kept=C.create_string_buffer(32);calls=[]
    def dispatch(*args):
        out=args[-1] if operation=='GetSecurityInfo' else args[2]
        C.cast(out,C.POINTER(n.H))[0]=C.addressof(kept)
        if outcome=='lost':raise OSError('software written output return lost')
        if outcome=='opaque':return object()
        return 5 if operation=='GetSecurityInfo' else 0
    def free(*args):calls.append(args);return None
    exports={name:(lambda *args:0) for name in n.EXPORTS}
    exports[operation]=dispatch;exports['LocalFree']=free
    api=n._SoftwareApi(exports,lambda:0,lambda fd:1,{},seal=n._SOFTWARE)
    owner=n._Owner(api,HOME)
    with pytest.raises(Exception):
        owner.output(operation,'LocalFree',
            lambda out:((n.H(42),1,7,None,None,None,None,C.byref(out)) if operation=='GetSecurityInfo'
                else ('not-a-host-call',1,C.byref(out),None)),status=operation=='GetSecurityInfo')
    resource=owner.resources[-1]
    assert resource.storage.value==C.addressof(kept) and not resource.owned
    with pytest.raises(Exception):owner.finish()
    assert not calls and n._OWNERS[id(owner)] is owner


def test_successful_allocation_with_invalid_sd_can_be_freed_once(monkeypatch,tmp_path):
    from tests.test_windows_scm_publisher import SDK,Model,software_api
    monkeypatch.setattr(n,'_OWNERS',{})
    model=Model(tmp_path);sdk=SDK(model);api=software_api(sdk,tmp_path,tmp_path/'profile.json')
    owner=n._Owner(api,HOME);h=n.H(sdk.handle(('file',model.files[HOME])))
    model.files[HOME].sd=b'\0'*24
    with pytest.raises(Exception):n.e._descriptor(owner,h)
    resource=owner.resources[-1]
    assert resource.owned and not owner.opaque
    owner.finish()
    assert sdk.native_effects.count(('free',resource.value))==1 and not n._OWNERS


def test_mutable_buffers_and_native_widths_are_fixed_before_call():
    assert C.sizeof(n.D)==4 and C.sizeof(n.B)==4 and C.sizeof(n.H)==8
    assert n.p._UnicodeString.Buffer.offset==8 and C.sizeof(n.p._UnicodeString)==16
    assert n.p._ObjectAttributes.RootDirectory.offset==8 and C.sizeof(n.p._ObjectAttributes)==48
    assert n.p._RenameInfo.RootDirectory.offset==8 and n.p._RenameInfo.FileName.offset==20
    assert C.sizeof(n.p._IoStatusBlock)==16
