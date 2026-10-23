"""Original submitted-primary ownership and Main's finite B token policy.

No C/Resume authority. All snapshots are actual borrowed original TOKEN reads.
"""
from __future__ import annotations
import ctypes as C
import threading
from types import MappingProxyType
from . import _windows_t037_child_evidence as e
from ._execution_contract import ExecutionContractError
from ._windows_owned_resources import registry_for, attach_registry, handle_closer, _OwnedResource

_SEAL = object()
GROUP_MASK = 0xE000007F
PRIV_MASK = 0x80000003
ALLOWED_PRIVILEGE = 'SeChangeNotifyPrivilege'
LOW_SID = 'S-1-16-4096'

class LUID(C.Structure):
    _fields_ = [('LowPart', e.U32), ('HighPart', e.I32)]

class LUID_AND_ATTRIBUTES(C.Structure):
    _fields_ = [('Luid', LUID), ('Attributes', e.U32)]

class SID_AND_ATTRIBUTES(C.Structure):
    _fields_ = [('Sid', e.PTR), ('Attributes', e.U32)]

def require_abi():
    if (C.sizeof(e.U32), C.sizeof(e.I32), C.sizeof(e.PTR), C.sizeof(LUID),
        C.sizeof(LUID_AND_ATTRIBUTES), C.alignment(LUID_AND_ATTRIBUTES),
        LUID_AND_ATTRIBUTES.Attributes.offset, C.sizeof(SID_AND_ATTRIBUTES),
        SID_AND_ATTRIBUTES.Attributes.offset) != (4,4,8,8,12,4,8,16,8):
        fail('B_PRIMARY_ABI_UNVERIFIED', 'fixed x64 token ABI is unavailable')

def fail(code, message):
    raise ExecutionContractError(code, message, safe_retry=False, stage='pre_dispatch')

def policy(message):
    fail('B_TOKEN_POLICY_UNVERIFIED', message)

def sid(value):
    if type(value) is not str: policy('SID is unavailable')
    parts = value.split('-')
    if len(parts) < 4 or parts[:2] != ['S','1'] or len(parts) > 18:
        policy('SID is not canonical')
    nums = []
    for i, part in enumerate(parts[2:]):
        if not part.isascii() or not part.isdigit() or str(int(part)) != part:
            policy('SID is not canonical')
        n=int(part)
        if n >= (1 << (48 if i == 0 else 32)): policy('SID exceeds its ABI field')
        nums.append(n)
    return value

def row_map(rows, *, group=False):
    if type(rows) is not tuple: policy('complete SID rows are unavailable')
    out={}
    for row in rows:
        if type(row) is not e.SidAttributes: policy('SID row is not a typed observation')
        key=sid(row.sid);a=row.attributes
        if key in out or type(a) is not int or not 0 <= a <= 0xFFFFFFFF:
            policy('duplicate/invalid SID attributes')
        if group and (a & ~GROUP_MASK or (a & 0x10 and a & 6)):
            policy('unsupported group attributes or enabled deny-only row')
        out[key]=a
    return out

def privilege_map(token, *, budget=False):
    if type(token.privileges) is not tuple: policy('privileges are unavailable')
    out={};names=set()
    for row in token.privileges:
        if (type(row) is not e.Privilege or type(row.luid) is not int or not 0 < row.luid < 1<<64
                or type(row.name) is not str or not row.name or row.name in names or row.luid in out
                or type(row.attributes) is not int or row.attributes & ~PRIV_MASK or row.attributes < 0):
            policy('unresolved/duplicate privilege or unsupported raw attributes')
        if budget and row.name != ALLOWED_PRIVILEGE:
            policy('a present privilege exceeds the selected budget, even if disabled')
        names.add(row.name);out[row.luid]=(row.name,row.attributes)
    return out

def semantic(token):
    if type(token) is not e.TokenObservation: policy('actual full token observation is required')
    if any(type(v) is not int or v <= 0 for v in (token.token_id,token.authentication_id,token.modified_id)):
        policy('dynamic token identity is unavailable')
    groups=row_map(token.groups,group=True)
    restricting=row_map(token.restricted_sids)
    caps=row_map(token.capabilities)
    user=row_map((token.user,));integrity=row_map((token.integrity,),group=True)
    if (sid(token.integrity.sid) != 'S-1-16-'+str(token.integrity_rid)
            or type(token.integrity_rid) is not int or not token.integrity.attributes & 0x20
            or type(token.mandatory_policy) is not int or type(token.is_appcontainer) is not bool):
        policy('invalid integrity/mandatory/AppContainer observation')
    il_rows={k:v for k,v in groups.items() if k.startswith('S-1-16-') or v & 0x60}
    if il_rows != integrity: policy('missing/ambiguous integrity group or mismatched IL attributes')
    return (user,token.authentication_id,groups,restricting,caps,integrity,
            token.integrity_rid,token.mandatory_policy,token.is_appcontainer)

def verify_returned(source, returned, expected_privileges):
    if semantic(source) != semantic(returned): policy('returned primary changed a security-semantic non-privilege field')
    if privilege_map(returned,budget=True) != expected_privileges:
        policy('returned primary does not exactly implement the privilege deletion')
    # TokenId/ModifiedId belong to distinct actual kernel instances, not a cross-token constant.
    return returned

def child_policy(source, child, profile):
    semantic(source);semantic(child)
    if child.user != source.user or child.user.sid != profile.service_account_sid or child.authentication_id != source.authentication_id:
        policy('child user/authentication context differs from the actual submitted primary')
    if not child.is_appcontainer: policy('child is not AppContainer')
    if child.integrity.sid != LOW_SID or child.integrity_rid != 4096 or child.integrity.attributes != source.integrity.attributes:
        policy('child is not the exact Low integrity transition')
    if child.mandatory_policy not in (1,3): policy('child requires NO_WRITE_UP without unsupported policy bits')
    before=row_map(source.groups,group=True);after=row_map(child.groups,group=True)
    before.pop(source.integrity.sid);after.pop(child.integrity.sid)
    for key,a in before.items():
        b=after.pop(key,None)
        if b is None or not (b==a or (a & 6 and not a & 0x10 and b==((a & ~6)|0x10))):
            policy('ordinary SID removed/promoted or changed outside the documented deny-only conversion')
    optional={'S-1-15-2-1',sid(profile.appcontainer_sid)}
    if any(k not in optional or a!=7 for k,a in after.items()):
        policy('unapproved extra AppContainer group or raw attributes')
    if row_map(child.restricted_sids) != row_map(source.restricted_sids):
        policy('actual restricting SID/raw-attribute map changed')
    expected={sid(s):4 for s in profile.capability_sids}
    if len(expected)!=len(profile.capability_sids) or row_map(child.capabilities)!=expected:
        policy('capability SID/raw attributes differ from actual approved SECURITY_CAPABILITIES')
    allowed=privilege_map(source,budget=True);actual=privilege_map(child,budget=True)
    for luid,(name,a) in actual.items():
        if luid not in allowed or allowed[luid][0]!=name or a & 3 & ~(allowed[luid][1] & 3):
            policy('child privilege name/LUID or enabled/default bits exceed actual submitted privilege')
    return True

def _function(fn):
    return (getattr(fn,'__self__',None),getattr(fn,'__func__',fn))

class _TokenCall:
    _FIXED = frozenset({'owner','name','function','arguments','output','output_address','closer','close_mode','keepalive'})
    def __setattr__(self,name,value):
        if name in self._FIXED and hasattr(self,name): raise AttributeError('original acquisition context is immutable')
        object.__setattr__(self,name,value)
    def __init__(self, owner, name, function, arguments, output, keepalive):
        self.owner,self.name,self.function=owner,name,function
        self.arguments,self.output=tuple(arguments),output
        self.output_address=C.addressof(output)
        self.closer,self.close_mode=owner.closer,owner.close_mode
        self.keepalive=tuple(keepalive)
        self.entered=False;self.raw_return=None;self.value=None;self.entry=None;self.state='PREPARED'

class OriginalBirthPreparation:
    """Pre-SDK root of actual original calls, slots, entries, parser and source facts."""
    _FIXED=frozenset({'api','factory','profile','profile_sha256','job','registry','thread','closer','close_mode',
        'functions','reader','scm_binding','scm_context','source_snapshot','submitted_snapshot','primary_entry','primary_slot','child','_seal'})
    def __setattr__(self,name,value):
        if name in self._FIXED and hasattr(self,name): raise AttributeError('original primary binding is immutable')
        object.__setattr__(self,name,value)
    def __init__(self,api,profile,factory,job,*,seal):
        from ._windows_t037 import _Win32LaunchApi,T037SessionProcessFactory
        if seal is not _SEAL or type(api) is not _Win32LaunchApi or (factory is not None and
                (type(factory) is not T037SessionProcessFactory or factory._api is not api
                 or factory.profile is not profile or factory._job is not job or factory._closed)):
            fail('B_PRIMARY_ORIGINAL_OWNER_REQUIRED','original launch API/factory/profile/Job required')
        require_abi()
        self._seal=seal;self.api=api;self.factory=factory;self.profile=profile;self.job=job
        self.profile_sha256=profile.sha256;self.thread=threading.get_ident();self.registry=registry_for(api)
        self.scm_binding=getattr(factory,'_scm_binding',None) if factory is not None else None
        if factory is not None:
            from ._windows_t037_admission import _require_factory_runtime
            self.scm_context=_require_factory_runtime(factory)
        else:self.scm_context=None
        self.closer,self.close_mode=handle_closer(api)
        functions={name:getattr(api.a,name) for name in ('OpenProcessToken','DuplicateTokenEx','CreateRestrictedToken','GetTokenInformation','LookupPrivilegeNameW')}
        functions['GetCurrentProcess']=api.k.GetCurrentProcess
        self.functions=MappingProxyType(functions)
        self.state='PREPARING';self.error=None;self.calls=[];self.pending=None;self.entries=[]
        self.source_snapshots=[];self.submitted_rechecks=[];self.entry_bindings={}
        self.reader=e.WindowsChildEvidenceApi.__new__(e.WindowsChildEvidenceApi)
        self.reader.a,self.reader.k=api.a,api.k;attach_registry(self.reader,self.registry)
        if not hasattr(api,'_birth_preparations'):api._birth_preparations={}
        api._birth_preparations[id(self)]=self
        if factory is not None:
            if not hasattr(factory,'_birth_preparations'):factory._birth_preparations={}
            factory._birth_preparations[id(self)]=self
    def unknown(self,error):
        if self.state!='UNKNOWN':
            self.state='UNKNOWN';self.error=error
            if not hasattr(self.api,'_retained_launch_cleanup'):self.api._retained_launch_cleanup=[]
            # Existing held-A/factory consumers already retain real ancillary cleanup UNKNOWN.
            # This is the exact original preparation/guarded closer, never a synthesized proof.
            self.api._retained_launch_cleanup.append(('birth_primary',self.close,(),error))
        if self.pending is not None:self.pending.state='UNKNOWN'
        return self.error
    def _check(self):
        if self.thread!=threading.get_ident():fail('B_PRIMARY_THREAD_MISMATCH','original preparation belongs to another thread')
        if self.state=='UNKNOWN':raise self.error
        if (self._seal is not _SEAL or self.registry is not registry_for(self.api)
                or self.registry.owner is not self.api or self.profile_sha256!=self.profile.sha256
                or self.reader.a is not self.api.a or self.reader.k is not self.api.k
                or getattr(self.api,'_birth_preparations',{}).get(id(self)) is not self
                or (self.factory is not None and (self.factory._api is not self.api or self.factory.profile is not self.profile
                    or self.factory._job is not self.job or self.factory._closed
                    or getattr(self.factory,'_birth_preparations',{}).get(id(self)) is not self))):
            error=ExecutionContractError('B_PRIMARY_CONTEXT_UNKNOWN','original preparation context changed',safe_retry=False)
            raise self.unknown(error)
        if self.factory is not None:
            from ._windows_appcontainer_gate import _participant
            try:
                # Original cleanup identity survives normal SCM stop/daemon
                # draining. New acquisitions separately require live admission.
                participant=_participant(self.factory)
                participant.check(registered=True)
                participant.gate.check(live=False)
                context=(participant.gate.runtime,participant.gate.daemon,
                         participant.gate.profile,participant.gate.provider)
                runtime,daemon,profile,provider=self.scm_context
                binding=self.scm_binding[0]
                if (self.factory._scm_binding is not self.scm_binding
                        or any(a is not b for a,b in zip(context,self.scm_context))
                        or participant.gate.binding is not binding
                        or daemon._windows_t037_profile is not profile
                        or daemon._scoped_host_provider is not provider
                        or daemon.project_authority is not binding[4]
                        or binding[4].authorization_verifier is not provider
                        or daemon._windows_t037_profile_path!=binding[5]
                        or provider._closed):
                    fail('B_PRIMARY_CONTEXT_UNKNOWN','original SCM/runtime binding changed')
            except BaseException as error:raise self.unknown(error)
        current={name:getattr(self.api.k if name=='GetCurrentProcess' else self.api.a,name) for name in self.functions}
        if any(_function(current[k])!=_function(v) for k,v in self.functions.items()) or _function(handle_closer(self.api)[0])!=_function(self.closer):
            error=ExecutionContractError('B_PRIMARY_CONTEXT_UNKNOWN','original SDK function/closer binding changed',safe_retry=False)
            raise self.unknown(error)
        for entry,binding in self.entry_bindings.values():
            if (entry.registry is not self.registry or entry.adapter is not self.api or entry.state not in ('ACQUIRED','RELEASED')
                    or (entry.handle,entry.value,entry.closer,entry.label,entry.mode,entry.thread_id)!=binding
                    or (entry.state=='ACQUIRED' and self.registry.find(self.api,entry.handle) is not entry)):
                error=ExecutionContractError('B_PRIMARY_CONTEXT_UNKNOWN','original TOKEN entry changed',safe_retry=False)
                raise self.unknown(error)
    def _admit(self):
        self._check()
        if self.factory is not None:
            from ._windows_t037_admission import _require_factory_runtime
            _require_factory_runtime(self.factory)
    def _acquire(self,name,args,*,keepalive=()):
        self._admit()
        slot=e.PTR()
        call=_TokenCall(self,name,self.functions[name],(*args,C.byref(slot)),slot,keepalive)
        self.calls.append(call);self.pending=call  # Root before the SDK can write the original output.
        try:
            self._admit()
            if C.cast(call.arguments[-1],e.PTR).value!=call.output_address:
                raise RuntimeError('original output pointer changed before entry')
            call.entered=True;call.state='ENTERED'
            call.raw_return=call.function(*call.arguments)
            raw=getattr(call.raw_return,'value',call.raw_return)
            if type(raw) not in (int,bool):raise TypeError('BOOL32 return is not an actual integral result')
            call.value=slot.value
            if not raw and call.value is None:
                call.state='NO_EFFECT';self.pending=None
                fail('B_PRIMARY_ACQUISITION_FAILED',name+' returned known NULL failure')
            if not raw or type(call.value) is not int or call.value<=0 or call.value>=((1<<64)-16):
                raise RuntimeError('native token acquisition/result is ambiguous')
            if self.registry.find(self.api,call.value) is not None:
                raise RuntimeError('new token output aliases an already-owned native resource')
            call.entry=self.registry.acquire(self.api,slot,call.closer,mode=call.close_mode,label='birth_'+name,
                keepalive=(self,call,slot,*keepalive),value_reader=lambda original:call.value)
            if (call.entry.state!='ACQUIRED' or call.entry.value!=call.value
                    or not any(x is call for x in call.entry.keepalive)):
                raise RuntimeError('native output did not transfer to its original registry entry')
            self.entries.append(call.entry)
            self.entry_bindings[id(call.entry)]=(call.entry,(call.entry.handle,call.entry.value,call.entry.closer,
                call.entry.label,call.entry.mode,call.entry.thread_id))
            call.state='TRANSFERRED';self.pending=None
            return slot,call.entry
        except BaseException as error:
            if call.state=='NO_EFFECT':raise
            raise self.unknown(error)
    def _read(self,entry):
        self._check()
        try:
            value=self.reader.read_owned_token(entry)
            if type(value) is not e.TokenObservation or value.groups is None:
                fail('B_PRIMARY_CONTEXT_UNKNOWN','actual complete submitted token observation unavailable')
            self._check()
            return value
        except BaseException as error:raise self.unknown(error)
    def prepare(self):
        self._admit()
        try:
            self.current_process_call=(self.functions['GetCurrentProcess'],())
            self.current_process_return=self.current_process_call[0]()
        except BaseException as error:raise self.unknown(error)
        access=0x0008|0x0002|0x0001|0x0080|0x0100
        _,entry=self._acquire('OpenProcessToken',(self.current_process_return,access))
        original=self._read(entry);self.source_snapshots.append(original)
        semantic(original);source_priv=privilege_map(original)
        if original.user.sid!=self.profile.service_account_sid or original.is_appcontainer:
            policy('SCM user/primary AppContainer state is not the intended original service context')
        self.source_snapshot=original
        slot,duplicate=self._acquire('DuplicateTokenEx',(entry.handle,access,None,2,1))
        duplicated=self._read(duplicate);self.source_snapshots.append(duplicated)
        if semantic(duplicated)!=semantic(original) or privilege_map(duplicated)!=source_priv:
            policy('actual duplicate changed source token security semantics')
        deleted=[p for p in duplicated.privileges if p.name!=ALLOWED_PRIVILEGE]
        if deleted:
            privileges=(LUID_AND_ATTRIBUTES*len(deleted))()
            for i,p in enumerate(deleted):
                privileges[i].Luid.LowPart=p.luid & 0xFFFFFFFF
                high=(p.luid>>32)&0xFFFFFFFF
                privileges[i].Luid.HighPart=high if high<0x80000000 else high-(1<<32)
                privileges[i].Attributes=0
            restricting=tuple(duplicated.restricted_sids)
            rows=(SID_AND_ATTRIBUTES*len(restricting))() if restricting else None
            buffers=[]
            for i,row in enumerate(restricting):
                parts=row.sid.split('-');raw=bytes((1,len(parts)-3))+int(parts[2]).to_bytes(6,'big')
                raw+=b''.join(int(n).to_bytes(4,'little') for n in parts[3:])
                buffer=C.create_string_buffer(raw);buffers.append(buffer)
                rows[i].Sid=C.addressof(buffer);rows[i].Attributes=0  # SDK input contract; observed attributes stay raw.
            slot,duplicate=self._acquire('CreateRestrictedToken',(duplicate.handle,0,0,None,len(deleted),privileges,
                len(restricting),rows),keepalive=(privileges,rows,*buffers,duplicated))
            final=self._read(duplicate)
            expected={l:v for l,v in source_priv.items() if v[0]==ALLOWED_PRIVILEGE}
            verify_returned(original,final,expected)
        else:
            final=verify_returned(original,duplicated,source_priv)
        self.primary_slot,self.primary_entry=slot,duplicate;self.submitted_snapshot=final
        self.state='READY'
        return self
    def recheck_submitted(self):
        self._check()
        if self.state not in ('READY','BOUND') or self.primary_slot.value!=self.primary_entry.value:
            error=ExecutionContractError('B_PRIMARY_CONTEXT_UNKNOWN','exact submitted output TOKEN is unavailable',safe_retry=False)
            raise self.unknown(error)
        value=self._read(self.primary_entry);self.submitted_rechecks.append(value)
        if value!=self.submitted_snapshot:
            error=ExecutionContractError('B_PRIMARY_CONTEXT_UNKNOWN','same final submitted token changed',safe_retry=False)
            raise self.unknown(error)
        return value
    def bind_child(self,child):
        from ._windows_t037 import _RetainedSuspendedChild
        self._admit()
        self.recheck_submitted()
        if (type(child) is not _RetainedSuspendedChild or child._api is not self.api
                or child._original_profile is not self.profile or child._original_factory is not self.factory
                or child._original_job is not self.job or child._create_arguments[0] is not self.primary_slot
                or child._birth_preparation is not self or not any(x is self for x in child._call_storage)):
            error=ExecutionContractError('B_PRIMARY_HANDOFF_UNKNOWN','original submitted token/child handoff differs',safe_retry=False)
            raise self.unknown(error)
        self.child=child;self.state='BOUND'
    def require_child(self,child):
        self._check()
        if (self.state!='BOUND' or self.child is not child or child._birth_preparation is not self
                or child._create_arguments[0] is not self.primary_slot
                or self.primary_slot.value!=self.primary_entry.value):
            fail('B_PRIMARY_CONTEXT_UNKNOWN','same original preparation/child/input required')
        return self.submitted_snapshot
    def close(self):
        if self.state=='RELEASED':
            if self.thread!=threading.get_ident():fail('B_PRIMARY_THREAD_MISMATCH','original close belongs to another thread')
            return  # Cached known close acknowledgment, no new SDK/query.
        self._check()
        for entry in reversed(self.entries):
            self._check()
            if entry.state=='RELEASED':continue
            try:
                entry.release_owned(self.api)
                self._check()
            except BaseException as error:raise self.unknown(error)
        self.state='RELEASED'
        self.api._birth_preparations.pop(id(self),None)
        if self.factory is not None:self.factory._birth_preparations.pop(id(self),None)

def require_no_preparation(owner):
    for prep in tuple(getattr(owner,'_birth_preparations',{}).values()):
        if type(prep) is not OriginalBirthPreparation:
            fail('B_PRIMARY_CONTEXT_UNKNOWN','unclassified original primary preparation remains')
        if prep.state=='UNKNOWN':raise prep.error
        if prep.state!='RELEASED':fail('B_PRIMARY_CONTEXT_UNKNOWN','original primary preparation is still active')
