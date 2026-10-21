"""Actual closed group / Executor / Store / SDK structures, public fixtures only."""
from dataclasses import replace
from types import SimpleNamespace
import ctypes as C,hashlib,json,uuid
import pytest
from comsol_mcp import _windows_filter_group as group,_w26_os_resources as res
from comsol_mcp import _windows_host_change as changes,_windows_t037_wfp as wfp
from comsol_mcp import _windows_owned_resources as owned
from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._operation_store import OperationStore
from comsol_mcp._project_authority import ProjectAuthority
from tests.test_windows_host_change import (_profile,_provider_sublayer_plan,FakeWfpSequence,_write_provider,_apply_grant,_restore_sequence_sha,_grant,_events_v2)


def profile_and_plan(*,zero_allows=False):
    base=_profile();p=base.windows_isolation;seed=p.wfp_grants[0];grants=[]
    if not zero_allows:
        for index,(key,(direction,family,layer)) in enumerate(group.LAYERS.items()):
            role='server' if index%2==0 else 'worker';image=next(i for i in base.launch_images if i.role==role)
            grants.append(replace(seed,role=role,application_path=image.path,direction=direction,layer=layer,layer_key=key,
                address_family=family,protocol=6 if index<2 else 17,local_addresses=('127.0.0.1/32',) if family=='IPV4' else ('::1/128',),
                remote_addresses=('127.0.0.1/32',) if family=='IPV4' else ('::1/128',),local_ports=(4000+index,),remote_ports=(5000+index,)))
    keys=tuple('{'+str(uuid.UUID(int=1000+i)).upper()+'}' for i in range(4+len(grants)))
    profile=replace(base,windows_isolation=replace(p,wfp_grants=tuple(grants),wfp_filter_keys=keys))
    parents=res.validate_host_change_plan(_provider_sublayer_plan(profile))['change_manifest']
    members=[];gid='{'+str(uuid.UUID(int=900)).upper()+'}'
    for index,key in enumerate(group.LAYERS):
        direction,family,_=group.LAYERS[key]
        members.append({'schema':group.SCHEMA,'group_id':gid,'group_sha256':'0'*64,'filter_key':keys[index],
            'provider_key':p.wfp_provider_key,'sublayer_key':p.wfp_sublayer_key,'action':'BLOCK','weight':10,
            'flags':1,'layer_key':key,'direction':direction,'address_family':family,'principal_sid':p.appcontainer_sid,'grant_sha256':None})
    for index,grant in enumerate(grants):
        members.append({**members[index], 'filter_key':keys[4+index],'action':'PERMIT','weight':100+index,'grant_sha256':group.digest(grant.as_dict())})
    fingerprint=group.group_fingerprint(profile,members)
    for member in members:member['group_sha256']=fingerprint
    actions=[]
    for a in parents:
        actions.append({k:a[k] for k in ['action_id','resource_kind','resource_name','operation','desired']})
    actions += [{'action_id':'filter-'+str(i),'resource_kind':'wfp_filter','resource_name':'filter-'+str(i),'operation':'CREATE','desired':m} for i,m in enumerate(members)]
    for a in actions:a.update(baseline_identity=None,baseline_sha256=None,backup_path=None,backup_sha256=None)
    plan=res.build_host_change_plan(profile,change_manifest=actions,operator_inputs={})
    return profile,plan


class GroupSDK(FakeWfpSequence):
    def _row(self,profile,a):
        marker=wfp.CtypesWindowsWfpMutationApi._owned_marker(None,profile,a)
        d=a['desired'];return {'filter_key':a['filter_key'],'provider_key':d['provider_key'],'sublayer_key':d['sublayer_key'],
            'layer_key':d['layer_key'],'flags':1,'filter_id':int(uuid.UUID(d['filter_key'].strip('{}'))),
            'action_type':0x1001 if d['action']=='BLOCK' else 0x1002,'action_callout_key':None,'has_provider_context':False,
            'context':{'raw_context':0},'provider_data_sha256':hashlib.sha256(marker).hexdigest(),
            'display_data':{'name':'COMSOL MCP T037 '+a['action_id'],'description':None},'weight':{'type':4,'value':d['weight']},
            'effective_weight':{'type':4,'value':d['weight']},'conditions':group.expected_conditions(d,a['grant'],b'public-synthetic-app-id'), 'security_sha256':'9'*64}
    def create_filter(self,*,profile,action,expected_dependency_sha256):
        row=self._row(profile,action)
        group.validate_readback(None,row,profile,action,wfp.CtypesWindowsWfpMutationApi._owned_marker(None,profile,action),b'public-synthetic-app-id')
        return self._create(action,self.filters,row)


def executor(tmp_path,monkeypatch,profile,plan,api=None):
    api=api or GroupSDK(profile);store=OperationStore(tmp_path/'operations.sqlite3')
    provider=_write_provider(tmp_path/'provider',profile,[_apply_grant(profile,plan)])
    authority=ProjectAuthority(store,workspace_root=tmp_path,permission_provider=lambda:{'host_control'},grant_ceiling={'host_control'},authorization_verifier=provider)
    monkeypatch.setattr(authority,'authorize_operation',lambda *a:{'project_id':profile.project_id,'workspace':profile.root.path,'policy':{'permissions':['host_control']}})
    exe=changes.WindowsHostChangeExecutor._for_software_test(store=store,authority=authority,profile=profile,api=api,token=changes._SOFTWARE_TEST_TOKEN)
    return exe,api,store,provider,authority


@pytest.mark.parametrize('zero',[False,True])
def test_complete_group_actual_executor_store_apply_reverse_restore(tmp_path,monkeypatch,zero):
    p,plan=profile_and_plan(zero_allows=zero);exe,api,store,provider,authority=executor(tmp_path,monkeypatch,p,plan)
    try:
        receipt=exe.apply(plan,approval=res.HostChangeApproval(plan.sha256,'apply-once'))
        assert len(receipt.actions)==6+len(p.windows_isolation.wfp_grants)
        job=store.db.execute("SELECT job_id,baseline_json FROM host_control_admissions WHERE phase='APPLY'").fetchone()
        baseline=json.loads(job['baseline_json']);assert baseline['mutation_sequence']['schema']==group.SEQUENCE
        assert len(_events_v2(store,job['job_id']))==2*len(receipt.actions)
        assert changes._receipt_payload_v2(plan,p,tuple(receipt.actions))['schema'].endswith('/3')
        restore_sha=_restore_sequence_sha(p,plan,receipt,api)
        provider.close();provider=_write_provider(tmp_path/'restore-provider',p,[_apply_grant(p,plan),_grant(p,'host.change.restore','restore-once',restore_sha,phase='RESTORE',source_plan=plan.sha256,source_receipt=receipt.receipt_sha256)])
        authority.authorization_verifier=provider;exe.provider=provider
        result=exe.restore(plan,receipt,authorization_ref='restore-once')
        assert result['host_change_state']=='RESTORED_UNVERIFIED' and result['effective_policy_state']=='UNVERIFIED'
        assert not api.filters and not api.sublayers and not api.providers
        creates=[x[1] for x in api.calls if x[0]=='create'];deletes=[x[1] for x in api.calls if x[0]=='delete']
        assert deletes==list(reversed(creates))
    finally:provider.close();store.close()


@pytest.mark.parametrize('mutation',['missing_default','missing_allow','duplicate_key','subject','flags','low_weight','schema','wrong_family','outside_profile','extra_field','missing_provider','fingerprint','grant_missing','unsupported_port'])
def test_whole_group_bad_targets_refuse_before_consumption_or_sdk(tmp_path,monkeypatch,mutation):
    p,plan=profile_and_plan();payload=plan.as_dict();filters=[a for a in payload['change_manifest'] if a['resource_kind']=='wfp_filter'];target=filters[0]['desired']
    if mutation=='missing_default':payload['change_manifest'].remove(filters[0])
    elif mutation=='missing_allow':payload['change_manifest'].remove(filters[-1])
    elif mutation=='duplicate_key':filters[1]['desired']['filter_key']=target['filter_key']
    elif mutation=='subject':target['principal_sid']='S-1-1-0'
    elif mutation=='flags':target['flags']=9
    elif mutation=='low_weight':filters[-1]['desired']['weight']=1
    elif mutation=='schema':target.pop('schema')
    elif mutation=='wrong_family':target['address_family']='IPV6'
    elif mutation=='outside_profile':target['filter_key']='{00000000-0000-0000-0000-000000009999}'
    elif mutation=='extra_field':target['verified']=True
    elif mutation=='missing_provider':payload['change_manifest']=payload['change_manifest'][1:]
    elif mutation=='fingerprint':target['group_sha256']='e'*64
    elif mutation=='grant_missing':filters[-1]['desired']['grant_sha256']='e'*64
    elif mutation=='unsupported_port':
        first=p.windows_isolation.wfp_grants[0];p=replace(p,windows_isolation=replace(p.windows_isolation,wfp_grants=(replace(first,local_ports=()),*p.windows_isolation.wfp_grants[1:])))
        payload['profile']=p.as_dict();payload['profile_sha256']=p.sha256
    for a in payload['change_manifest']:a['desired_identity_sha256']=res._sha256(a['desired'])
    bad=res.HostChangePlan(group.canonical(payload),group.digest(payload));exe,api,store,provider,authority=executor(tmp_path,monkeypatch,p,plan)
    monkeypatch.setattr(provider,'verify_scope',lambda *a:pytest.fail('malformed plan consumed authorization'))
    try:
        with pytest.raises(ExecutionContractError):exe.apply(bad,approval=res.HostChangeApproval(bad.sha256,'apply-once'))
        assert api.calls==[] and store.db.execute('SELECT count(*) FROM jobs').fetchone()[0]==0
    finally:provider.close();store.close()


@pytest.mark.parametrize('kind',['wfp_provider','wfp_sublayer','wfp_filter'])
def test_actual_group_partial_after_effect_stops_and_store_unknown_no_replay(tmp_path,monkeypatch,kind):
    p,plan=profile_and_plan();api=GroupSDK(p);api.fail_after=kind;exe,api,store,provider,_=executor(tmp_path,monkeypatch,p,plan,api)
    try:
        with pytest.raises(ExecutionContractError):exe.apply(plan,approval=res.HostChangeApproval(plan.sha256,'apply-once'))
        before=list(api.calls);assert store.db.execute('SELECT status FROM jobs').fetchone()[0]=='UNKNOWN'
        with pytest.raises(ExecutionContractError):exe.apply(plan,approval=res.HostChangeApproval(plan.sha256,'apply-once'))
        assert api.calls==before
    finally:provider.close();store.close()


@pytest.mark.parametrize('field',['flags','weight','effective_weight','action_type','conditions','context','provider_data_sha256'])
def test_group_readback_cannot_drop_actual_identity_condition_or_weight(field):
    p,plan=profile_and_plan();_,actions=changes._normalize_supported_actions(plan,p);api=GroupSDK(p);a=actions[-1];row=api._row(p,a)
    row[field]=[] if field=='conditions' else {} if field in {'weight','effective_weight','context'} else 9 if field in {'flags','action_type'} else '0'*64
    with pytest.raises(ExecutionContractError):group.validate_readback(None,row,p,a,wfp.CtypesWindowsWfpMutationApi._owned_marker(None,p,a),b'public-synthetic-app-id')


def test_actual_native_struct_weight_package_default_and_ipv6_full_selectors():
    p,plan=profile_and_plan();_,actions=changes._normalize_supported_actions(plan,p)
    api=wfp.CtypesWindowsWfpMutationApi.__new__(wfp.CtypesWindowsWfpMutationApi)
    for a in [a for a in actions if a['resource_kind']=='wfp_filter']:
        d=a['desired'];rows,backing=group.conditions(api,d,a['grant'],None,C.c_void_p(777))
        obj=group.make_filter(api,p.windows_isolation,a,b'public-marker',rows)
        assert obj.weight.type==4 and obj.weight.uint64.contents.value==d['weight'] and obj.flags==1
        assert obj.action.type==(0x1001 if d['action']=='BLOCK' else 0x1002)
        assert len(rows)==(1 if d['action']=='BLOCK' else 7)
        if d['action']=='PERMIT' and d['address_family']=='IPV6':
            assert rows[2].conditionValue.type==257
            address=C.cast(rows[2].conditionValue.v6AddrMask,C.POINTER(group.V6_ADDRESS)).contents
            assert bytes(address.addr).hex()=='00000000000000000000000000000001' and address.prefixLength==128


@pytest.mark.parametrize('mode',['foreign_filter','owned_filter_drift','provider_drift','sublayer_drift'])
def test_group_restore_preserves_foreign_or_changed_full_dependency_before_authorization(tmp_path,monkeypatch,mode):
    p,plan=profile_and_plan();exe,api,store,provider,_=executor(tmp_path,monkeypatch,p,plan)
    try:
        receipt=exe.apply(plan,approval=res.HostChangeApproval(plan.sha256,'apply-once'));before=list(api.calls)
        if mode=='foreign_filter':
            key='{00000000-0000-0000-0000-000000009999}';row=dict(next(iter(api.filters.values())))
            row.update(filter_key=key,filter_id=9999);api.filters[key]=row
        elif mode=='owned_filter_drift':next(iter(api.filters.values()))['weight']={'type':4,'value':987}
        elif mode=='provider_drift':next(iter(api.providers.values()))['flags']=9
        else:next(iter(api.sublayers.values()))['weight']=1
        monkeypatch.setattr(provider,'verify_scope',lambda *a:pytest.fail('changed whole-group consumed restore authorization'))
        with pytest.raises(ExecutionContractError):exe.restore(plan,receipt,authorization_ref='unused')
        assert api.calls==before and len(api.filters)>=len(p.windows_isolation.wfp_filter_keys)
        assert store.db.execute("SELECT count(*) FROM host_control_admissions WHERE phase='RESTORE'").fetchone()[0]==0
    finally:provider.close();store.close()


def test_group_receipt_cannot_be_rehashed_as_legacy_v2(tmp_path,monkeypatch):
    p,plan=profile_and_plan();exe,api,store,provider,_=executor(tmp_path,monkeypatch,p,plan)
    try:
        receipt=exe.apply(plan,approval=res.HostChangeApproval(plan.sha256,'apply-once'))
        body=changes._receipt_payload_v2(plan,p,receipt.actions);body['schema']='comsol-mcp.host-change-applied-receipt/2'
        wrong=replace(receipt,receipt_sha256=group.digest(body));before=list(api.calls)
        with pytest.raises(ExecutionContractError):exe.restore(plan,wrong,authorization_ref='unused')
        assert api.calls==before
    finally:provider.close();store.close()


@pytest.mark.parametrize('field',['boolean_weight','boolean_effective','boolean_flags','missing_condition','duplicate_condition'])
def test_actual_group_readback_rejects_malformed_sdk_types(field):
    p,plan=profile_and_plan();_,actions=changes._normalize_supported_actions(plan,p);api=GroupSDK(p);a=actions[2];row=api._row(p,a)
    if field=='boolean_weight':row['weight']={'type':4,'value':True}
    elif field=='boolean_effective':row['effective_weight']={'type':True,'value':a['desired']['weight']}
    elif field=='boolean_flags':row['flags']=True
    elif field=='missing_condition':row['conditions']=[]
    else:row['conditions'].append(row['conditions'][0])
    with pytest.raises(ExecutionContractError):group.validate_readback(None,row,p,a,wfp.CtypesWindowsWfpMutationApi._owned_marker(None,p,a),None)


def test_explicit_group_bound_4098_does_not_widen_legacy_or_allow_arbitrary_shape():
    p,plan=profile_and_plan(zero_allows=True);payload=plan.as_dict()
    assert res._host_plan_action_bound(payload['change_manifest'])==4098
    assert res._host_plan_action_bound([{'desired':{'action':'PERMIT'}}])==4096
    # Count-only boundary, no 4096 SDK effects. 4098 arbitrary resources must not
    # become an accepted sequence3 merely by entering the increased bound.
    malformed=[dict(payload['change_manifest'][0]) for _ in range(4098)]
    malformed[2]=payload['change_manifest'][2]
    with pytest.raises(ExecutionContractError):group.normalize_actions({'change_manifest':malformed},p,plan.sha256)


@pytest.mark.parametrize('mode',['missing_step','identity','kind','source_plan'])
def test_store_sequence3_reconstructs_whole_group_not_caller_boolean(tmp_path,monkeypatch,mode):
    p,plan=profile_and_plan();exe,api,store,provider,_=executor(tmp_path,monkeypatch,p,plan)
    try:
        receipt=exe.apply(plan,approval=res.HostChangeApproval(plan.sha256,'apply-once'))
        row=dict(store.db.execute('SELECT * FROM host_control_admissions').fetchone());baseline=json.loads(row['baseline_json'])
        if mode=='missing_step':baseline['mutation_sequence']['steps'].pop()
        elif mode=='identity':baseline['mutation_sequence']['steps'][2]['identity_sha256']='0'*64
        elif mode=='kind':baseline['mutation_sequence']['steps'][2]['resource_kind']='windows_service'
        else:row['plan_sha256']='0'*64
        with pytest.raises(ExecutionContractError):group.validate_journal_group(baseline,row)
    finally:provider.close();store.close()
