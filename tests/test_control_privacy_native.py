"""Actual shared verifier wiring under the existing pure Windows SDK shim."""
import gc
import ctypes as C
import weakref
import os
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest

from comsol_mcp import _host_scoped_provider as provider
from comsol_mcp import _windows_host_preflight_owner as owned
from comsol_mcp._execution_contract import ExecutionContractError
from tests.test_windows_host_preflight_owner import SDK, Function, install


def control_verify(consume=lambda api, leaf, meta: meta):
    return provider._verify_windows_private_path(
        Path('C:/host/private/config'), directory=False, expected_identity=None,
        _control_callback=consume, _control_access=provider._CONTROL_PRIVACY_ACCESS)


def test_control_continuation_uses_real_complete_verifier_and_retains_pins(monkeypatch):
    sdk = SDK();install(monkeypatch, sdk)
    def consume(api, leaf, meta):
        owner = owned.hold_original(api)
        assert owner._control_strict is True and owner.entries()
        assert not any(isinstance(c, tuple) and c[0] == 'close' for c in sdk.calls)
        assert meta['security_semantics'] == provider.WINDOWS_PRIVATE_PATH_SECURITY_SEMANTICS
        return meta
    answer = control_verify(consume)
    assert answer['identity']['device'] == sdk.volume
    assert sdk.open_count == 4  # Two complete checks, both pairs retained until use ends.
    assert any(isinstance(c, tuple) and c[0] == 'close' for c in sdk.calls)


@pytest.mark.parametrize('fault', ['token_io','token_bool','token_false','sid_io','sid_bool','sid_false','descriptor_io','descriptor_bool','descriptor_status','relative_io','relative_bool','info_bool','second_identity'])
def test_control_unknown_retains_original_and_has_no_cleanup_sdk(monkeypatch, fault):
    sdk = SDK();sdk.fault = fault;install(monkeypatch, sdk)
    before = {id(x) for x in owned.retained_original_owners()}
    with pytest.raises(ExecutionContractError):
        control_verify(lambda *a: pytest.fail('unsafe use'))
    roots = [x for x in owned.retained_original_owners() if id(x) not in before]
    assert len(roots) == 1
    owner = roots[0]
    assert owner._control_held and owner.entries() and owner.first_cause is not None
    # Descriptor frees completed before the final identity comparison are
    # known operations, not cleanup after the first observed mismatch.
    forbidden = ('close',) if fault == 'second_identity' else ('close','free')
    assert not any(isinstance(c, tuple) and c[0] in forbidden for c in sdk.calls)
    api_ref = weakref.ref(owner.api);owner_ref = weakref.ref(owner)
    calls = list(sdk.calls)
    with pytest.raises(ExecutionContractError):
        owner.function('CloseHandle', sdk.k)
    assert sdk.calls == calls
    del roots, owner
    gc.collect()
    assert api_ref() is not None and owner_ref() is not None


def test_control_callback_exception_no_revalidation_or_resource_close(monkeypatch):
    sdk = SDK();install(monkeypatch, sdk)
    def failed(api, leaf, meta):
        sdk.calls.append('first-consumer-exception')
        raise OSError('uncertain original read')
    with pytest.raises(ExecutionContractError):
        control_verify(failed)
    assert sdk.calls[-1] == 'first-consumer-exception'


def test_control_close_unknown_stops_all_later_native_closers(monkeypatch):
    sdk = SDK();sdk.close_result = 0;install(monkeypatch, sdk)
    with pytest.raises(ExecutionContractError):
        control_verify()
    closes = [c for c in sdk.calls if isinstance(c, tuple) and c[0] == 'close']
    assert len(closes) == 1 and sdk.calls[-1] == closes[0]


def test_wrong_control_continuation_selector_before_api(monkeypatch):
    from comsol_mcp import _windows_parameter_publish as paths
    monkeypatch.setattr(paths, '_API', lambda: pytest.fail('SDK constructor'))
    with pytest.raises(ExecutionContractError):
        provider._verify_windows_private_path(Path('C:/private'), directory=True,
            expected_identity=None, _control_callback=lambda *a: None,
            _control_access=object())


@pytest.mark.parametrize('fault', ['none', 'different_full_id', 'hardlink', 'changed_full_metadata'])
def test_original_fd_observer_uses_real_full_identity_guard(monkeypatch, tmp_path, fault):
    path = tmp_path / 'synthetic-private-file'
    path.write_bytes(b'data')
    fd = os.open(path, os.O_RDONLY)
    original = os.fstat(fd)
    monkeypatch.setitem(sys.modules, 'msvcrt', SimpleNamespace(get_osfhandle=lambda current: 100001 if current == fd else pytest.fail('fd changed')))
    monkeypatch.setattr(provider, 'os', SimpleNamespace(name='nt', fstat=os.fstat))
    calls = []
    def getter(handle, *, directory):
        calls.append(handle)
        file_id = 2**90 + (43 if fault == 'different_full_id' and handle != 'leaf' else 42)
        changed = 5 if fault == 'changed_full_metadata' and len(calls) > 2 else 2
        return ((7, file_id), 4, 1, changed, 3, 2 if fault == 'hardlink' else 1)
    getter.borrowed = getter  # Isolated observer contract; composed real getter below.
    def actual_observer_route(named, **kwargs):
        assert named == path and kwargs['_control_access'] is provider._CONTROL_PRIVACY_ACCESS
        kwargs['_internal_stdio_observer'](None, 'leaf', getter)
        return {'identity': {'device':7,'inode':42}}
    monkeypatch.setattr(provider, '_verify_windows_private_path', actual_observer_route)
    try:
        if fault == 'none':
            assert provider._assert_opened_path_security(path, fd, original, directory=False,
                _control_access=provider._CONTROL_PRIVACY_ACCESS)['identity']['inode'] == 42
        else:
            with pytest.raises(ExecutionContractError):
                provider._assert_opened_path_security(path, fd, original, directory=False,
                    _control_access=provider._CONTROL_PRIVACY_ACCESS)
    finally:
        os.close(fd)  # Synthetic fixture fd only, not a Windows unknown owner.


def test_control_real_verifier_refuses_unsupported_volume_before_token_or_use(monkeypatch):
    sdk=SDK();install(monkeypatch,sdk)
    monkeypatch.setattr(provider, '_windows_volume_has_persistent_acls', lambda *a:(False,7))
    with pytest.raises(ExecutionContractError):
        control_verify(lambda *a:pytest.fail('unsupported volume used'))
    assert sdk.calls == []


@pytest.mark.parametrize('change_full_id', [False, True])
def test_control_root_uses_existing_full128_identity_across_consumption(monkeypatch, change_full_id):
    from comsol_mcp import _windows_parameter_publish as paths
    sdk = SDK();sdk.full_delta = 2**80
    def full(handle, kind, out, size):
        isdir, inode = sdk.handles[sdk.address(handle)]
        if kind == 18:
            row=C.cast(out,C.POINTER(paths._FullIdInfo)).contents
            row.volume=7;value=(sdk.full_delta+inode).to_bytes(16,'little')
            for i,b in enumerate(value):row.file_id[i]=b
        elif kind == 0:
            row=C.cast(out,C.POINTER(paths._BasicInfo)).contents
            row.attributes=16 if isdir else 128;row.created=1;row.changed=2;row.written=3
        elif kind == 1:
            row=C.cast(out,C.POINTER(paths._StandardInfo)).contents
            row.size=0;row.links=1;row.directory=isdir;row.delete_pending=False
        else:pytest.fail('new SDK information class')
        return 1
    sdk.k.GetFileInformationByHandleEx = Function(full)
    install(monkeypatch,sdk)
    monkeypatch.setattr(provider,'_safe_absolute',lambda value, field:Path(value))
    def consume(api,leaf,meta):
        assert meta['identity']['inode']>2**64
        if change_full_id:sdk.full_delta += 2**80
        return meta
    call=lambda:provider._with_control_private_path('C:/host/private',directory=True,consume=consume)
    if change_full_id:
        with pytest.raises(ExecutionContractError):call()
    else:
        assert call()['identity']['inode']>2**64


def _composed_borrowed_case(monkeypatch, tmp_path, *, fault=None,
                            fail_kind=None, failure='raise'):
    """Actual verifier/getter/owner, with only the existing OS/SDK boundary fake."""
    from comsol_mcp import _windows_parameter_publish as paths
    sdk=SDK();borrowed_handle=100001;sdk.borrowed_queries=0;sdk.observed_owners=[]
    sdk.borrowed_output_refs=[];sdk.query_error=OSError('synthetic borrowed query')
    def full(handle, kind, out, size):
        address=sdk.address(handle)
        owner=next(x for x in owned.retained_original_owners() if x.api.k is sdk.k)
        if address==borrowed_handle:
            # These buffers must already be strongly rooted BEFORE the first
            # actual getter call. A CRT handle never becomes a registry entry.
            assert owner.registry.find(owner.api,handle) is None
            storage,buffers=owner._borrowed_outputs[-1]
            assert sdk.address(storage)==borrowed_handle and len(buffers)==3
            assert any(C.addressof(value)==C.cast(out,C.c_void_p).value for value in buffers)
            sdk.observed_owners.append(owner)
            sdk.borrowed_output_refs.extend(weakref.ref(value) for value in buffers)
            sdk.borrowed_queries+=1
            isdir,inode=False,sdk.final_inode
        else:
            isdir,inode=sdk.handles[address]
            entry=owner.registry.find(owner.api,handle)
            assert entry is not None  # Original SDK path ownership is unchanged.
            assert any(isinstance(v, (paths._FullIdInfo,paths._BasicInfo,paths._StandardInfo))
                       for v in entry.keepalive)
        sdk.calls.append(('full',address,kind))
        if address==borrowed_handle and kind==fail_kind:
            if failure=='raise':raise sdk.query_error
            return False if failure=='malformed_bool' else 0
        if kind==18:
            row=C.cast(out,C.POINTER(paths._FullIdInfo)).contents
            row.volume=7;value=2**80+inode
            if address==borrowed_handle and fault=='identity':value+=2**80
            for index,b in enumerate(value.to_bytes(16,'little')):row.file_id[index]=b
        elif kind==0:
            row=C.cast(out,C.POINTER(paths._BasicInfo)).contents
            row.attributes=(16 if isdir else 128)
            if address==borrowed_handle and fault=='reparse':row.attributes|=0x400
            row.created=1;row.changed=2;row.written=3
            if address==borrowed_handle and fault=='changed_second' and sdk.borrowed_queries>3:
                row.changed=4
        elif kind==1:
            row=C.cast(out,C.POINTER(paths._StandardInfo)).contents
            row.size=0 if isdir else 4;row.links=1;row.directory=isdir;row.delete_pending=False
            if address==borrowed_handle and fault=='links':row.links=2
        else:pytest.fail('unexpected SDK information class')
        return 1
    sdk.k.GetFileInformationByHandleEx=Function(full)
    install(monkeypatch,sdk)
    path=tmp_path/'synthetic-original-file';path.write_bytes(b'data')
    fd=os.open(path,os.O_RDONLY);info=os.fstat(fd)
    monkeypatch.setitem(sys.modules,'msvcrt',SimpleNamespace(
        get_osfhandle=lambda actual:borrowed_handle if actual==fd else pytest.fail('foreign fd')))
    monkeypatch.setattr(provider,'os',SimpleNamespace(name='nt',fstat=os.fstat))
    def call():
        return provider._assert_opened_path_security(Path('C:/host/private/config'),fd,info,
            directory=False,_control_access=provider._CONTROL_PRIVACY_ACCESS)
    return sdk,borrowed_handle,fd,call


def test_composed_real_getter_accepts_distinct_borrowed_CRT_without_adoption(monkeypatch,tmp_path):
    sdk,borrowed_handle,fd,call=_composed_borrowed_case(monkeypatch,tmp_path)
    try:
        answer=call()
        assert answer['identity']['inode']==2**80+sdk.final_inode
        assert sdk.borrowed_queries==6  # Original observation and same-handle repeat.
        owner=sdk.observed_owners[0]
        assert owned.confirmed_original_cleanup(owner)
        assert owner.registry.find(owner.api,borrowed_handle) is None
        assert not any(c==('close',borrowed_handle) for c in sdk.calls)
        assert owner._borrowed_outputs and all(r() is not None for r in sdk.borrowed_output_refs)
    finally:
        os.close(fd)  # Local synthetic fixture only, never a Windows SDK handle.


@pytest.mark.parametrize('kind',[18,0,1])
@pytest.mark.parametrize('failure',['raise','zero','malformed_bool'])
def test_composed_borrowed_query_unknown_roots_outputs_and_zero_later_SDK(monkeypatch,tmp_path,kind,failure):
    sdk,borrowed_handle,fd,call=_composed_borrowed_case(monkeypatch,tmp_path,
        fail_kind=kind,failure=failure)
    try:
        with pytest.raises(ExecutionContractError):call()
        owner=sdk.observed_owners[0]
        assert owner._control_held and owner.entries() and owner.first_cause is not None
        assert owner.registry.find(owner.api,borrowed_handle) is None
        assert sdk.calls[-1]==('full',borrowed_handle,kind)
        assert [c[2] for c in sdk.calls if isinstance(c,tuple) and c[:2]==('full',borrowed_handle)]==(
            [18,0,1][: [18,0,1].index(kind)+1])
        before=list(sdk.calls);ref=weakref.ref(owner);gc.collect()
        assert ref() is not None and all(r() is not None for r in sdk.borrowed_output_refs)
        with pytest.raises(ExecutionContractError):owner.function('CloseHandle',sdk.k)
        assert sdk.calls==before
        assert not any(c==('close',borrowed_handle) for c in sdk.calls)
    finally:
        os.close(fd)  # Independent local fixture; no synthetic SDK cleanup.


@pytest.mark.parametrize('fault',['identity','links','reparse','changed_second'])
def test_composed_borrowed_identity_links_attributes_still_fail_closed(monkeypatch,tmp_path,fault):
    sdk,borrowed_handle,fd,call=_composed_borrowed_case(monkeypatch,tmp_path,fault=fault)
    try:
        with pytest.raises(ExecutionContractError):call()
        owner=sdk.observed_owners[0]
        assert owner._control_held and owner.registry.find(owner.api,borrowed_handle) is None
        assert sdk.calls[-1]==('full',borrowed_handle,1)
        assert all(r() is not None for r in sdk.borrowed_output_refs)
        assert not any(c==('close',borrowed_handle) for c in sdk.calls)
    finally:
        os.close(fd)
