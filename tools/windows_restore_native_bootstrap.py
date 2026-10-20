"""Task-only protected namespace descriptor allocation; no engine or authority.

The native output belongs to an independent original preflight owner before
allocation. This module never changes an existing path's security descriptor.
"""
from contextlib import contextmanager
import ctypes as C
from comsol_mcp._windows_parameter_publish import _API
from comsol_mcp._windows_host_preflight_owner import hold_original,scalar,finish_original


@contextmanager
def protected_descriptor(sddl):
    if type(sddl) is not str or not sddl.startswith('O:S-1-5-21-') or 'D:P' not in sddl:
        raise ValueError('task protected owner/DACL literal required')
    api=_API();owner=hold_original(api)
    descriptor=C.c_void_p();length=C.c_uint32();primary=None
    try:
        advapi=C.WinDLL('advapi32',use_last_error=True)
        advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes=[C.c_wchar_p,C.c_uint32,C.POINTER(C.c_void_p),C.POINTER(C.c_uint32)]
        advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype=C.c_int32
        api.k.LocalFree.argtypes=[C.c_void_p];api.k.LocalFree.restype=C.c_void_p
        # Freeze the original callback before the SDK can write any output.
        owner.function('LocalFree',api.k)
        ok=scalar(owner.allocation_call('ConvertStringSecurityDescriptorToSecurityDescriptorW',advapi,descriptor,
            sddl,1,C.byref(descriptor),C.byref(length),family='local',keepalive=(sddl,length)),'BOOL')
        if not ok or not descriptor.value or not 20<=length.value<=1024*1024:
            raise ValueError('protected descriptor native allocation was not valid')
        yield descriptor,owner
    except BaseException as failure:
        primary=failure;owner.remember(failure);raise
    finally:
        # Original partial output is retained on unconfirmed release. No retry,
        # dynamic callback lookup, pathname reconstruction or owner adoption.
        finish_original(api,primary)
