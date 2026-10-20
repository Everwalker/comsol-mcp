"""Software policy controls for actual observed Windows ancestor ACL masks.
No Windows kernel PASS is inferred from these scalar reducer tests.
"""
import pytest
from comsol_mcp._host_scoped_provider import _validate_windows_acl_policy as check
from comsol_mcp._execution_contract import ExecutionContractError


def gate(rows, *, ancestor=True, installer=False, owner=True, private=False):
    return check(owner_trusted=owner, owner_is_daemon=owner,
                 require_daemon_owner=private, dacl_present=True,
                 ace_rows=rows, pinned_ancestor_traversal=ancestor,
                 owner_is_trustedinstaller=installer)


def row(mask,flags=0,kind=0,trusted=False):
    return dict(type=kind,flags=flags,sid_trusted=trusted,access_mask=mask)


@pytest.mark.parametrize('mask',[1,2,4,8,0x20,0x80,0x20000,0x100000,0x1200a9,0x80000000,0x20000000,0xa0000000,0])
def test_untrusted_readonly_masks_accepted_only_on_ancestor(mask):
    gate([row(mask)])
    with pytest.raises(ExecutionContractError):gate([row(mask)],ancestor=False)


@pytest.mark.parametrize('mask',[0x10,0x40,0x100,0x10000,0x40000,0x80000,0x40000000,0x10000000,0x2000000,0x1000000,0x200,0xffffffff])
def test_untrusted_effective_mutation_unknown_or_generic_write_refused(mask):
    with pytest.raises(ExecutionContractError):gate([row(mask)])
    with pytest.raises(ExecutionContractError):gate([row(mask,kind=1),row(mask)])


@pytest.mark.parametrize('mask',[0x40000000,0x10000000,0xe0010000])
def test_inherit_only_never_authorizes_private_children(mask):
    gate([row(mask,flags=0x0b)])
    with pytest.raises(ExecutionContractError):gate([row(mask,flags=0x0b)],ancestor=False)
    with pytest.raises(ExecutionContractError):gate([row(mask,flags=0x03)])


def test_installer_owner_is_only_pinned_ancestor_traversal_role():
    gate([row(0x1200a9)],owner=False,installer=True)
    with pytest.raises(ExecutionContractError):gate([],owner=False,installer=True,ancestor=False)
    with pytest.raises(ExecutionContractError):gate([],owner=False,installer=True,private=True)
    with pytest.raises(ExecutionContractError):gate([],owner=False,installer=False)


@pytest.mark.parametrize('r',[row(1,kind=5),row(1,kind=9),row(1,flags=0x20),row(True),row(-1),row(2**32),row(0x2000000,flags=0x0b),{'type':0,'flags':0,'sid_trusted':False}])
def test_unsupported_and_malformed_rows_refuse(r):
    with pytest.raises(ExecutionContractError):gate([r])


def test_actual_c_drive_descriptor_add_sibling_does_not_grant_private_owner():
    # Actual C:\ observed O:TrustedInstaller,(A;;LC;;;AU),(A;OICIIO;SDGXGWGR;;;AU),
    # (A;OICI;0x1200a9;;;BU),capability0x1000a1. LC=0x4 for files/directories.
    original=[row(4),row(0xe0010000,flags=0x0b),row(0x1200a9,flags=3),row(0x1000a1)]
    gate(original,owner=False,installer=True)
    with pytest.raises(ExecutionContractError):gate(original,owner=False,installer=True,ancestor=False)
    # Without the unsafe effective mask, readonly and inherit-only are accepted.
    gate(original[1:],owner=False,installer=True)
    with pytest.raises(ExecutionContractError):gate(original[1:],owner=False,installer=True,ancestor=False)


def test_actual_users_descriptor_read_inherit_only_allowed_but_never_private():
    rows=[row(0xa0000000,flags=0x0b),row(0x1200a9),row(0x100021),row(0xa0000000,flags=0x0b)]
    gate(rows)
    with pytest.raises(ExecutionContractError):gate(rows,ancestor=False)


@pytest.mark.parametrize("allocation_unknown", [False, True])
def test_native_descriptor_role_uses_exact_separately_owned_installer_sid(monkeypatch,allocation_unknown):
    """Real production parser/preflight/registry; SDK boundary is a standin."""
    import ctypes as C
    from tests.test_windows_host_preflight_owner import SDK,install
    from comsol_mcp import _windows_parameter_publish as paths,_host_scoped_provider as provider
    TI='S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464'
    def binary(text):
        p=list(map(int,text.split('-')[1:]));return bytes((p[0],len(p)-2))+p[1].to_bytes(6,'big')+b''.join(x.to_bytes(4,'little') for x in p[2:])
    class ActualSidSDK(SDK):
        def __init__(self):
            super().__init__();self.installer=C.create_string_buffer(binary(TI));self.keep.append(self.installer)
            self.a.EqualSid.callback=lambda p,q:int(C.string_at(p,self.length(p))==C.string_at(q,self.length(q)))
            self.a.GetLengthSid.callback=self.length
        def length(self,p):return 8+4*C.string_at(p,8)[1]
        def sid(self,text,out):
            b=C.create_string_buffer(binary(text));self.keep.append(b);C.cast(out,C.POINTER(C.c_void_p))[0]=C.cast(b,C.c_void_p);self.calls.append(('sid',text))
            if allocation_unknown and text==TI:raise self.primary
            return 1
        def security(self,*args):
            rc=super().security(*args);C.cast(args[3],C.POINTER(C.c_void_p))[0]=C.cast(self.installer,C.c_void_p);return rc
    sdk=ActualSidSDK();install(monkeypatch,sdk);api=paths._API();h=api.open('C:\\',directory=True,read_control=True)
    try:
        if allocation_unknown:
            import gc,weakref
            from comsol_mcp import _windows_host_preflight_owner as preflight
            sdk.local_result=123
            initial={id(x) for x in preflight.retained_original_owners()}
            with pytest.raises(ExecutionContractError) as caught:
                provider._verify_windows_owned_handle_security(api,h,directory=True,require_daemon_owner=False,pinned_ancestor_traversal=True)
            roots=[x for x in preflight.retained_original_owners() if id(x) not in initial]
            assert len(roots)==1 and roots[0].first_cause is sdk.primary
            root=roots[0];ref=weakref.ref(root.api);original_entries=list(root.entries())
            assert original_entries and any(getattr(e.handle,'value',e.handle)==C.addressof(sdk.keep[-1]) for e in original_entries)
            del caught,roots,root;gc.collect()
            assert ref() is not None and any(x.api is ref() for x in preflight.retained_original_owners())
            calls=list(sdk.calls);gc.collect();assert calls==sdk.calls
            return
        assert provider._verify_windows_owned_handle_security(api,h,directory=True,require_daemon_owner=False,pinned_ancestor_traversal=True)
        assert ('sid',TI) in sdk.calls
        with pytest.raises(ExecutionContractError):provider._verify_windows_owned_handle_security(api,h,directory=True,require_daemon_owner=False)
        # The fixed service SID was constructed only in the explicit role,
        # independently owned/freed, never appended to the private trusted set.
        assert sdk.calls.count(('sid',TI))==1
        freed=[x[1] for x in sdk.calls if isinstance(x,tuple) and x[0]=='free']
        installer_buffers=[b for b in sdk.keep if C.sizeof(b)==len(binary(TI))+1 and C.string_at(b,len(binary(TI)))==binary(TI)]
        assert len(installer_buffers)==2 and C.addressof(installer_buffers[-1]) in freed
        assert C.addressof(sdk.installer) not in freed
    finally:api.close(h)


@pytest.mark.parametrize('descriptor_change',[False,True])
def test_privatepath_both_pinned_ancestor_roles_and_same_raw_sd(monkeypatch,descriptor_change):
    import ctypes as C
    from pathlib import Path
    from tests.test_windows_host_preflight_owner import SDK,install
    from comsol_mcp import _host_scoped_provider as provider
    sdk=SDK();install(monkeypatch,sdk);original=provider._win32_descriptor_digest;roles=[]
    def record(*a,**kw):
        roles.append((kw.get('pinned_ancestor_traversal',False),kw['require_daemon_owner']))
        return original(*a,**kw)
    monkeypatch.setattr(provider,'_win32_descriptor_digest',record)
    if descriptor_change:
        native=sdk.a.GetSecurityInfo.callback;count=[0]
        def changed(*a):
            count[0]+=1
            if count[0]==5:C.memmove(sdk.descriptor,b'CHANGED-descriptor',18)
            return native(*a)
        sdk.a.GetSecurityInfo.callback=changed
        with pytest.raises(ExecutionContractError) as refused:
            provider._verify_windows_private_path(Path('C:/host/private/config'),directory=False,expected_identity=None)
        assert refused.value.code=='AUTHORIZATION_STATE_UNKNOWN'
    else:assert provider._verify_windows_private_path(Path('C:/host/private/config'),directory=False,expected_identity=None)
    assert roles==[(True,False)]*3+[(False,True)]+[(True,False)]*3+[(False,True)]
    closed=[x[1] for x in sdk.calls if isinstance(x,tuple) and x[0]=='close'];assert len(closed)==len(set(closed))==9
