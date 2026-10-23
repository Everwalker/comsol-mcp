"""SOFTWARE_ONLY SDK ABI/effect controls. No Windows or host mutation."""
import ctypes as C
import copy
import gc
import struct
from types import MethodType

import pytest

from comsol_mcp import _windows_host_bootstrap as b
from comsol_mcp import _windows_scm_config_native as n
from comsol_mcp import _windows_scm_config_materialization as m
from tests.test_windows_scm_config_materialization import fixture, installed, no_native
from tests.test_windows_host_bootstrap import raw_sd


@pytest.mark.parametrize('rid,mask', [(0x2000,1),(0x3000,3),(0x4000,7)])
def test_literal_sacl_layout_uses_only_label_ace_and_documented_byte_order(rid,mask):
    raw=n.label_bytes(dict(rid=rid,mask=mask,flags=0))
    assert struct.unpack_from('<BBHIIII',raw)==(1,0,0x8010,0,0,20,0)
    assert struct.unpack_from('<BBHHH',raw,20)==(2,0,28,1,0)
    assert struct.unpack_from('<BBHI',raw,28)==(0x11,0,20,mask)
    assert raw[36:44]==bytes((1,1,0,0,0,0,0,16))
    assert int.from_bytes(raw[44:48],'little')==rid
    assert n.explicit_label(raw).ace.rid==rid
    with pytest.raises(b.BootstrapError): b.decode_sd(raw)  # existing DACL parser is NOT weakened


@pytest.mark.parametrize('change', [{'rid':True},{'rid':0x1000},{'rid':0x2001},{'mask':0},{'mask':2},
    {'mask':8},{'mask':True},{'flags':True},{'flags':1},{'flags':8},{'extra':True}])
def test_no_implicit_or_inherited_label_policy(change):
    policy=dict(rid=0x2000,mask=1,flags=0);policy.update(change)
    with pytest.raises(b.BootstrapError): n.label_bytes(policy)


@pytest.mark.parametrize('damage', ['absent','inherit_only','type','authority','ace_extent','acl_extent','offset','duplicate'])
def test_bounded_label_readback_refuses_bad_or_nonexplicit_layout(damage):
    raw=bytearray(n.label_bytes(dict(rid=0x2000,mask=1,flags=0)))
    if damage=='absent':raw=bytearray(struct.pack('<BBHIIII',1,0,0x8000,0,0,0,0))
    elif damage=='inherit_only':raw[29]=8
    elif damage=='type':raw[28]=0
    elif damage=='authority':raw[43]=5
    elif damage=='ace_extent':struct.pack_into('<H',raw,30,24)
    elif damage=='acl_extent':struct.pack_into('<H',raw,22,32)
    elif damage=='offset':struct.pack_into('<I',raw,12,2**32-1)
    elif damage=='duplicate':
        raw+=raw[28:48];struct.pack_into('<H',raw,22,48);struct.pack_into('<H',raw,24,2)
    with pytest.raises(Exception):n.explicit_label(bytes(raw))


def test_original_setter_keeps_whole_buffer_original_arguments_and_output_after_gc():
    original,backend,_=installed()
    record=original._configuration.native.label_calls[-1]
    gc.collect()
    args=record.call.args
    assert record.call.state=='SUCCEEDED' and record.call.scalar==0
    assert args[:6]==(record.argument,1,0x10,None,None,None)
    assert args[6].value==C.addressof(record.storage)+20
    assert bytes(record.storage.raw)==record.raw
    assert record.handle.owner is record.owner is original.owner
    assert record.original_root is original.nodes['']
    assert record.before==original.nodes[m.CONFIG_PATHS[-1]].image
    assert record.call.fn is original._configuration.native.setter
    assert all(c.name not in ('AdjustTokenPrivileges','SetNamedSecurityInfoW','StartServiceW') for c in original.owner.calls)
    original.restore()


@pytest.mark.parametrize('outcome',[5,True,None,'0',0.0])
def test_dword_failure_or_malformed_setter_never_replays_or_becomes_ack(outcome):
    original,backend,_=fixture()
    sdk=backend.sdk['SetSecurityInfo'];calls=[]
    def result(*args):
        calls.append(args);sdk(*args);return outcome
    backend.sdk['SetSecurityInfo']=result
    # Original binding is frozen at prepare; replace it before a NEW prepare.
    original.abandon_readonly()
    fresh,backend,_=fixture(prepared=False)
    sdk=backend.sdk['SetSecurityInfo']
    def result(*args):calls.append(args);sdk(*args);return outcome
    backend.sdk['SetSecurityInfo']=result
    fresh.prepare()
    with pytest.raises(b.BootstrapError):fresh.apply(approved_plan_sha256=fresh.intent.sha256)
    assert fresh.state=='UNKNOWN'
    count=len(calls)
    for f in (fresh.restore, fresh.acknowledge_configuration):
        with pytest.raises(b.BootstrapError):f()
    assert len(calls)==count==1
    assert fresh in b.retained_bootstrap_owners()


@pytest.mark.parametrize('stage',['LABEL_READ','CONFIG_WRITE','CONFIG_FLUSH'])
@pytest.mark.parametrize('fault',['raise','invalid'])
def test_partial_or_opaque_outputs_keep_original_nodes_slots_and_no_retry(stage,fault):
    original,backend,_=fixture()
    backend.faults[stage]=fault
    with pytest.raises(b.BootstrapError):original.apply(approved_plan_sha256=original.intent.sha256)
    assert original.state=='UNKNOWN'
    calls=list(backend.calls);handles=tuple(original.owner.handles)
    for f in (original.handoff_runtime,original.restore,original.acknowledge_configuration):
        with pytest.raises(b.BootstrapError):f()
    assert backend.calls==calls and tuple(original.owner.handles)==handles
    assert any(c.state=='UNKNOWN' for c in original.owner.calls)


@pytest.mark.parametrize('drift',['handle','argument','closer','call','owner','recipe','resolved'])
def test_ack_never_upgrades_original_binding_drift(drift,monkeypatch):
    original,backend,_=fixture()
    def lost(*args):raise RuntimeError('lost ack')
    monkeypatch.setattr(original.owner,'checkpoint',lost)
    with pytest.raises(b.BootstrapError):original.apply(approved_plan_sha256=original.intent.sha256)
    ack=original._configuration_ack
    if drift=='handle':ack.handle.original_value+=1
    elif drift=='argument':ack.handle.argument=b.HANDLE(ack.handle.original_value)
    elif drift=='closer':backend.sdk['SetSecurityInfo']=lambda *a:0
    elif drift=='call':ack.label_call.call.scalar=5
    elif drift=='owner':original.owner=b._Owner(backend)
    elif drift=='recipe':original._configuration.recipe['labels']['file']['rid']=0x3000
    elif drift=='resolved':original._configuration.resolved=m.ResolvedConfiguration('0'*64,'S-1-5-80-0-0-0-0-0',(0,0),())
    before=list(backend.calls)
    with pytest.raises(b.BootstrapError):original.acknowledge_configuration()
    assert backend.calls==before and original.state=='CONFIGURATION_ACK_PENDING'


def test_label_only_effect_cannot_hide_other_security_or_content_change():
    original,backend,_=fixture(prepared=False)
    setter=backend.sdk['SetSecurityInfo']
    def bad(*args):
        result=setter(*args)
        backend.tree[backend.target(args[0])]['sd']=raw_sd(True,broad=True)
        return result
    backend.sdk['SetSecurityInfo']=bad
    original.prepare()
    with pytest.raises(b.BootstrapError) as exc:original.apply(approved_plan_sha256=original.intent.sha256)
    assert exc.value.code=='CONFIG_SET_DRIFT' and original.state=='UNKNOWN'


# Main counter copied unchanged; imports/autouse SOFTWARE_ONLY seam are above.
def test_replaced_cached_setter_is_rejected_before_any_install_effect():
    original, backend, _ = fixture()
    companion = original._configuration.native
    original_setter = companion.setter
    calls = []
    before_tree = dict(backend.tree)

    def substituted_setter(*args):
        calls.append(args)
        return original_setter(*args)

    companion.setter = substituted_setter
    with pytest.raises(b.BootstrapError) as error:
        original.apply(approved_plan_sha256=original.intent.sha256)
    assert error.value.code == 'CONFIG_NATIVE_ORIGIN'
    assert calls == []
    assert backend.tree == before_tree


def test_replaced_cached_getter_refuses_apply_before_any_create_or_dispatch():
    original,backend,_=fixture();companion=original._configuration.native
    fn=companion.getter;spy=[]
    def replacement(*args):spy.append(args);return fn(*args)
    companion.getter=replacement
    before_tree=copy.deepcopy(backend.tree);before_calls=list(backend.calls)
    with pytest.raises(b.BootstrapError) as error:original.apply(approved_plan_sha256=original.intent.sha256)
    assert error.value.code=='CONFIG_NATIVE_ORIGIN'
    assert not spy and backend.tree==before_tree and backend.calls==before_calls
    assert not original.nodes and original.service is None


@pytest.mark.parametrize('alias',['setter','getter'])
@pytest.mark.parametrize('operation',['read_label','set_label','set_descriptor'])
def test_each_real_companion_path_rejects_alias_wrapper_before_any_query_or_effect(alias,operation):
    original,backend,_=installed();companion=original._configuration.native
    fn=getattr(companion,alias);spy=[]
    def replacement(*args):spy.append(args);return fn(*args)
    setattr(companion,alias,replacement)
    node=original.nodes[m.CONFIG_PATHS[0]]
    before_tree=copy.deepcopy(backend.tree);before_calls=list(backend.calls);before_handles=tuple(original.owner.handles)
    with pytest.raises(b.BootstrapError) as error:
        if operation=='read_label':companion.read_label(node.handle)
        elif operation=='set_label':companion.set_label(node,node.label)
        else:companion.set_descriptor(node,node.image.raw_sd)
    assert error.value.code=='CONFIG_NATIVE_ORIGIN'
    assert not spy and backend.calls==before_calls and backend.tree==before_tree
    assert tuple(original.owner.handles)==before_handles and not node.handle.closed


@pytest.mark.parametrize('alias',['setter','getter'])
def test_equal_normalized_bound_method_does_not_replace_original_cached_object(alias):
    original,backend,_=fixture();companion=original._configuration.native
    fn=getattr(companion,alias);other=MethodType(fn.__func__,fn.__self__)
    assert other is not fn and b._binding(other)==b._binding(fn)
    setattr(companion,alias,other)
    before=list(backend.calls)
    with pytest.raises(b.BootstrapError) as error:original.apply(approved_plan_sha256=original.intent.sha256)
    assert error.value.code=='CONFIG_NATIVE_ORIGIN' and backend.calls==before and not original.nodes


def _original_typed_software_functions():
    """Only callable metadata emulates ctypes; callbacks remain SOFTWARE_ONLY."""
    from tests.test_windows_host_preflight_owner import Function
    original,backend,_=fixture(prepared=False)
    P,D=b.HANDLE,b.DWORD
    for name,result,args in (('SetSecurityInfo',D,[P,D,D,P,P,P,P]),
                            ('GetKernelObjectSecurity',b.BOOL,[P,D,P,D,C.POINTER(D)])):
        fn=Function(backend.sdk[name]);fn.restype=result;fn.argtypes=list(args)
        backend.sdk[name]=fn
    original.prepare()
    return original,backend


@pytest.mark.parametrize('alias',['setter','getter'])
@pytest.mark.parametrize('drift',['restype','argtypes_inplace','argtypes_none','argtypes_invalid','removed_restype'])
def test_same_original_sdk_function_with_signature_drift_refuses_before_install(alias,drift):
    original,backend=_original_typed_software_functions();companion=original._configuration.native
    fn=getattr(companion,alias)
    assert fn is backend.function('SetSecurityInfo' if alias=='setter' else 'GetKernelObjectSecurity')
    if drift=='restype':fn.restype=b.HANDLE
    elif drift=='argtypes_inplace':fn.argtypes[0]=b.DWORD
    elif drift=='argtypes_none':fn.argtypes=None
    elif drift=='argtypes_invalid':fn.argtypes=True
    else:del fn.restype
    before=copy.deepcopy(backend.tree);calls=list(backend.calls)
    with pytest.raises(b.BootstrapError) as error:original.apply(approved_plan_sha256=original.intent.sha256)
    assert error.value.code=='CONFIG_NATIVE_ORIGIN'
    assert not original.nodes and backend.tree==before and backend.calls==calls


def test_read_label_dispatches_the_same_original_cached_getter_and_sdk_function():
    original,backend=_original_typed_software_functions()
    original.apply(approved_plan_sha256=original.intent.sha256)
    companion=original._configuration.native;node=original.nodes[m.CONFIG_PATHS[0]]
    before=len(original.owner.calls)
    assert companion.read_label(node.handle)==node.label
    calls=original.owner.calls[before:]
    assert len(calls)==1 and calls[0].name=='GetKernelObjectSecurity'
    assert calls[0].fn is companion.getter is backend.function('GetKernelObjectSecurity')
    assert calls[0].state=='SUCCEEDED' and calls[0].scalar==1
    original.restore()


@pytest.mark.parametrize('operation',['set_label','set_descriptor'])
def test_alias_drift_during_original_read_is_rejected_before_setter_dispatch(operation):
    original,backend,_=fixture(prepared=False);state={'armed':False};spy=[]
    getter=backend.sdk['GetKernelObjectSecurity'];setter=backend.sdk['SetSecurityInfo']
    def replacement(*args):spy.append(args);return setter(*args)
    def read_then_drift(*args):
        result=getter(*args)
        if state['armed']:original._configuration.native.setter=replacement
        return result
    backend.sdk['GetKernelObjectSecurity']=read_then_drift
    original.prepare();original.apply(approved_plan_sha256=original.intent.sha256)
    companion=original._configuration.native;node=original.nodes[m.CONFIG_PATHS[0]]
    before_tree=copy.deepcopy(backend.tree);before_calls=len(backend.calls);before_records=len(companion.label_calls)
    state['armed']=True
    with pytest.raises(b.BootstrapError) as error:
        if operation=='set_label':companion.set_label(node,node.label)
        else:companion.set_descriptor(node,node.image.raw_sd)
    assert error.value.code=='CONFIG_NATIVE_ORIGIN' and not spy
    assert not any(c[0] in ('DACL_SET','LABEL_SET','CREATE_NEW') for c in backend.calls[before_calls:])
    assert len(companion.label_calls)==before_records and backend.tree==before_tree
