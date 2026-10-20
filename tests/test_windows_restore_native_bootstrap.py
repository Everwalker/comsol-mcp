"""Production original preflight ownership at the task SDK boundary; software only."""
import ctypes as C,gc,weakref
import pytest
from tools.windows_restore_native_bootstrap import protected_descriptor
from tests.test_windows_host_preflight_owner import SDK,Function,install
from comsol_mcp import _windows_host_preflight_owner as preflight


@pytest.mark.parametrize('failure',['partial_raise','close_fail'])
def test_descriptor_actual_partial_output_original_cleanup_and_gc(monkeypatch,failure):
    sdk=SDK();install(monkeypatch,sdk);storage=C.create_string_buffer(bytes(32));original=sdk.primary
    def allocate(sddl,revision,out,length):
        C.cast(out,C.POINTER(C.c_void_p))[0]=C.addressof(storage)
        C.cast(length,C.POINTER(C.c_uint32))[0]=32
        # Mutating the DLL callback after allocation cannot replace original.
        sdk.k.LocalFree=Function(lambda *_:pytest.fail('replacement callback called'))
        if failure=='partial_raise':raise original
        return 1
    sdk.a.ConvertStringSecurityDescriptorToSecurityDescriptorW=Function(allocate)
    sdk.local_result=123
    initial={id(x) for x in preflight.retained_original_owners()}
    with pytest.raises(BaseException) as caught:
        with protected_descriptor('O:S-1-5-21-1D:P(A;OICI;FA;;;SY)'):
            assert failure=='close_fail'
    roots=[x for x in preflight.retained_original_owners() if id(x) not in initial]
    assert len(roots)==1 and roots[0].entries()
    owner=roots[0];api_ref=weakref.ref(owner.api)
    assert any(getattr(e.handle,'value',e.handle)==C.addressof(storage) for e in owner.entries()), (str(caught.value),[(e.label,e.handle) for e in owner.entries()],sdk.calls)
    if failure=='partial_raise':assert owner.first_cause is original
    assert [x for x in sdk.calls if isinstance(x,tuple) and x[0]=='free']==[('free',C.addressof(storage))]
    del owner,roots;gc.collect()
    assert api_ref() is not None and any(x.api is api_ref() for x in preflight.retained_original_owners())
    before=list(sdk.calls);gc.collect();assert sdk.calls==before
