"""Finite selected B policy and original primary flow over concrete inert SDK leaves."""
import ctypes as C
from dataclasses import replace
import struct
import threading
from types import SimpleNamespace
import pytest
from comsol_mcp import _windows_t037 as win
from comsol_mcp import _windows_t037_primary as p
from comsol_mcp import _windows_t037_child_evidence as e
from comsol_mcp import _windows_t037_resume as resume
from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._windows_owned_resources import registry_for, NativeOwnedRegistry
from tests.test_windows_t037_connected_b import connected_chain, launch_refusal, known_child_cleanup
from tests.test_windows_t037_token_groups import group_bytes, sid_bytes


def tokens():
    profile=SimpleNamespace(service_account_sid='S-1-5-21-9',service_sid='S-1-5-80-42',
        appcontainer_sid='S-1-15-2-99',capability_sids=('S-1-15-3-1',))
    groups=(e.SidAttributes('S-1-5-32-545',7),e.SidAttributes('S-1-5-5-1-2',0xC0000007),
        e.SidAttributes(profile.service_sid,7),e.SidAttributes('S-1-16-16384',0x60))
    source=e.TokenObservation(e.SidAttributes(profile.service_account_sid,0),(e.SidAttributes('S-1-5-99-7',4),),(),
        e.SidAttributes('S-1-16-16384',0x60),16384,3,(e.Privilege(23,3,p.ALLOWED_PRIVILEGE),),
        100,200,101,False,groups)
    child=replace(source,token_id=300,modified_id=301,is_appcontainer=True,
        integrity=e.SidAttributes(p.LOW_SID,0x60),integrity_rid=4096,
        groups=groups[:-1]+(e.SidAttributes(p.LOW_SID,0x60),),
        capabilities=(e.SidAttributes(profile.capability_sids[0],4),))
    return profile,source,child


def test_fixed_windows_target_abi_stride_offsets_and_real_api_binding(monkeypatch):
    p.require_abi()
    assert C.sizeof(p.LUID)==8 and C.alignment(p.LUID)==4
    assert p.LUID.HighPart.offset==4
    assert C.sizeof(p.LUID_AND_ATTRIBUTES)==12 and C.alignment(p.LUID_AND_ATTRIBUTES)==4
    assert p.LUID_AND_ATTRIBUTES.Attributes.offset==8
    assert C.sizeof(p.SID_AND_ATTRIBUTES)==16 and p.SID_AND_ATTRIBUTES.Attributes.offset==8
    rows=(p.LUID_AND_ATTRIBUTES*2)()
    rows[0].Luid.LowPart=0x89ABCDEF;rows[0].Luid.HighPart=-2;rows[0].Attributes=0
    rows[1].Luid.LowPart=7;rows[1].Luid.HighPart=1;rows[1].Attributes=0
    assert C.addressof(rows[1])-C.addressof(rows[0])==12
    assert C.string_at(C.addressof(rows),24)==struct.pack('<IiIIiI',0x89ABCDEF,-2,0,7,1,0)
    class Export:
        def __call__(self,*a):pytest.fail('binding must not invoke an SDK function')
    class DLL:
        def __getattr__(self,n):
            value=Export();setattr(self,n,value);return value
    def initialize(api):
        api.C=C;api.W=SimpleNamespace(HANDLE=e.PTR,DWORD=e.U32,BOOL=e.I32,WORD=C.c_uint16,
            BYTE=C.c_uint8,UINT=e.U32,LPCWSTR=C.c_wchar_p,LPWSTR=C.c_wchar_p)
        api.k,api.a=DLL(),DLL()
    monkeypatch.setattr(win._Win32ReadOnlyApi,'__init__',initialize)
    api=win._Win32LaunchApi()
    assert api.a.CreateRestrictedToken.restype is e.I32
    assert api.a.CreateRestrictedToken.argtypes==[e.PTR,e.U32,e.U32,C.POINTER(p.SID_AND_ATTRIBUTES),
        e.U32,C.POINTER(p.LUID_AND_ATTRIBUTES),e.U32,C.POINTER(p.SID_AND_ATTRIBUTES),C.POINTER(e.PTR)]
    assert len(api.a.CreateRestrictedToken.argtypes)==9


@pytest.mark.parametrize('mode',['preserve','deny_only','optional_app','optional_all','absent_privilege',
    'disabled_privilege','used_for_access','mandatory1','mandatory3'])
def test_selected_policy_accepts_only_explicit_finite_source_relations(mode):
    profile,source,child=tokens()
    if mode=='deny_only':child=replace(child,groups=(replace(child.groups[0],attributes=0x11),)+child.groups[1:])
    if mode.startswith('optional_'):
        key=profile.appcontainer_sid if mode=='optional_app' else 'S-1-15-2-1'
        child=replace(child,groups=child.groups+(e.SidAttributes(key,7),))
    if mode=='absent_privilege':child=replace(child,privileges=())
    if mode=='disabled_privilege':
        source=replace(source,privileges=(e.Privilege(23,0,p.ALLOWED_PRIVILEGE),));child=replace(child,privileges=source.privileges)
    if mode=='used_for_access':child=replace(child,privileges=(e.Privilege(23,0x80000003,p.ALLOWED_PRIVILEGE),))
    if mode.startswith('mandatory'):child=replace(child,mandatory_policy=int(mode[-1]))
    assert p.child_policy(source,child,profile) is True
    assert source.token_id!=child.token_id and source.modified_id!=child.modified_id


def mutate(child,mode,profile):
    if mode=='groups_none':return replace(child,groups=None)
    if mode=='group_deleted':return replace(child,groups=child.groups[1:])
    if mode=='logon_deleted':return replace(child,groups=child.groups[:1]+child.groups[2:])
    if mode=='service_deleted':return replace(child,groups=child.groups[:2]+child.groups[3:])
    if mode=='group_duplicate':return replace(child,groups=child.groups+(child.groups[0],))
    if mode=='unknown_group_bits':return replace(child,groups=(replace(child.groups[0],attributes=0x10000007),)+child.groups[1:])
    if mode=='deny_enabled':return replace(child,groups=(replace(child.groups[0],attributes=0x17),)+child.groups[1:])
    if mode=='group_attr_changed':return replace(child,groups=(replace(child.groups[0],attributes=5),)+child.groups[1:])
    if mode=='extra_group':return replace(child,groups=child.groups+(e.SidAttributes('S-1-5-99-88',7),))
    if mode=='lpac_extra':return replace(child,groups=child.groups+(e.SidAttributes('S-1-15-2-2',7),))
    if mode=='app_wrong_attrs':return replace(child,groups=child.groups+(e.SidAttributes(profile.appcontainer_sid,4),))
    if mode=='restrict_missing':return replace(child,restricted_sids=())
    if mode=='restrict_changed':return replace(child,restricted_sids=(replace(child.restricted_sids[0],attributes=0),))
    if mode=='restrict_duplicate':return replace(child,restricted_sids=child.restricted_sids*2)
    if mode=='cap_missing':return replace(child,capabilities=())
    if mode=='cap_attrs':return replace(child,capabilities=(replace(child.capabilities[0],attributes=7),))
    if mode=='cap_duplicate':return replace(child,capabilities=child.capabilities*2)
    if mode=='IL_high':return replace(child,integrity=e.SidAttributes('S-1-16-16384',0x60),integrity_rid=16384,
        groups=child.groups[:-1]+(e.SidAttributes('S-1-16-16384',0x60),))
    if mode=='IL_attributes':return replace(child,integrity=replace(child.integrity,attributes=0x20),
        groups=child.groups[:-1]+(e.SidAttributes(p.LOW_SID,0x20),))
    if mode=='IL_missing_group':return replace(child,groups=child.groups[:-1])
    if mode=='IL_extra_group':return replace(child,groups=child.groups+(e.SidAttributes('S-1-16-8192',0x60),))
    if mode.startswith('mandatory_'):return replace(child,mandatory_policy=int(mode.split('_')[1]))
    if mode=='user':return replace(child,user=e.SidAttributes('S-1-5-21-77',0))
    if mode=='auth':return replace(child,authentication_id=999)
    if mode=='not_app':return replace(child,is_appcontainer=False)
    if mode=='unknown_id':return replace(child,modified_id=0)
    if mode=='extra_disabled_priv':return replace(child,privileges=child.privileges+(e.Privilege(24,0,'SeDebugPrivilege'),))
    if mode=='priv_name':return replace(child,privileges=(e.Privilege(23,0,'SeDebugPrivilege'),))
    if mode=='priv_luid':return replace(child,privileges=(e.Privilege(999,0,p.ALLOWED_PRIVILEGE),))
    if mode=='priv_unresolved':return replace(child,privileges=(e.Privilege(23,0,None),))
    if mode=='priv_unknown_bits':return replace(child,privileges=(e.Privilege(23,0x80,p.ALLOWED_PRIVILEGE),))
    if mode=='priv_removed':return replace(child,privileges=(e.Privilege(23,4,p.ALLOWED_PRIVILEGE),))
    if mode=='priv_duplicate':return replace(child,privileges=child.privileges*2)
    raise AssertionError(mode)

BAD=['groups_none','group_deleted','logon_deleted','service_deleted','group_duplicate','unknown_group_bits',
    'deny_enabled','group_attr_changed','extra_group','lpac_extra','app_wrong_attrs','restrict_missing','restrict_changed',
    'restrict_duplicate','cap_missing','cap_attrs','cap_duplicate','IL_high','IL_attributes','IL_missing_group',
    'IL_extra_group','mandatory_0','mandatory_2','mandatory_4','user','auth','not_app','unknown_id',
    'extra_disabled_priv','priv_name','priv_luid','priv_unresolved','priv_unknown_bits','priv_removed','priv_duplicate']

@pytest.mark.parametrize('mode',BAD)
def test_selected_policy_refuses_missing_ambiguous_or_unapproved_facts(mode):
    profile,source,child=tokens()
    with pytest.raises(ExecutionContractError) as error:p.child_policy(source,mutate(child,mode,profile),profile)
    assert error.value.code=='B_TOKEN_POLICY_UNVERIFIED'

@pytest.mark.parametrize('kind',['enable','default','already_submitted_app','already_deny_only'])
def test_no_enabled_promotions_and_optional_exceptions_never_promote_existing_source_rows(kind):
    profile,source,child=tokens()
    if kind in ('enable','default'):
        source=replace(source,privileges=(e.Privilege(23,0,p.ALLOWED_PRIVILEGE),))
        child=replace(child,privileges=(e.Privilege(23,2 if kind=='enable' else 1,p.ALLOWED_PRIVILEGE),))
    elif kind=='already_submitted_app':
        source=replace(source,groups=source.groups+(e.SidAttributes(profile.appcontainer_sid,1),))
        child=replace(child,groups=child.groups+(e.SidAttributes(profile.appcontainer_sid,7),))
    else:
        source=replace(source,groups=(replace(source.groups[0],attributes=0x11),)+source.groups[1:])
    with pytest.raises(ExecutionContractError):p.child_policy(source,child,profile)


def encode(token,kind,base):
    if kind==10:
        raw=bytearray(56);struct.pack_into('<QQ',raw,0,token.token_id,token.authentication_id)
        struct.pack_into('<Q',raw,48,token.modified_id);return bytes(raw)
    if kind in (1,25):
        row=token.user if kind==1 else token.integrity
        return struct.pack('<QI4x',base+16,row.attributes)+sid_bytes(row.sid)
    if kind in (2,11,30):
        rows={2:token.groups,11:token.restricted_sids,30:token.capabilities}[kind]
        return group_bytes(tuple((x.sid,x.attributes) for x in rows),base)
    if kind in (27,29):return struct.pack('<I',token.mandatory_policy if kind==27 else int(token.is_appcontainer))
    if kind==3:return struct.pack('<I',len(token.privileges))+b''.join(struct.pack('<QI',x.luid,x.attributes) for x in token.privileges)
    raise AssertionError(kind)


def child_from_actual_input(api,profile):
    prep=next(iter(api._birth_preparations.values()))
    source=prep.submitted_snapshot
    return replace(source,token_id=3003,modified_id=8007,is_appcontainer=True,
        integrity=e.SidAttributes(p.LOW_SID,source.integrity.attributes),integrity_rid=4096,
        groups=tuple(e.SidAttributes(p.LOW_SID,x.attributes) if x.sid==source.integrity.sid else x for x in source.groups),
        capabilities=tuple(e.SidAttributes(s,4) for s in profile.capability_sids))


@pytest.mark.parametrize('present',['allowed','disabled','absent','delete_disabled_extra','delete_enabled_extra'])
def test_real_normal_factory_prepares_actual_primary_and_transfers_same_output_before_CREATE(monkeypatch,tmp_path,present):
    with connected_chain(monkeypatch,tmp_path) as (_,factory,api,sdk,command,events,state,_):
        if present=='disabled':state['primary_privileges']=[(23,p.ALLOWED_PRIVILEGE,0)]
        if present=='absent':state['primary_privileges']=[]
        if present.startswith('delete_'):
            state['primary_privileges'].append((0x1234567887654321,'SeDebugPrivilege',0 if 'disabled' in present else 2))
        monkeypatch.setattr(sdk,'token_bytes',lambda kind,base,read=False:encode(child_from_actual_input(api,factory.profile),kind,base))
        def names(system,luid,storage,used):
            value=C.cast(luid,C.POINTER(e.U64)).contents.value
            text=p.ALLOWED_PRIVILEGE if value==23 else 'SeDebugPrivilege'
            raw=text.encode('utf-16-le');C.memmove(storage,raw+b'\0\0',len(raw)+2)
            C.cast(used,C.POINTER(e.U32)).contents.value=len(text);return 1
        # The SDK constructor binds this existing export via sdk.privilege_name.
        monkeypatch.setattr(sdk,'privilege_name',names)
        error=launch_refusal(factory,command)
        assert error.code=='NATIVE_CHILD_PROOF_UNAVAILABLE' and 'RESUME' not in events and not resume._pending
        owner=api._last_connected_b_observer;prep=owner.preparation
        assert type(prep) is p.OriginalBirthPreparation and prep.state=='RELEASED'
        assert prep.child is owner.process and owner.process._create_arguments[0] is prep.primary_slot
        assert owner.submitted_primary is prep.submitted_snapshot and prep.primary_entry.state=='RELEASED'
        assert owner.result.finite_state=='MATCHED_FINITE_CHECKS_ONLY' and owner.result.qualification=='UNQUALIFIED'
        assert prep.source_snapshot.token_id==55 and prep.submitted_snapshot.token_id in (56,57)
        assert owner.result.observations.token_before.token_id==3003
        assert len(prep.submitted_rechecks)==3 and all(x==prep.submitted_snapshot for x in prep.submitted_rechecks)
        assert any(x is prep for x in prep.child._call_storage)
        assert events.count(('close',55))==events.count(('close',56))==1
        if present.startswith('delete_'):
            call=next(x for x in prep.calls if x.name=='CreateRestrictedToken')
            assert len(call.arguments)==9 and call.arguments[1:4]==(0,0,None)
            assert call.arguments[4]==1 and call.arguments[6]==1
            assert call.arguments[7][0].Attributes==0
            assert prep.source_snapshot.restricted_sids==prep.submitted_snapshot.restricted_sids==(e.SidAttributes('S-1-5-99-7',4),)
            assert state['primary_calls']==[('CreateRestrictedToken',(0x1234567887654321,),1)]
            assert events.count(('close',57))==1
        else:assert not state['primary_calls']
        known_child_cleanup(api,factory)


@pytest.mark.parametrize('mode',[x for x in BAD if x not in ('groups_none','priv_unresolved','unknown_id')])
def test_connected_B_selected_policy_checks_actual_child_buffers_and_never_issues_Resume(monkeypatch,tmp_path,mode):
    with connected_chain(monkeypatch,tmp_path) as (_,factory,api,sdk,command,events,state,_):
        monkeypatch.setattr(sdk,'token_bytes',lambda kind,base,read=False:encode(mutate(child_from_actual_input(api,factory.profile),mode,factory.profile),kind,base))
        def names(system,luid,storage,used):
            value=C.cast(luid,C.POINTER(e.U64)).contents.value
            child=mutate(child_from_actual_input(api,factory.profile),mode,factory.profile)
            text=next(x.name for x in child.privileges if x.luid==value)
            raw=text.encode('utf-16-le');C.memmove(storage,raw+b'\0\0',len(raw)+2)
            C.cast(used,C.POINTER(e.U32)).contents.value=len(text);return 1
        monkeypatch.setattr(sdk,'privilege_name',names)
        error=launch_refusal(factory,command)
        assert error.code=='NATIVE_CHILD_PROOF_UNAVAILABLE' and 'RESUME' not in events and not resume._pending
        result=api._last_connected_b_observer.result
        assert result.finite_state in ('MISMATCH','UNVERIFIED') and result.qualification=='UNQUALIFIED'
        assert not all(x.matches is True for x in result.finite_comparisons)
        assert result.selected_policy_issues or result.issues
        known_child_cleanup(api,factory)


@pytest.mark.parametrize('stage',['open','duplicate','restrict'])
@pytest.mark.parametrize('fault',['write_throw','return_throw','false_nonnull','true_null','transfer_throw'])
def test_acquisition_unknown_retains_actual_original_slot_and_no_later_SDK_or_retry(monkeypatch,tmp_path,stage,fault):
    with connected_chain(monkeypatch,tmp_path) as (_,factory,api,sdk,command,events,state,static):
        if stage=='restrict':state['primary_privileges'].append((24,'SeDebugPrivilege',0))
        name={'open':'OpenProcessToken','duplicate':'DuplicateTokenEx','restrict':'CreateRestrictedToken'}[stage]
        original=getattr(api.a,name);captured=[]
        class Opaque:
            @property
            def value(self):raise OSError('software BOOL return normalization unknown')
        def invoke(*args):
            prep=next(iter(api._birth_preparations.values()));captured.append(prep)
            call=prep.pending
            assert call.function is invoke and call.entered and C.cast(args[-1],e.PTR).value==call.output_address
            original(*args)
            if fault=='write_throw':raise OSError('software original output written then call raised')
            if fault=='return_throw':return Opaque()
            if fault=='false_nonnull':return 0
            if fault=='true_null':C.cast(args[-1],C.POINTER(e.PTR)).contents.value=None
            return 1
        monkeypatch.setattr(api.a,name,invoke)
        if fault=='transfer_throw':
            original_acquire=NativeOwnedRegistry.acquire
            def acquire(registry,adapter,handle,closer,**kw):
                entry=original_acquire(registry,adapter,handle,closer,**kw)
                if kw.get('label')=='birth_'+name:raise RuntimeError('software registry transfer failed after actual insertion')
                return entry
            monkeypatch.setattr(NativeOwnedRegistry,'acquire',acquire)
        with pytest.raises(Exception):factory(command)
        prep=captured[0];call=prep.pending
        assert type(prep) is p.OriginalBirthPreparation and prep.state=='UNKNOWN'
        assert call.entered and call.state=='UNKNOWN' and call.output is not None and call.closer is prep.closer
        assert call.output.value==(None if fault=='true_null' else {'open':55,'duplicate':56,'restrict':57}[stage])
        assert api._birth_preparations[id(prep)] is prep and factory._birth_preparations[id(prep)] is prep
        assert factory._held_static_a.state=='UNKNOWN' and not factory._held_static_a.reader.retired
        assert not any(isinstance(x,tuple) and x[0] in ('close','close-image','terminate') for x in events)
        assert 'CREATE_SUSPENDED' not in events and 'RESUME' not in events
        effects=(list(events),list(state['primary_queries']),list(static.calls))
        for action in (lambda:factory(command),factory.preflight,factory.close,prep.close):
            with pytest.raises(Exception):action()
        assert (events,state['primary_queries'],static.calls)==effects
        assert len(captured)==1


def test_known_NULL_failure_is_known_no_effect_not_a_false_unknown_or_duplicate_retry(monkeypatch,tmp_path):
    with connected_chain(monkeypatch,tmp_path) as (_,factory,api,sdk,command,events,state,_):
        captured=[]
        def absent(*args):captured.append(next(iter(api._birth_preparations.values())));return 0
        monkeypatch.setattr(api.a,'OpenProcessToken',absent)
        error=launch_refusal(factory,command)
        assert error.code=='B_PRIMARY_ACQUISITION_FAILED'
        assert captured[0].calls[0].state=='NO_EFFECT' and captured[0].state=='RELEASED'
        assert not api._birth_preparations and not factory._birth_preparations
        assert 'CREATE_SUSPENDED' not in events and 'RESUME' not in events
        known_child_cleanup(api,factory)


def test_borrowed_TOKEN_reader_does_not_open_or_double_close_original_entry(monkeypatch):
    from tests.test_windows_t037_token_groups import TokenSDK
    sdk=TokenSDK(monkeypatch)
    out=e.PTR();assert sdk.open_token(101,8,C.byref(out))==1
    entry=sdk.api._owned_handle(out.value,'child_evidence_token')
    snapshot=sdk.api.read_owned_token(entry)
    assert snapshot.groups is not None and sdk.opens==[(101,8)] and not sdk.closed
    entry.release_owned(sdk.api)
    assert sdk.closed==[901]


@pytest.mark.parametrize('field',['user','authentication','groups','restricting','capabilities','integrity','mandatory','appcontainer','privileges'])
def test_duplicate_complete_security_semantic_drift_refuses_before_CREATE_with_known_close(monkeypatch,tmp_path,field):
    with connected_chain(monkeypatch,tmp_path) as (_,factory,api,sdk,command,events,state,_):
        original=api.a.GetTokenInformation
        def query(token,kind,storage,capacity,used):
            if getattr(token,'value',token)!=56:return original(token,kind,storage,capacity,used)
            state['primary_queries'].append((56,kind,storage is not None))
            prep=next(iter(api._birth_preparations.values()))
            value=replace(prep.source_snapshot,token_id=56,modified_id=156)
            if field=='user':value=replace(value,user=replace(value.user,attributes=1))
            if field=='authentication':value=replace(value,authentication_id=value.authentication_id+1)
            if field=='groups':value=replace(value,groups=(replace(value.groups[0],attributes=5),)+value.groups[1:])
            if field=='restricting':value=replace(value,restricted_sids=())
            if field=='capabilities':value=replace(value,capabilities=(e.SidAttributes('S-1-15-3-99',4),))
            if field=='integrity':value=replace(value,integrity=e.SidAttributes('S-1-16-8192',0x60),integrity_rid=8192,
                groups=value.groups[:-1]+(e.SidAttributes('S-1-16-8192',0x60),))
            if field=='mandatory':value=replace(value,mandatory_policy=1)
            if field=='appcontainer':value=replace(value,is_appcontainer=True)
            if field=='privileges':value=replace(value,privileges=(replace(value.privileges[0],attributes=0),))
            raw=encode(value,kind,C.addressof(storage) if storage is not None else 0)
            C.cast(used,C.POINTER(e.U32)).contents.value=len(raw)
            if storage is None:return 0
            assert capacity>=len(raw);C.memmove(storage,raw,len(raw));return 1
        monkeypatch.setattr(api.a,'GetTokenInformation',query)
        error=launch_refusal(factory,command)
        assert error.code=='B_TOKEN_POLICY_UNVERIFIED' and 'CREATE_SUSPENDED' not in events and 'RESUME' not in events
        assert events.count(('close',55))==events.count(('close',56))==1
        assert not api._birth_preparations and not factory._birth_preparations
        known_child_cleanup(api,factory)


@pytest.mark.parametrize('stage,threshold',[('pre_CREATE',2),('B_before',4),('B_after',6)])
def test_final_submitted_same_instance_drift_is_sticky_and_stops_all_later_SDK(monkeypatch,tmp_path,stage,threshold):
    with connected_chain(monkeypatch,tmp_path) as (_,factory,api,sdk,command,events,state,static):
        original=api.a.GetTokenInformation;reads=[];boundary=[];roots=[]
        def query(token,kind,storage,capacity,used):
            ok=original(token,kind,storage,capacity,used)
            if getattr(token,'value',token)==56 and kind==10 and storage is not None:
                index=len(reads);reads.append(index)
                if index>=threshold:
                    C.cast(C.addressof(storage)+48,C.POINTER(e.U64)).contents.value+=1
                    roots.append(next(iter(api._birth_preparations.values())))
                    boundary[:]=[list(events),list(state['primary_queries']),list(static.calls)]
            return ok
        monkeypatch.setattr(api.a,'GetTokenInformation',query)
        with pytest.raises(Exception):factory(command)
        prep=roots[0]
        assert prep.state=='UNKNOWN' and len(roots)==2 and prep.submitted_rechecks[-1]!=prep.submitted_snapshot
        assert ('CREATE_SUSPENDED' in events)==(stage!='pre_CREATE') and 'RESUME' not in events
        assert factory._held_static_a.state=='UNKNOWN' and not factory._held_static_a.reader.retired
        assert [events,state['primary_queries'],static.calls]==boundary
        for action in (lambda:factory(command),factory.preflight,factory.close,prep.close):
            with pytest.raises(Exception):action()
        assert [events,state['primary_queries'],static.calls]==boundary
        if stage!='pre_CREATE':
            assert state['alive'] and prep.child.never_resumed and not prep.child._closed
            holder=object()
            prep.child.transfer_owner(api,holder)  # Same actual object, no SDK or adoption.
            assert prep.child._owner is holder
            for action in (prep.child.poll,lambda:prep.child.wait(.01),prep.child.terminate,
                           lambda:prep.child.cleanup_owned(holder,timeout=.01),prep.child.note_resume_attempt):
                with pytest.raises(Exception):action()
            assert [events,state['primary_queries'],static.calls]==boundary


@pytest.mark.parametrize('which',['factory_api','factory_profile','SCM_binding','reader_adapter','SDK_function','closer','registry_entry'])
def test_actual_primary_context_substitution_latches_before_more_SDK_or_cleanup(monkeypatch,tmp_path,which):
    with connected_chain(monkeypatch,tmp_path) as (_,factory,api,sdk,command,events,state,static):
        original=api.a.GetTokenInformation;roots=[];boundary=[]
        def query(*args):
            ok=original(*args)
            if not roots:
                prep=next(iter(api._birth_preparations.values()));roots.append(prep)
                boundary[:]=[list(events),list(state['primary_queries']),list(static.calls)]
                if which=='factory_api':factory._api=object()
                if which=='factory_profile':factory.profile=replace(factory.profile)
                if which=='SCM_binding':factory._scm_binding=object()
                if which=='reader_adapter':prep.reader.a=SimpleNamespace()
                if which=='SDK_function':api.a.LookupPrivilegeNameW=lambda *args:pytest.fail('substituted function entered')
                if which=='closer':api.k.CloseHandle=lambda *args:pytest.fail('substituted closer entered')
                if which=='registry_entry':prep.entries[0].adapter=object()
            return ok
        monkeypatch.setattr(api.a,'GetTokenInformation',query)
        with pytest.raises(Exception):factory(command)
        prep=roots[0]
        assert prep.state=='UNKNOWN' and api._birth_preparations[id(prep)] is prep
        if which=='factory_api':assert factory._api is api
        assert 'CREATE_SUSPENDED' not in events and 'RESUME' not in events
        assert [events,state['primary_queries'],static.calls]==boundary
        for action in (lambda:factory(command),factory.preflight,factory.close,prep.close):
            with pytest.raises(Exception):action()
        assert [events,state['primary_queries'],static.calls]==boundary


def test_foreign_thread_cannot_read_or_close_or_poison_original_primary_owner(monkeypatch,tmp_path):
    with connected_chain(monkeypatch,tmp_path) as (_,factory,api,sdk,command,events,state,_):
        original=api.a.GetTokenInformation;seen=[];errors=[]
        def query(*args):
            if not seen:
                prep=next(iter(api._birth_preparations.values()));seen.append(prep)
                before=(list(events),list(state['primary_queries']))
                def foreign():
                    for action in (prep.close,lambda:prep.reader.read_owned_token(prep.entries[0])):
                        try:action()
                        except Exception as error:errors.append(error)
                        else:errors.append(None)
                thread=threading.Thread(target=foreign);thread.start();thread.join(2)
                assert not thread.is_alive() and len(errors)==2 and all(error is not None for error in errors)
                assert prep.state=='PREPARING' and prep.error is None and before==(events,state['primary_queries'])
            return original(*args)
        monkeypatch.setattr(api.a,'GetTokenInformation',query)
        error=launch_refusal(factory,command)
        assert error.code=='NATIVE_CHILD_PROOF_UNAVAILABLE' and seen[0].state=='RELEASED'
        assert api._last_connected_b_observer.result.finite_state=='MATCHED_FINITE_CHECKS_ONLY'
        known_child_cleanup(api,factory)


@pytest.mark.parametrize('handle',[55,56])
@pytest.mark.parametrize('fault',['false','throw_after_close','normalization_throw'])
def test_original_primary_close_UNKNOWN_is_one_entry_and_stops_all_followup_cleanup(monkeypatch,tmp_path,fault,handle):
    with connected_chain(monkeypatch,tmp_path) as (_,factory,api,sdk,command,events,state,static):
        original=api.k.CloseHandle;roots=[];boundary=[]
        class Opaque:
            @property
            def value(self):raise OSError('software close result normalization unknown')
            def __bool__(self):raise OSError('software close result normalization unknown')
        def close(actual):
            if getattr(actual,'value',actual)!=handle:return original(actual)
            prep=next(iter(api._birth_preparations.values()));roots.append(prep)
            events.append(('primary-close-enter',handle))
            if fault=='throw_after_close':original(actual)
            boundary[:]=[list(events),list(state['primary_queries']),list(static.calls)]
            if fault=='throw_after_close':raise OSError('software close performed then receipt lost')
            return Opaque() if fault=='normalization_throw' else 0
        monkeypatch.setattr(api.k,'CloseHandle',close)
        with pytest.raises(Exception):factory(command)
        prep=roots[0]
        assert prep.state=='UNKNOWN' and len(roots)==1
        entry=next(x for x in prep.entries if x.value==handle)
        call=next(x for x in prep.calls if x.value==handle)
        assert entry.state=='UNKNOWN' and entry.handle==call.output.value and entry.closer is prep.closer
        assert any(x is call.output for x in entry.keepalive)
        if handle==56:assert prep.entries[0].state=='ACQUIRED' and ('close',55) not in events
        else:assert prep.primary_entry.state=='RELEASED' and events.count(('close',56))==1
        assert [events,state['primary_queries'],static.calls]==boundary and 'RESUME' not in events
        for action in (prep.close,lambda:factory(command),factory.preflight,factory.close):
            with pytest.raises(Exception):action()
        assert [events,state['primary_queries'],static.calls]==boundary and len(roots)==1


def test_confirmed_primary_close_ack_is_cached_without_query_or_second_close(monkeypatch,tmp_path):
    with connected_chain(monkeypatch,tmp_path) as (_,factory,api,sdk,command,events,state,_):
        error=launch_refusal(factory,command)
        prep=api._last_connected_b_observer.preparation
        assert error.code=='NATIVE_CHILD_PROOF_UNAVAILABLE' and prep.state=='RELEASED'
        before=(list(events),list(state['primary_queries']))
        prep.close();prep.close()
        assert before==(events,state['primary_queries']) and all(x.state=='RELEASED' for x in prep.entries)
        known_child_cleanup(api,factory)


def test_exact_primary_slot_substitution_is_rejected_before_original_CREATE_entry(monkeypatch,tmp_path):
    with connected_chain(monkeypatch,tmp_path) as (_,factory,api,sdk,command,events,state,_):
        original=win._RetainedSuspendedChild.create_original;captured=[]
        def substituted(child):
            prep=child._birth_preparation;captured.append(prep)
            assert child._create_arguments[0] is prep.primary_slot and prep.primary_entry.value==56
            prep.primary_slot.value=57  # Same ctypes object, different native HANDLE: never an adopted replacement.
            return original(child)
        monkeypatch.setattr(win._RetainedSuspendedChild,'create_original',substituted)
        with pytest.raises(Exception):factory(command)
        prep=captured[0]
        assert 'CREATE_SUSPENDED' not in events and 'RESUME' not in events
        assert prep.child._call_entered is False and prep.primary_entry.state=='RELEASED'
        assert events.count(('close',55))==events.count(('close',56))==1
        known_child_cleanup(api,factory)


@pytest.mark.parametrize('fault',['export_swapped','original_UNKNOWN'])
def test_each_privilege_lookup_stops_immediately_on_original_context_loss(monkeypatch,tmp_path,fault):
    with connected_chain(monkeypatch,tmp_path) as (_,factory,api,sdk,command,events,state,static):
        state['primary_privileges']=[(23,p.ALLOWED_PRIVILEGE,3),(24,'SeDebugPrivilege',0)]
        original=api.a.LookupPrivilegeNameW
        original_error=OSError('SOFTWARE original preparation became UNKNOWN during valid lookup')
        lookups=[];roots=[];boundary=[];contexts_before=[];results=[]
        def replacement(*args):
            lookups.append(('replacement_entered',C.cast(args[1],C.POINTER(e.U64)).contents.value))
            return original(*args)
        def first_lookup(*args):
            value=C.cast(args[1],C.POINTER(e.U64)).contents.value
            lookups.append(('original_entered',value))
            prep=next(iter(api._birth_preparations.values()));roots.append(prep)
            contexts=getattr(prep.reader,'_privilege_name_calls',())
            context=contexts[-1] if contexts else None
            contexts_before.append(None if context is None else {
                'context':context,'function':context['function'],'luid_slot':context['luid_slot'],
                'name_storage':context['name_storage'],'length_slot':context['length_slot'],
                'arguments':context['arguments'],'raw_before':context['raw_return']})
            result=original(*args);results.append(result)  # Valid output name/length first, not an early fixture failure.
            if len(lookups)==1:
                assert value==23 and result==1
                if fault=='export_swapped':api.a.LookupPrivilegeNameW=replacement
                else:assert prep.unknown(original_error) is original_error
                boundary[:]=[list(lookups),list(events),list(state['primary_queries']),list(static.calls),list(sdk.calls)]
            return result
        monkeypatch.setattr(api.a,'LookupPrivilegeNameW',first_lookup)
        with pytest.raises(Exception):factory(command)
        prep=roots[0]
        assert lookups==[('original_entered',23)]  # r3 reaches a second real SDK-seam call: initial counter FAIL here.
        assert prep.state=='UNKNOWN' and prep.error is not None
        if fault=='original_UNKNOWN':assert prep.error is original_error
        assert factory._held_static_a.state=='UNKNOWN' and not factory._held_static_a.reader.retired
        assert api._birth_preparations[id(prep)] is factory._birth_preparations[id(prep)] is prep
        assert prep.calls[0].entry.state=='ACQUIRED' and prep.calls[0].output.value==55
        assert 'CREATE_SUSPENDED' not in events and 'RESUME' not in events and not resume._pending
        assert [lookups,events,state['primary_queries'],static.calls,sdk.calls]==boundary
        saved=contexts_before[0];assert saved is not None
        context=saved['context']
        assert saved['function'] is first_lookup and saved['raw_before'] is None
        assert saved['arguments'][1]._obj is saved['luid_slot'] and saved['luid_slot'].value==23
        assert saved['arguments'][2] is saved['name_storage'] and saved['arguments'][3]._obj is saved['length_slot']
        assert context is prep.reader._privilege_name_calls[0] and context['entry'] is prep.calls[0].entry
        assert context['entered'] and context['returned'] and context['raw_return'] is results[0]
        assert context['length_slot'].value==len(p.ALLOWED_PRIVILEGE)
        assert C.string_at(C.addressof(context['name_storage']),2*context['length_slot'].value).decode('utf-16-le')==p.ALLOWED_PRIVILEGE
        for action in (lambda:factory(command),factory.preflight,factory.close,prep.close):
            with pytest.raises(Exception):action()
        assert [lookups,events,state['primary_queries'],static.calls,sdk.calls]==boundary


def test_guarded_multi_privilege_source_lookup_keeps_normal_deletion_and_known_cleanup(monkeypatch,tmp_path):
    with connected_chain(monkeypatch,tmp_path) as (_,factory,api,sdk,command,events,state,_):
        state['primary_privileges']=[(23,p.ALLOWED_PRIVILEGE,3),(24,'SeDebugPrivilege',0)]
        error=launch_refusal(factory,command)
        owner=api._last_connected_b_observer;prep=owner.preparation
        assert error.code=='NATIVE_CHILD_PROOF_UNAVAILABLE' and prep.state=='RELEASED'
        assert owner.result.finite_state=='MATCHED_FINITE_CHECKS_ONLY' and owner.result.qualification=='UNQUALIFIED'
        assert [(x.luid,x.name,x.attributes) for x in prep.source_snapshot.privileges]==[(23,p.ALLOWED_PRIVILEGE,3),(24,'SeDebugPrivilege',0)]
        assert [(x.luid,x.name,x.attributes) for x in prep.submitted_snapshot.privileges]==[(23,p.ALLOWED_PRIVILEGE,3)]
        calls=prep.reader._privilege_name_calls
        assert [x['luid_slot'].value for x in calls]==[23,24,23,24,23,23,23,23]
        assert len({id(x['name_storage']) for x in calls})==len(calls)
        for context in calls:
            assert context['entry'] in prep.entries and context['entry'].state=='RELEASED'
            assert p._function(context['function'])==p._function(prep.functions['LookupPrivilegeNameW'])
            assert context['entered'] and context['returned'] and context['raw_return']==1
            assert context['arguments'][1]._obj is context['luid_slot']
            assert context['arguments'][2] is context['name_storage']
            assert context['arguments'][3]._obj is context['length_slot']
            assert context['length_slot'].value==len(p.ALLOWED_PRIVILEGE if context['luid_slot'].value==23 else 'SeDebugPrivilege')
        assert state['primary_calls']==[('CreateRestrictedToken',(24,),1)]
        assert events.count('CREATE_SUSPENDED')==1 and 'RESUME' not in events and not resume._pending
        assert events.count(('close',55))==events.count(('close',56))==events.count(('close',57))==1
        known_child_cleanup(api,factory)
