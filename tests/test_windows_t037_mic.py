"""Pure bytes and synthetic SDK boundaries; not native MIC enforcement tests."""
import ast
import copy
import ctypes as C
import gc
import inspect
import struct
from types import SimpleNamespace as NS
import weakref

import pytest

from comsol_mcp import _windows_t037_mic as mic
from comsol_mcp import _windows_owned_resources as owned


def label_ace(rid=0x1000, mask=1, flags=0):
    return struct.pack('<BBHI', 0x11, flags, 20, mask) + b'\x01\x01\x00\x00\x00\x00\x00\x10' + struct.pack('<I', rid)


def descriptor(*aces, control=0x8010, offset=20, slack=0):
    if not offset:
        return struct.pack('<BBHIIII', 1, 0, control, 0, 0, 0, 0)
    acl = struct.pack('<BBHHH', 2, 0, 8 + sum(map(len, aces)) + slack, len(aces), 0) + b''.join(aces) + b'\0' * slack
    return struct.pack('<BBHIIII', 1, 0, control, 0, 0, offset, 0) + b'\0' * (offset - 20) + acl


@pytest.mark.parametrize('rid', [0, 0x1000, 0x2000, 0x3000, 0x4000, 0x5000])
@pytest.mark.parametrize('mask', [0, 1, 2, 4, 7])
def test_explicit_sdk_label_bytes_preserve_rid_mask_and_no_authority(rid, mask):
    raw = descriptor(label_ace(rid, mask, 0x13), slack=4)
    result = mic.parse_label_security_descriptor(raw)
    assert result.ace.sid == f'S-1-16-{rid}' and result.ace.rid == rid
    assert result.ace.mask == mask and result.ace.flags == 0x13 and result.ace.inherited
    assert result.raw == raw and result.documented_default is None and result.state == 'UNVERIFIED'
    assert result.ace.policy_flags == tuple(name for bit, name in ((1,'NO_WRITE_UP'),(2,'NO_READ_UP'),(4,'NO_EXECUTE_UP')) if mask & bit)


@pytest.mark.parametrize('raw,state', [(descriptor(control=0x8000,offset=0),'ABSENT'),
                                     (descriptor(offset=0),'NULL'), (descriptor(),'PRESENT')])
def test_absence_default_is_separate_from_an_observed_ace(raw,state):
    result = mic.parse_label_security_descriptor(raw)
    assert result.sacl_state == state and result.ace is None
    assert 'DOCUMENTED_WINDOWS_MEDIUM_DEFAULT' in result.documented_default
    assert 'NOT_AN_EXPLICIT_ACE_OR_ENFORCEMENT_TEST' in result.documented_default
    assert result.reasons == ('NO_LABEL_ACE_RETURNED',) and result.state == 'UNVERIFIED'


def test_unknown_rid_is_preserved_without_fabricated_interpretation():
    result = mic.parse_label_security_descriptor(descriptor(label_ace(0xFFFFFFFF)))
    assert result.ace.sid == 'S-1-16-4294967295' and result.documented_default is None
    assert result.reasons == ('RID_PRESERVED_WITHOUT_SUPPORTED_LEVEL_INTERPRETATION',)


@pytest.mark.parametrize('fault', ['short','too_large','mutable','revision','reserved','absolute','RM','control_reserved',
    'owner_offset','group_offset','dacl_offset','sacl_no_present','offset_unaligned','offset_header','offset_overflow',
    'missing_acl','nonzero_gap','trailing','acl_revision','acl_reserved','acl_reserved2','acl_size_small','acl_size_over',
    'acl_size_unaligned','ace_count_over','ace_count_truncated','ace_size_short','ace_size_over','ace_size_unaligned',
    'unknown_ace','inherit_only','audit_flag','critical_flag','NP_without_inherit','policy_unknown','sid_revision',
    'sid_count','sid_authority','sid_extra','duplicate','conflicting','unused_acl_nonzero'])
def test_closed_sd_acl_ace_sid_bounds_and_unknown_semantics_refuse(fault):
    data = bytearray(descriptor(label_ace()))
    if fault == 'short': data = data[:19]
    elif fault == 'too_large': data += b'\0' * mic.MAX_DESCRIPTOR
    elif fault == 'mutable':
        with pytest.raises(mic.MicReadError): mic.parse_label_security_descriptor(data)
        return
    elif fault == 'revision': data[0] = 2
    elif fault == 'reserved': data[1] = 1
    elif fault == 'absolute': struct.pack_into('<H',data,2,0x10)
    elif fault == 'RM': struct.pack_into('<H',data,2,0xC010)
    elif fault == 'control_reserved': struct.pack_into('<H',data,2,0x8050)
    elif fault in ('owner_offset','group_offset','dacl_offset'): struct.pack_into('<I',data,{'owner_offset':4,'group_offset':8,'dacl_offset':16}[fault],20)
    elif fault == 'sacl_no_present': struct.pack_into('<H',data,2,0x8000)
    elif fault in ('offset_unaligned','offset_header','offset_overflow'): struct.pack_into('<I',data,12,{'offset_unaligned':21,'offset_header':16,'offset_overflow':0xFFFFFFFF}[fault])
    elif fault == 'missing_acl': data = data[:22]
    elif fault == 'nonzero_gap': data = bytearray(descriptor(label_ace(),offset=24)); data[20]=1
    elif fault == 'trailing': data += b'\0'
    elif fault == 'acl_revision': data[20]=3
    elif fault == 'acl_reserved': data[21]=1
    elif fault == 'acl_reserved2': data[26]=1
    elif fault in ('acl_size_small','acl_size_over','acl_size_unaligned'): struct.pack_into('<H',data,22,{'acl_size_small':4,'acl_size_over':100,'acl_size_unaligned':29}[fault])
    elif fault in ('ace_count_over','ace_count_truncated'): struct.pack_into('<H',data,24,65 if fault=='ace_count_over' else 2)
    elif fault in ('ace_size_short','ace_size_over','ace_size_unaligned'): struct.pack_into('<H',data,30,{'ace_size_short':16,'ace_size_over':24,'ace_size_unaligned':19}[fault])
    elif fault == 'unknown_ace': data[28]=2
    elif fault in ('inherit_only','audit_flag','critical_flag','NP_without_inherit'): data[29]={'inherit_only':8,'audit_flag':64,'critical_flag':32,'NP_without_inherit':4}[fault]
    elif fault == 'policy_unknown': struct.pack_into('<I',data,32,8)
    elif fault == 'sid_revision': data[36]=2
    elif fault == 'sid_count': data[37]=2
    elif fault == 'sid_authority': data[43]=5
    elif fault == 'sid_extra':
        data += b'\0'*4;struct.pack_into('<H',data,22,32);struct.pack_into('<H',data,30,24)
    elif fault == 'duplicate': data = bytearray(descriptor(label_ace(),label_ace()))
    elif fault == 'conflicting': data = bytearray(descriptor(label_ace(),label_ace(0x3000)))
    else: data=bytearray(descriptor(label_ace(),slack=4));data[-1]=1
    with pytest.raises(mic.MicReadError): mic.parse_label_security_descriptor(bytes(data))


class Function:
    def __init__(self, function): self.function=function
    def __call__(self,*args): return self.function(*args)


class Sdk:
    """Allocates real ctypes bytes, while replacing only the OS SDK boundary."""
    def __init__(self, fault=None):
        self.fault=fault;self.queries=[];self.buffers={};self.freed=[];self.file_reads=0;self.close_file=[]
        self.fail_free=fault=='free_failure';self.slots=[];self.free_return=None
        self.free_fault=None;self.free_effects=[]
        self.a=NS(GetSecurityInfo=Function(self.security),GetSecurityDescriptorLength=Function(self.length),
                  GetSecurityDescriptorControl=Function(self.control),GetSecurityDescriptorSacl=Function(self.sacl))
        self.k=NS(LocalFree=Function(self.free),VirtualQuery=Function(self.region),
                  GetFileInformationByHandle=Function(self.file),GetFileType=Function(lambda handle:2 if fault=='file_type' else 1))
        self.original_free=self.k.LocalFree

    def security(self,handle,kind,flags,owner,group,dacl,sacl,output):
        assert handle==201 and kind==1 and flags==0x10 and owner is group is dacl is sacl is None
        assert mic._retained[self.api.registry] is self.api and self.api.pending is not None
        assert C.addressof(self.api.pending.storage)==C.addressof(C.cast(output,C.POINTER(C.c_void_p)).contents)
        self.queries.append((handle,kind,flags))
        slot=C.cast(output,C.POINTER(C.c_void_p));self.slots.append(slot)
        if self.fault=='status_no_allocation': return 5
        if self.fault=='null_success': return 0
        if self.fault=='pointer_invalid': slot[0]=1;return 0
        if self.fault=='pointer_invalid_max': slot[0]=C.c_void_p(-1).value;return 0
        raw=descriptor(label_ace())
        if self.fault=='absent': raw=descriptor(control=0x8000,offset=0)
        if self.fault=='malformed_raw': raw=descriptor(label_ace(flags=8))
        if self.fault=='label_drift' and len(self.queries)==2: raw=descriptor(label_ace(0x3000))
        buffer=C.create_string_buffer(raw);address=C.addressof(buffer);self.buffers[address]=(buffer,raw)
        slot[0]=address
        if self.fault=='throws_after_allocation': raise OSError('synthetic SDK exception after return slot filled')
        if self.fault=='status_with_allocation': return 5
        if self.fault=='status_bool': return True
        return 0

    def length(self,pointer): return len(self.buffers[pointer][1])+(4 if self.fault=='sdk_length' else 0)

    def control(self,pointer,control,revision):
        raw=self.buffers[pointer][1]
        C.cast(control,C.POINTER(C.c_uint16))[0]=struct.unpack_from('<H',raw,2)[0]+(1 if self.fault=='sdk_control' else 0)
        C.cast(revision,C.POINTER(C.c_uint32))[0]=2 if self.fault=='sdk_revision' else 1
        return 0 if self.fault=='sdk_control_failure' else 1

    def sacl(self,pointer,present,sacl,defaulted):
        raw=self.buffers[pointer][1];control=struct.unpack_from('<H',raw,2)[0];offset=struct.unpack_from('<I',raw,12)[0]
        C.cast(present,C.POINTER(C.c_int32))[0]=2 if self.fault=='sdk_present' else int(bool(control&0x10))
        C.cast(sacl,C.POINTER(C.c_void_p))[0]=pointer+offset+(4 if self.fault=='sdk_sacl_pointer' else 0) if offset else None
        C.cast(defaulted,C.POINTER(C.c_int32))[0]=1 if self.fault=='sdk_defaulted' else int(bool(control&0x20))
        if self.fault=='slot_drift': self.slots[-1][0]=pointer+4
        return 0 if self.fault=='sdk_sacl_failure' else 1

    def region(self,pointer,output,capacity):
        assert capacity==C.sizeof(mic._MemoryInfo)
        if self.fault=='region_fail': return 0
        found=next(((base,buffer) for base,(buffer,raw) in self.buffers.items() if base<=pointer<base+C.sizeof(buffer)),None)
        if found is None: return 0
        base,buffer=found;value=C.cast(output,C.POINTER(mic._MemoryInfo)).contents
        value.base=base;value.allocation_base=base;value.size=C.sizeof(buffer)
        value.state=0x2000 if self.fault=='region_reserved' else 0x1000
        value.protect=0x104 if self.fault=='region_guard' else 4
        if self.fault=='region_truncated':value.size=19
        return C.sizeof(value)

    def free(self,pointer):
        assert type(pointer) is int and pointer in self.buffers
        allocation,=[e for e in self.api.registry.unresolved() if e.adapter is self.api and e.domain=='mic_sd']
        assert allocation.value==allocation.handle==pointer and allocation.registry.owner is self.api.registry.owner
        self.freed.append(pointer)
        if self.free_fault=='effect_then_raise':
            self.free_effects.append(pointer)
            raise OSError('SDK effect completed but Python return was lost')
        if self.free_fault=='before_call_raise':raise OSError('no reliable SDK return')
        if self.free_fault=='different_handle':return pointer+8
        return pointer if self.fail_free else self.free_return

    def file(self,handle,output):
        assert handle==201;self.file_reads+=1
        value=C.cast(output,C.POINTER(mic._FileInfo)).contents
        value.attributes=0x400 if self.fault=='file_reparse' else 0x20
        value.creation=100;value.access=200;value.write=300;value.volume=55;value.size_low=12;value.links=1;value.id_low=77
        if self.fault=='file_id_missing':value.id_low=0
        if self.file_reads==2:
            for fault,name in [('identity_drift','id_low'),('extent_drift','size_low'),('write_drift','write'),
                               ('volume_drift','volume'),('creation_drift','creation'),('links_drift','links'),('last_access_only','access')]:
                if self.fault==fault:setattr(value,name,getattr(value,name)+1)
        return 0 if self.fault=='file_api_failure' else 1


def setup(monkeypatch,fault=None):
    class Owner:
        pass
    sdk=Sdk(fault);owner=Owner();adapter=NS();registry=owned.registry_for(owner)
    entry=registry.acquire(adapter,201,lambda value:sdk.close_file.append(value) or 1,mode='BOOL')
    binding=mic.BorrowedFileHandle(owner,adapter,entry)
    monkeypatch.setattr(mic,'os',NS(name='nt'))
    monkeypatch.setattr(mic,'_retained',{})
    monkeypatch.setattr(C,'WinDLL',lambda name,**kwargs:sdk.a if name=='advapi32' else sdk.k,raising=False)
    api=mic.NativeFileMicApi(owner)
    sdk.api=api
    return sdk,binding,api


@pytest.mark.parametrize('fault',[None,'absent','last_access_only'])
def test_readonly_actual_adapter_logic_borrows_never_releases_file_and_no_issuer(monkeypatch,fault):
    sdk,binding,api=setup(monkeypatch,fault)
    result=mic.observe_file_mandatory_label(binding,api=api)
    assert result.state=='UNVERIFIED' and result.mic_enforcement=='NOT_RUN' and result.issuer=='UNCHANGED'
    assert result.before_file==result.after_file and result.before_label==result.after_label
    assert binding.entry.state=='ACQUIRED' and not sdk.close_file and len(sdk.queries)==len(sdk.freed)==2
    assert api.registry.unresolved()==(binding.entry,) and not mic._retained
    if fault=='absent': assert result.before_label.ace is None and result.before_label.documented_default
    with pytest.raises(TypeError): mic.FileMicObservation(result.before_file,result.after_file,result.before_label,result.after_label,state='VERIFIED')


@pytest.mark.parametrize('fault',['sdk_length','sdk_control','sdk_revision','sdk_present','sdk_sacl_pointer','sdk_defaulted',
    'sdk_control_failure','sdk_sacl_failure','slot_drift','malformed_raw','status_no_allocation','status_with_allocation',
    'status_bool','null_success','throws_after_allocation','label_drift','identity_drift','extent_drift','write_drift',
    'volume_drift','creation_drift','links_drift','file_type','file_reparse','file_id_missing','file_api_failure'])
def test_sdk_errors_raw_mismatch_and_file_security_drift_never_emit_verified_fact(monkeypatch,fault):
    sdk,binding,api=setup(monkeypatch,fault)
    with pytest.raises((mic.MicReadError,OSError)):mic.observe_file_mandatory_label(binding,api=api)
    assert binding.entry.state=='ACQUIRED' and not sdk.close_file
    assert len(sdk.freed)==len(sdk.buffers) and api.registry.unresolved()==(binding.entry,)
    assert not mic._retained
    if fault in ('status_no_allocation','status_with_allocation','throws_after_allocation'):assert len(sdk.queries)==1


@pytest.mark.parametrize('fault',['pointer_invalid','pointer_invalid_max','region_fail','region_reserved','region_guard','region_truncated'])
def test_bad_pointer_is_not_dereferenced_or_released_and_retains_original_owner(monkeypatch,fault):
    sdk,binding,api=setup(monkeypatch,fault)
    with pytest.raises(owned.NativeResourceError):mic.observe_file_mandatory_label(binding,api=api)
    allocations=[e for e in api.registry.unresolved() if e.domain=='mic_sd']
    assert len(allocations)==1 and allocations[0].state=='UNKNOWN'
    assert mic._retained[api.registry] is api and not sdk.freed and not sdk.close_file
    with pytest.raises(owned.NativeResourceError):mic.observe_file_mandatory_label(binding,api=api)
    with pytest.raises(owned.NativeResourceError):api.recover_owned(binding.owner)
    assert len(sdk.queries)==1 and not sdk.freed and binding.entry.state=='ACQUIRED'


def test_localfree_same_hlocal_failure_retains_owner_and_allows_only_fixed_closer_retry(monkeypatch):
    sdk,binding,api=setup(monkeypatch,'free_failure')
    with pytest.raises(owned.NativeResourceError):mic.observe_file_mandatory_label(binding,api=api)
    allocation,=[e for e in api.registry.unresolved() if e.domain=='mic_sd'];holder=allocation.keepalive[1]
    pointer=allocation.value
    assert allocation.handle==holder.value==holder.storage.value==pointer and holder.state=='UNKNOWN'
    assert allocation.state=='UNKNOWN' and holder.closer is sdk.original_free
    api_ref=weakref.ref(api);holder_ref=weakref.ref(holder);storage_ref=weakref.ref(holder.storage)
    owner_ref=weakref.ref(binding.owner);file_entry_ref=weakref.ref(binding.entry)
    del binding,api,holder;gc.collect();api=api_ref()
    assert owner_ref() is api.registry.owner and file_entry_ref() is not None
    binding=mic.BorrowedFileHandle(owner_ref(),file_entry_ref().adapter,file_entry_ref())
    assert api is mic._retained[binding.entry.registry] and holder_ref() is not None and storage_ref() is not None
    with pytest.raises(owned.NativeResourceError):mic.observe_file_mandatory_label(binding)
    assert sdk.freed==[pointer] and len(sdk.queries)==1 and binding.entry.state=='ACQUIRED'
    with pytest.raises(owned.NativeResourceError):api.recover_owned(object())
    with pytest.raises(owned.NativeResourceError):api.registry.retry_owned(dict(allocation.__dict__),binding.owner)
    assert sdk.freed==[pointer]
    api.k.LocalFree=Function(lambda value:pytest.fail('substituted SDK closer called'))
    with pytest.raises(owned.NativeResourceError):api.recover_owned(binding.owner)
    assert sdk.freed==[pointer,pointer] and allocation.state=='UNKNOWN'
    sdk.fail_free=False;api.recover_owned(binding.owner)
    assert sdk.freed==[pointer]*3 and allocation.state=='RELEASED' and not mic._retained
    assert binding.entry.state=='ACQUIRED' and not sdk.close_file
    with pytest.raises(owned.NativeResourceError):api.recover_owned(binding.owner)


@pytest.mark.parametrize('fault',['effect_then_raise','before_call_raise',False,True,'released',1.0,'different_handle'])
def test_localfree_indeterminate_result_never_retries_even_explicitly_or_after_closer_replacement(monkeypatch,fault):
    sdk,binding,api=setup(monkeypatch)
    if fault in ('effect_then_raise','before_call_raise','different_handle'):sdk.free_fault=fault
    else:sdk.free_return=fault
    with pytest.raises(owned.NativeResourceError):mic.observe_file_mandatory_label(binding,api=api)
    allocation,=[e for e in api.registry.unresolved() if e.domain=='mic_sd'];holder=allocation.keepalive[1]
    pointer=allocation.value
    refs=[weakref.ref(item) for item in (binding.owner,api,holder,holder.storage)]
    assert allocation.handle==holder.value==holder.storage.value==pointer
    assert holder.state==allocation.state=='UNKNOWN' and holder.closer is sdk.original_free
    assert sdk.free_effects==([pointer] if fault=='effect_then_raise' else [])
    sdk.free_fault=None;sdk.free_return=None
    api.k.LocalFree=Function(lambda value:pytest.fail('substituted API closer used'))
    with pytest.raises(owned.NativeResourceError):api.recover_owned(binding.owner)
    with pytest.raises(owned.NativeResourceError):api.registry.retry_owned(allocation,binding.owner)
    with pytest.raises(owned.NativeResourceError):holder.release(pointer)
    with pytest.raises(owned.NativeResourceError):mic.observe_file_mandatory_label(binding,api=api)
    with pytest.raises(owned.NativeResourceError):mic.observe_file_mandatory_label(binding)
    gc.collect()
    assert all(ref() is not None for ref in refs) and mic._retained[api.registry] is api
    assert holder.state==allocation.state=='UNKNOWN' and allocation.keepalive[1] is holder
    assert sdk.freed==[pointer] and len(sdk.queries)==1
    assert binding.entry.state=='ACQUIRED' and not sdk.close_file


@pytest.mark.parametrize('fault',['effect_then_raise','before_call_raise','different_handle'])
def test_localfree_known_failure_permission_is_consumed_before_uncertain_retry(monkeypatch,fault):
    sdk,binding,api=setup(monkeypatch,'free_failure')
    with pytest.raises(owned.NativeResourceError):mic.observe_file_mandatory_label(binding,api=api)
    allocation,=[e for e in api.registry.unresolved() if e.domain=='mic_sd'];pointer=allocation.value
    sdk.fail_free=False;sdk.free_fault=fault
    with pytest.raises((owned.NativeResourceError,OSError)):api.recover_owned(binding.owner)
    sdk.free_fault=None
    with pytest.raises(owned.NativeResourceError):api.recover_owned(binding.owner)
    with pytest.raises(owned.NativeResourceError):api.registry.retry_owned(allocation,binding.owner)
    assert sdk.freed==[pointer,pointer] and len(sdk.queries)==1
    assert allocation.state=='UNKNOWN' and mic._retained[api.registry] is api
    assert binding.entry.state=='ACQUIRED' and not sdk.close_file


@pytest.mark.parametrize('fault',['slot','value','entry','closer'])
def test_localfree_known_failure_cannot_recover_changed_original_binding(monkeypatch,fault):
    sdk,binding,api=setup(monkeypatch,'free_failure')
    with pytest.raises(owned.NativeResourceError):mic.observe_file_mandatory_label(binding,api=api)
    entry,=[e for e in api.registry.unresolved() if e.domain=='mic_sd'];holder=entry.keepalive[1];pointer=entry.value
    if fault=='slot':holder.storage.value=pointer+8
    elif fault=='value':holder.value+=8
    elif fault=='entry':entry.handle+=8
    else:holder.closer=Function(lambda value:pytest.fail('replacement holder closer used'))
    sdk.fail_free=False
    with pytest.raises(owned.NativeResourceError):api.recover_owned(binding.owner)
    with pytest.raises(owned.NativeResourceError):api.registry.retry_owned(entry,binding.owner)
    assert sdk.freed==[pointer] and len(sdk.queries)==1 and entry.state=='UNKNOWN'
    assert binding.entry.state=='ACQUIRED' and not sdk.close_file


@pytest.mark.parametrize('fault',['dict','owner','proxy_owner','adapter','entry_copy','handle_changed','released','UNKNOWN','thread','domain','different_api'])
def test_borrowed_original_entry_identity_and_wrong_owner_refuse_before_security_read(monkeypatch,fault):
    sdk,binding,api=setup(monkeypatch)
    selected=binding
    if fault=='dict':selected=dict(owner=binding.owner,adapter=binding.adapter,entry=binding.entry)
    elif fault=='owner':selected=mic.BorrowedFileHandle(NS(),binding.adapter,binding.entry)
    elif fault=='proxy_owner':
        proxy=NS();owned.attach_registry(proxy,api.registry);selected=mic.BorrowedFileHandle(proxy,binding.adapter,binding.entry)
    elif fault=='adapter':selected=mic.BorrowedFileHandle(binding.owner,NS(),binding.entry)
    elif fault=='entry_copy':selected=mic.BorrowedFileHandle(binding.owner,binding.adapter,copy.copy(binding.entry))
    elif fault=='handle_changed':binding.entry.handle=202
    elif fault=='released':binding.entry.release_owned(binding.owner)
    elif fault=='UNKNOWN':binding.entry.state='UNKNOWN';binding.entry.error=RuntimeError('unresolved file')
    elif fault=='thread':binding.entry.thread_id+=1
    elif fault=='domain':binding.entry.domain='mic_sd'
    elif fault=='different_api':api.registry=owned.registry_for(NS())
    closes=list(sdk.close_file)
    with pytest.raises((mic.MicReadError,owned.NativeResourceError)):mic.observe_file_mandatory_label(selected,api=api)
    assert not sdk.queries and sdk.close_file==closes


def test_readonly_export_list_has_no_opens_setters_privilege_or_issuer_path():
    tree=ast.parse(inspect.getsource(mic))
    imports={a.name for node in ast.walk(tree) if isinstance(node,ast.Import) for a in node.names}
    assert not {'subprocess','winreg','socket'} & imports
    text=inspect.getsource(mic)
    for forbidden in ['SetSecurityInfo','SetNamedSecurityInfo','AdjustTokenPrivileges','OpenProcess','CreateFileW','CreateProcess','ResumeThread']:
        assert forbidden not in text
    assert mic.LABEL_SECURITY_INFORMATION==0x10 and C.sizeof(mic._FileInfo)==52 and C.sizeof(mic._MemoryInfo)==48
    from comsol_mcp._windows_t037_acl import ChildDaclObservation
    assert ChildDaclObservation.__dataclass_fields__['mandatory_integrity_state'].default=='NOT_VERIFIED'
