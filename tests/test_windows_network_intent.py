"""Network intent/real ctypes memory controls; never native policy evidence."""
from dataclasses import replace
import ctypes as C
import copy
import gc
import hashlib
import inspect
import json
import uuid

import pytest

from comsol_mcp import _w26_os_resources as res, _windows_filter_group as group
from comsol_mcp import _windows_t037_native as native, _windows_t037_wfp as wfp
from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._windows_t037 import WindowsT037ProfileV2
from tests.test_windows_host_change import _profile, _provider_sublayer_plan


def network_profile_and_plan():
    base = _profile()
    seed = base.windows_isolation.wfp_grants[0]
    images = {i.role: i.path for i in base.launch_images}
    connect = next(k for k, v in group.LAYERS.items() if v[:2] == ('OUTBOUND', 'IPV4'))
    accept = next(k for k, v in group.LAYERS.items() if v[:2] == ('INBOUND', 'IPV4'))
    common = dict(protocol=6, address_family='IPV4', local_addresses=('127.0.0.1/32',), remote_addresses=('127.0.0.1/32',))
    grants = (
        replace(seed, **common, role='worker', application_path=images['worker'], layer_key=connect,
                local_ports=(), remote_ports=(32001,), local_port_range=(40000, 45000)),
        replace(seed, **common, role='server', application_path=images['server'], direction='INBOUND',
                layer='ALE_AUTH_RECV_ACCEPT', layer_key=accept, local_ports=(32001,), remote_ports=(), remote_port_range=(40000, 45000)),
        replace(seed, **common, role='worker', application_path=images['worker'], direction='INBOUND',
                layer='ALE_AUTH_RECV_ACCEPT', layer_key=accept, local_ports=(32002,), remote_ports=(), remote_port_range=(40000, 45000)),
    )
    keys = tuple('{' + str(uuid.UUID(int=5000+i)).upper() + '}' for i in range(7))
    profile = replace(base, windows_isolation=replace(base.windows_isolation, wfp_grants=grants, wfp_filter_keys=keys))
    parents = res.validate_host_change_plan(_provider_sublayer_plan(profile))['change_manifest']
    members = []
    for index, (key, (direction, family, _)) in enumerate(group.LAYERS.items()):
        members.append(dict(schema=group.SCHEMA, group_id='{00000000-0000-0000-0000-000000009999}',
            group_sha256='0'*64, filter_key=keys[index], provider_key=base.windows_isolation.wfp_provider_key,
            sublayer_key=base.windows_isolation.wfp_sublayer_key, action='BLOCK', weight=10, flags=1,
            layer_key=key, direction=direction, address_family=family, principal_sid=base.windows_isolation.appcontainer_sid, grant_sha256=None))
    for index, grant in enumerate(grants):
        members.append({**members[0], 'filter_key': keys[4+index], 'action': 'PERMIT', 'weight': 20,
                        'layer_key': grant.layer_key, 'direction': grant.direction, 'address_family': grant.address_family,
                        'grant_sha256': group.digest(grant.as_dict())})
    fingerprint = group.group_fingerprint(profile, members)
    for m in members:
        m['group_sha256'] = fingerprint
    actions = [{k: a[k] for k in ('action_id', 'resource_kind', 'resource_name', 'operation', 'desired')} for a in parents]
    actions += [dict(action_id='filter-'+str(i), resource_kind='wfp_filter', resource_name='filter-'+str(i), operation='CREATE', desired=m) for i, m in enumerate(members)]
    for a in actions:
        a.update(baseline_identity=None, baseline_sha256=None, backup_path=None, backup_sha256=None)
    return profile, res.build_host_change_plan(profile, change_manifest=actions, operator_inputs={})


def test_range_roundtrip_complete_four_default_group():
    profile, plan = network_profile_and_plan()
    restored = res.parse_host_resource_profile(profile.as_dict())
    assert restored.as_dict() == profile.as_dict() and restored.sha256 == profile.sha256
    actions = group.normalize_actions(plan.as_dict(), profile, plan.sha256)
    filters = [a for a in actions if a['resource_kind'] == 'wfp_filter']
    assert len(actions) == 9 and len(filters) == 7
    assert sum(a['desired']['action'] == 'BLOCK' for a in filters) == 4
    assert all(a['desired']['address_family'] == 'IPV4' for a in filters if a['desired']['action'] == 'PERMIT')


def test_session_network_intent_has_fixed_listener_values():
    from comsol_mcp._windows_t037_network import derive_session_network_intent
    profile, _ = network_profile_and_plan()
    wrapper = WindowsT037ProfileV2(profile, 'd'*64, {}, 'SOFTWARE_INTENT_ONLY')
    intent = derive_session_network_intent(wrapper)
    assert (intent.server_listen_port, intent.worker_listen_port, intent.bind_address) == (32001, 32002, '127.0.0.1')
    assert intent.source_port_range == (40000, 45000)
    assert intent.verification_state == 'NOT_VERIFIED'


def test_range_ctypes_encoding_decoding_retains_original_backing():
    profile, plan = network_profile_and_plan()
    actions = group.normalize_actions(plan.as_dict(), profile, plan.sha256)
    api = wfp.CtypesWindowsWfpMutationApi.__new__(wfp.CtypesWindowsWfpMutationApi)
    reader = native.CtypesWindowsPolicyApi.__new__(native.CtypesWindowsPolicyApi)
    for a in actions:
        if a['resource_kind'] != 'wfp_filter' or a['desired']['action'] != 'PERMIT':
            continue
        rows, backing = group.conditions(api, a['desired'], a['grant'], None, C.c_void_p(777))
        obj = group.make_filter(api, profile.windows_isolation, a, b'software-marker', rows)
        ranges = [r for r in rows if r.conditionValue.type == 258]
        assert len(ranges) == 1 and ranges[0].matchType == 5
        address = ranges[0].conditionValue.rangeValue
        del backing
        gc.collect()
        value = reader._decode_value(ranges[0].conditionValue)
        assert value == {'type': 258, 'low': {'type': 2, 'value': 40000}, 'high': {'type': 2, 'value': 45000}}
        assert obj._keepalive and address


@pytest.mark.parametrize('field,value', [
    ('local_port_range', [40000]), ('local_port_range', None),
    ('local_port_range', [45000,40000]), ('local_port_range', [0,40000]),
    ('local_port_range', [40000,65536]), ('local_port_range', [True,45000]),
    ('local_port_range', [1,65535]), ('remote_port_range', [40000,45000]),
    ('unexpected_range', [40000,45000]),
])
def test_closed_grant_rejects_bad_ranges_and_destination_ranges(field,value):
    profile,_=network_profile_and_plan();document=profile.as_dict();grant=document['windows_isolation']['wfp_grants'][0]
    grant[field]=value
    with pytest.raises(ValueError):res.parse_host_resource_profile(document)


@pytest.mark.parametrize('fault',['list_and_range','UDP','IPv6','broad_local','broad_remote','not_loopback','missing_destination','destination_port0','wrong_image','wrong_sid'])
def test_source_range_keeps_exact_peer_role_image_and_destination_contract(fault):
    profile,_=network_profile_and_plan();document=profile.as_dict();g=document['windows_isolation']['wfp_grants'][0]
    if fault=='list_and_range':g['local_ports']=[40001]
    elif fault=='UDP':g['protocol']=17
    elif fault=='IPv6':g.update(address_family='IPV6',local_addresses=['::1/128'],remote_addresses=['::1/128'])
    elif fault=='broad_local':g['local_addresses']=['127.0.0.0/8']
    elif fault=='broad_remote':g['remote_addresses']=['0.0.0.0/0']
    elif fault=='not_loopback':g['remote_addresses']=['192.0.2.1/32']
    elif fault=='missing_destination':g['remote_ports']=[]
    elif fault=='destination_port0':g['remote_ports']=[0]
    elif fault=='wrong_image':g['application_path']=next(i['path'] for i in document['launch_images'] if i['role']=='server')
    else:g['principal_sid']='S-1-15-2-999'
    with pytest.raises(ValueError):res.parse_host_resource_profile(document)


def test_old_canonical_hash_discrete_port_lists_and_legacy_leaf_unchanged():
    original=_profile()
    assert original.sha256=='ce3b4ab1d20d1089549b3eb1bf13961f2450789ff2563e9a6677a3a939a840f8'
    assert 'local_port_range' not in original.windows_isolation.wfp_grants[0].as_dict()
    discrete=replace(original.windows_isolation.wfp_grants[0],remote_ports=(32001,32002))
    p=replace(original,windows_isolation=replace(original.windows_isolation,wfp_grants=(discrete,)))
    assert p.as_dict()['windows_isolation']['wfp_grants'][0]['remote_ports']==[32001,32002]
    with pytest.raises(ExecutionContractError):group.normalize_grant(discrete,p)
    ranged,_=network_profile_and_plan()
    with pytest.raises(ExecutionContractError) as error:
        wfp.normalize_endpoint_grant(ranged.windows_isolation.wfp_grants[0],policy=ranged.windows_isolation,launch_images=ranged.launch_images)
    assert error.value.code=='WFP_GRANT_UNSUPPORTED'


@pytest.mark.parametrize('fault',['missing_flow','duplicate_flow','different_source_range','different_server_port','same_listeners','exact_conflict','plain_dict'])
def test_derived_intent_refuses_incomplete_conflicting_or_untyped_input(fault):
    from comsol_mcp._windows_t037_network import derive_session_network_intent
    p,_=network_profile_and_plan();grants=list(p.windows_isolation.wfp_grants)
    if fault=='missing_flow':grants.pop()
    elif fault=='duplicate_flow':grants.append(replace(grants[0],remote_ports=(32003,)))
    elif fault=='different_source_range':grants[1]=replace(grants[1],remote_port_range=(41000,45000))
    elif fault=='different_server_port':grants[1]=replace(grants[1],local_ports=(32003,))
    elif fault=='same_listeners':grants[2]=replace(grants[2],local_ports=(32001,))
    elif fault=='exact_conflict':grants.append(replace(grants[0],local_port_range=None,local_ports=(40500,)))
    p=replace(p,windows_isolation=replace(p.windows_isolation,wfp_grants=tuple(grants)))
    wrapped=WindowsT037ProfileV2(p,'d'*64,{},'SOFTWARE_ONLY')
    with pytest.raises(ExecutionContractError):derive_session_network_intent(p.as_dict() if fault=='plain_dict' else wrapped)


def test_existing_explicit_exact_license_grant_is_retained_in_profile_binding():
    from comsol_mcp._windows_t037_network import derive_session_network_intent
    p,_=network_profile_and_plan();g=p.windows_isolation.wfp_grants[0]
    license_grant=replace(g,local_port_range=None,local_ports=(41000,),remote_addresses=('192.0.2.123/32',),remote_ports=(27001,))
    p=replace(p,windows_isolation=replace(p.windows_isolation,wfp_grants=(*p.windows_isolation.wfp_grants,license_grant)))
    wrapper=WindowsT037ProfileV2(p,'d'*64,{},'SOFTWARE_ONLY');intent=derive_session_network_intent(wrapper)
    assert intent.profile_sha256==wrapper.sha256 and len(p.windows_isolation.wfp_grants)==4


@pytest.mark.parametrize('mutation',['missing_v4_connect','missing_v6_connect','missing_v4_accept','missing_v6_accept'])
def test_range_group_cannot_drop_any_default_layer(mutation):
    p,plan=network_profile_and_plan();payload=plan.as_dict();index=['missing_v4_connect','missing_v6_connect','missing_v4_accept','missing_v6_accept'].index(mutation)
    filters=[a for a in payload['change_manifest'] if a['resource_kind']=='wfp_filter'];payload['change_manifest'].remove(filters[index])
    with pytest.raises(ExecutionContractError):group.normalize_actions(payload,p,plan.sha256)


@pytest.mark.parametrize('fault',['foreign_pointer','null_pointer','missing_backing','wrong_match','changed_type','changed_low_type','changed_bounds','wrong_field'])
def test_range_filter_refuses_foreign_or_unheld_native_pointer_before_dereference(fault):
    p,plan=network_profile_and_plan();a=next(a for a in group.normalize_actions(plan.as_dict(),p,plan.sha256) if a.get('grant',{}).get('local_port_range'))
    api=wfp.CtypesWindowsWfpMutationApi.__new__(wfp.CtypesWindowsWfpMutationApi)
    rows,backing=group.conditions(api,a['desired'],a['grant'],None,C.c_void_p(777));r=next(r for r in rows if r.conditionValue.type==258)
    if fault=='foreign_pointer':r.conditionValue.rangeValue=1
    elif fault=='null_pointer':r.conditionValue.rangeValue=None
    elif fault=='missing_backing':rows._group_backing=()
    elif fault=='wrong_match':r.matchType=0
    elif fault=='changed_type':r.conditionValue.type=2
    elif fault=='changed_low_type':next(v for v in backing if type(v) is native.FWPM_RANGE0).valueLow.type=3
    elif fault=='changed_bounds':next(v for v in backing if type(v) is native.FWPM_RANGE0).valueHigh.uint16=45001
    else:r.fieldKey=wfp.guid_struct(wfp.FWPM_CONDITION_IP_REMOTE_PORT)
    with pytest.raises(ExecutionContractError):group.make_filter(api,p.windows_isolation,a,b'software-marker',rows)


@pytest.mark.parametrize('fault',['range_match','range_kind','low_kind','high_bound','low_bool','inverted','extra_condition','extra_low_field','wrong_field','context_bool'])
def test_exact_readback_compares_range_operator_type_bounds_and_closed_fields(fault):
    from tests.test_windows_filter_group import GroupSDK
    p,plan=network_profile_and_plan();a=next(a for a in group.normalize_actions(plan.as_dict(),p,plan.sha256) if a.get('grant',{}).get('local_port_range'))
    row=GroupSDK(p)._row(p,a);condition=next(r for r in row['conditions'] if r['condition_value']['type']==258)
    value=condition['condition_value']
    if fault=='range_match':condition['match_type']=0
    elif fault=='range_kind':condition['condition_value']={'type':2,'value':40000}
    elif fault=='low_kind':value['low']['type']=3
    elif fault=='high_bound':value['high']['value']=45001
    elif fault=='low_bool':value['low']['value']=True
    elif fault=='inverted':value['low']['value']=50000
    elif fault=='extra_condition':condition['verified']=True
    elif fault=='extra_low_field':value['low']['proven']=True
    elif fault=='wrong_field':condition['field_key']=wfp.FWPM_CONDITION_IP_REMOTE_PORT
    else:row['context']['raw_context']=False
    with pytest.raises(ExecutionContractError):group.validate_readback(None,row,p,a,wfp.CtypesWindowsWfpMutationApi._owned_marker(None,p,a),b'public-synthetic-app-id')


def effective_group_fixture():
    from tests.test_windows_filter_group import GroupSDK
    p,plan=network_profile_and_plan();actions=group.normalize_actions(plan.as_dict(),p,plan.sha256);sdk=GroupSDK(p)
    filters=[sdk._row(p,a) for a in actions if a['resource_kind']=='wfp_filter']
    layers=[]
    for key in group.LAYERS:
        fields={}
        for row in filters:
            if row['layer_key']!=key:continue
            for c in row['conditions']:
                kind=c['condition_value']['type'];fields[c['field_key']]={'field_key':c['field_key'],'field_type':1 if kind in (256,257) else 0,'data_type':3 if kind==256 else 11 if kind==257 else 2 if kind==258 else kind}
        layers.append(dict(layer_key=key,display_data={'name':None,'description':None},flags=3,fields=list(fields.values()),default_sublayer_key=p.windows_isolation.wfp_sublayer_key,layer_id=len(layers)+1))
    snapshot=dict(schema='comsol-mcp.wfp-five-catalog-readonly/1',state='COMPLETE_READ_ONLY',counts={},limits={},snapshot_sha256='',reasons=[],providers=[],sublayers=[],filters=filters,provider_contexts=[],layers=layers)
    class Api:
        def observe_wfp_policy_snapshot(self):
            content={k:snapshot[k] for k in ('providers','sublayers','filters','provider_contexts','layers')}
            content['filters']=sorted(content['filters'],key=lambda r:(r['layer_key'],r['filter_id']))
            content['layers']=sorted(content['layers'],key=lambda r:r['layer_key'])
            raw=native._canonical(content);snapshot['snapshot_sha256']=hashlib.sha256(raw).hexdigest()
            counts={k:len(snapshot[k]) for k in content};counts.update(conditions=sum(len(r['conditions']) for r in filters),pages=5,pages_by_kind={k:1 for k in ('Provider','SubLayer','Filter','ProviderContext','Layer')},total_records=sum(len(v) for v in content.values()),total_bytes=len(raw),elapsed_ms=1)
            snapshot['counts']=counts;snapshot['limits']={'max_page_size':native.MAX_ENUM_PAGE,'max_pages_per_kind':native.MAX_ENUM_PAGES,'max_total_pages':native.MAX_ENUM_PAGES*5,'max_total_records':native.MAX_ENUM_ROWS,'max_total_bytes':native.MAX_ENUM_TOTAL_BYTES,'max_elapsed_ms':native.MAX_ENUM_SECONDS*1000}
            return snapshot
        def observe_application_id(self,path):
            blob=b'public-synthetic-app-id'
            return {'image_path':path.lower(),'type':12,'size':len(blob),'sha256':hashlib.sha256(blob).hexdigest()}
    return WindowsT037ProfileV2(p,'d'*64,{},'SOFTWARE_ONLY'),Api(),snapshot


def test_full_group_observation_matches_four_blocks_three_ranges_but_never_mints_proof():
    from comsol_mcp._windows_t037_effective import observe_effective_wfp
    p,api,snapshot=effective_group_fixture();result=observe_effective_wfp(p,api)
    assert len(result.matched_filter_keys)==7
    assert result.state=='UNVERIFIED'
    assert {'WFP_ENUMERATION_ACCESS_VISIBILITY_NOT_PROVEN','WFP_DEFAULT_BLOCK_NOT_PROVEN','WFP_EFFECTIVE_ARBITRATION_NOT_PROVEN'}<=set(result.reasons)
    assert not any(r.startswith('WFP_GROUP_') for r in result.reasons)


@pytest.mark.parametrize('fault',['missing_default','range_match','low_kind','bounds','bool_bound','extra_value_field','layer_range_kind','layer_bool','extra_filter_field','BLOCK_with_extra_condition','SID','app_id','weight','provider'])
def test_group_observation_rejects_mismatches_and_keeps_native_gates(fault):
    from comsol_mcp._windows_t037_effective import observe_effective_wfp
    p,api,s=effective_group_fixture();permit=next(r for r in s['filters'] if r['action_type']==0x1002);condition=next(c for c in permit['conditions'] if c['condition_value']['type']==258)
    if fault=='missing_default':s['filters'].remove(next(r for r in s['filters'] if r['action_type']==0x1001))
    elif fault=='range_match':condition['match_type']=0
    elif fault=='low_kind':condition['condition_value']['low']['type']=3
    elif fault=='bounds':condition['condition_value']['high']['value']=45001
    elif fault=='bool_bound':condition['condition_value']['low']['value']=True
    elif fault=='extra_value_field':condition['condition_value']['high']['native']=True
    elif fault in ('layer_range_kind','layer_bool'):
        layer=next(l for l in s['layers'] if l['layer_key']==permit['layer_key']);field=next(f for f in layer['fields'] if f['field_key']==condition['field_key']);field['data_type']=258 if fault=='layer_range_kind' else True
    elif fault=='extra_filter_field':permit['approval']='VERIFIED'
    elif fault=='BLOCK_with_extra_condition':next(r for r in s['filters'] if r['action_type']==0x1001)['conditions'].append(copy.deepcopy(condition))
    elif fault=='SID':next(c for c in permit['conditions'] if c['condition_value']['type']==13)['condition_value']['value']='S-1-15-2-999'
    elif fault=='app_id':next(c for c in permit['conditions'] if c['condition_value']['type']==12)['condition_value']['sha256']='0'*64
    elif fault=='weight':permit['weight']['value']=5;permit['effective_weight']['value']=5
    else:permit['provider_key']='{00000000-0000-0000-0000-000000000099}'
    if fault in ('layer_bool','extra_filter_field'):
        with pytest.raises(ExecutionContractError):observe_effective_wfp(p,api)
    else:
        result=observe_effective_wfp(p,api)
        assert result.state=='UNVERIFIED'
        if fault=='weight':assert 'WFP_GROUP_ALLOW_WEIGHT_NOT_ABOVE_DEFAULT' in result.reasons
        else:assert len(result.matched_filter_keys)<7
        assert 'WFP_DEFAULT_BLOCK_NOT_PROVEN' in result.reasons


@pytest.mark.parametrize('text,expected', [
    ('Protocol tcp Dynamic Port Range\n----------------\nStart Port : 40000\nNumber of Ports : 5001\n',(40000,45000)),
    ('\r\n协议 tcp 动态端口范围\r\n----------------\r\n启动端口 : 1024\r\n端口数 : 58977\r\n',(1024,60000)),
])
def test_strict_readonly_range_parser_supports_known_output_without_default_guess(text,expected):
    from comsol_mcp._windows_t037_network import parse_ipv4_tcp_dynamic_range
    assert parse_ipv4_tcp_dynamic_range(text)==expected


@pytest.mark.parametrize('fault',['unknown_header','duplicate','extra_line','zero_count','overflow','full_range','missing_start','bool_text'])
def test_dynamic_range_parser_refuses_bad_or_ambiguous_output(fault):
    from comsol_mcp._windows_t037_network import parse_ipv4_tcp_dynamic_range
    text='Protocol tcp Dynamic Port Range\n----------------\nStart Port : 40000\nNumber of Ports : 5001\n'
    if fault=='unknown_header':text=text.replace('tcp','udp')
    elif fault=='duplicate':text+=text
    elif fault=='extra_line':text+='Start Port : 40001\n'
    elif fault=='zero_count':text=text.replace('5001','0')
    elif fault=='overflow':text=text.replace('5001','65535')
    elif fault=='full_range':text=text.replace('40000','1').replace('5001','65535')
    elif fault=='missing_start':text=text.replace('Start Port : 40000','')
    else:text=text.replace('40000','True')
    with pytest.raises(ValueError):parse_ipv4_tcp_dynamic_range(text)


def test_fresh_dynamic_range_revalidation_refuses_drift_or_untyped_intent(monkeypatch):
    from comsol_mcp import _windows_t037_network as net
    p,_=network_profile_and_plan();intent=net.derive_session_network_intent(WindowsT037ProfileV2(p,'d'*64,{},'SOFTWARE_ONLY'))
    calls=[]
    def observe():
        calls.append('one-read');return net.DynamicTcpRangeObservation((40000,45001),'protected-system32', 'a'*64,'b'*64)
    monkeypatch.setattr(net,'read_windows_ipv4_tcp_dynamic_range',observe)
    with pytest.raises(ExecutionContractError):net.revalidate_source_port_range(intent)
    assert calls==['one-read']
    with pytest.raises(ExecutionContractError):net.revalidate_source_port_range(intent.as_dict())
    assert calls==['one-read']


@pytest.mark.parametrize('fault',[None,'wrong_system_path','reparse','untrusted_owner','untrusted_write','query_error','ambiguous_output','identity_drift'])
def test_protected_getter_software_controls_use_no_caller_executable_and_close_original_handles(monkeypatch,fault):
    """Software SDK double; not the real Windows observation."""
    from comsol_mcp import _windows_t037_network as net
    state=transferred_getter_sdk(monkeypatch,fault)
    if fault is None:
        observed=net.read_windows_ipv4_tcp_dynamic_range()
        assert observed.source_port_range==(40000,45000) and observed.verification_state=='NOT_VERIFIED'
    else:
        with pytest.raises(ExecutionContractError):net.read_windows_ipv4_tcp_dynamic_range()
    assert state.closed==list(reversed([value for value,path in state.created]))
    assert len(state.queries)==(1 if fault in (None,'query_error','ambiguous_output','identity_drift') else 0)
    assert net._dynamic_range_owner is None and not state.cleanup_owners


@pytest.mark.parametrize('bounds',[(1,1),(65535,65535),(40000,45000)])
def test_uint16_source_range_inclusive_boundaries_are_not_discrete_lists(bounds):
    from comsol_mcp._windows_t037_network import validate_port_range
    assert validate_port_range(bounds)==bounds


@pytest.mark.parametrize('fault',['OUTBOUND_destination_range','INBOUND_destination_range','LISTEN','BIND'])
def test_range_is_never_a_destination_or_resource_assignment_selector(fault):
    p,_=network_profile_and_plan();doc=p.as_dict();g=doc['windows_isolation']['wfp_grants'][0]
    if fault=='OUTBOUND_destination_range':g.update(local_ports=[32001],local_port_range=None,remote_ports=[],remote_port_range=[40000,45000]);g.pop('local_port_range')
    elif fault=='INBOUND_destination_range':g.update(direction='INBOUND',layer='ALE_AUTH_RECV_ACCEPT',local_ports=[],remote_ports=[32001])
    else:g.update(direction=fault,layer='ALE_AUTH_LISTEN' if fault=='LISTEN' else 'ALE_RESOURCE_ASSIGNMENT')
    with pytest.raises(ValueError):res.parse_host_resource_profile(doc)


def transferred_getter_sdk(monkeypatch, fault=None):
    """Software-only real-helper boundary; integer HANDLEs and Detach semantics."""
    import sys
    import weakref
    import subprocess
    from types import SimpleNamespace as NS
    from comsol_mcp import _windows_t037_network as net
    state=NS(created=[], wrappers=[], detached=[], closed=[], queries=[], fail_close=fault=='close_failure',
             close_result=1, close_fault=None, close_effects=[], reads=0, fault=fault, cleanup_owners=[])
    class Handle:
        def __init__(self,path,value):self.path=path;self.value=value;self.original=value
        def __int__(self):
            if state.fault=='int_failure':raise MemoryError('original int conversion failed')
            return self.value
        def Detach(self):
            state.detached.append(self.original)
            if state.fault=='detach_before':raise OSError('Detach failed before transfer')
            result=self.value;self.value=0
            if state.fault=='detach_after':raise MemoryError('Detach return allocation failed')
            if state.fault=='detach_mismatch':return result+1
            if state.fault=='detach_bool':return True
            return result
        def Close(self):raise AssertionError('PyHANDLE.Close must never replace the captured SDK closer')
        def __del__(self):
            if self.value:state.cleanup_owners.append(self.original)
    class SD:
        def GetSecurityDescriptorOwner(self):return 'untrusted' if fault=='untrusted_owner' else 'S-1-5-18'
        def GetSecurityDescriptorDacl(self):return self
        def GetAceCount(self):return 1
        def GetAce(self,index):return ((0,0),0x1200A9 | (2 if fault=='untrusted_write' else 0),'S-1-1-0')
    def create(path,*args):
        if state.fault=='create_raises':raise OSError('CreateFile returned no resource')
        if state.fault=='sdk_none':return None
        if state.fault=='sdk_integer':return 900
        h=Handle(path,900+len(state.created));state.created.append((h.original,path));state.wrappers.append(weakref.ref(h));return h
    def info(value):
        assert type(value) is int and value in dict(state.created)
        path=dict(state.created)[value]
        attrs=0 if path.endswith('netsh.exe') else 16
        if fault=='reparse' and path.endswith('netsh.exe'):attrs|=0x400
        return (attrs,None,None,None,1,0,3,1,0,value+(1 if fault=='identity_drift' and state.queries else 0))
    def close(value):
        assert type(value) is int
        state.closed.append(value)
        if state.close_fault=='effect_then_raise':
            state.close_effects.append(value)
            raise OSError('native close effect completed but Python return was lost')
        if state.close_fault=='before_call_raise':raise OSError('no reliable SDK return')
        if state.fail_close and value==903:return 0
        return state.close_result
    def read(value,size):state.reads+=1;return 0,b'exe' if state.reads==1 else b''
    def run(argv,**kwargs):
        state.queries.append(argv)
        assert argv==[r'C:\Windows\System32\netsh.exe','int','ipv4','show','dynamicport','tcp']
        assert kwargs['shell'] is False and kwargs['env']['PATH']==r'C:\Windows\System32'
        output=b'Protocol tcp Dynamic Port Range\n----------------\nStart Port : 40000\nNumber of Ports : 5001\n'
        if fault=='ambiguous_output':output+=output
        return NS(returncode=1 if fault=='query_error' else 0,stdout=output,stderr=b'')
    close_function=close
    state.kernel=NS(CloseHandle=close_function,GetOEMCP=lambda:437)
    modules={'os':NS(name='nt'),'win32api':NS(GetWindowsDirectory=lambda:r'C:\Windows',GetSystemDirectory=lambda:r'C:\attacker' if fault=='wrong_system_path' else r'C:\Windows\System32'),
             'win32con':NS(FILE_SHARE_READ=1,FILE_SHARE_WRITE=2,OPEN_EXISTING=3),
             'win32file':NS(CreateFile=create,GetFileInformationByHandle=info,ReadFile=read),
             'win32security':NS(LookupAccountName=lambda *a:('installer',None,None),ConvertSidToStringSid=lambda v:v,
                                GetSecurityInfo=lambda *a:SD(),ConvertSecurityDescriptorToStringSecurityDescriptor=lambda *a:'same-SDDL')}
    for name,module in modules.items():monkeypatch.setitem(sys.modules,name,module)
    monkeypatch.setattr(C,'WinDLL',lambda *a,**kw:state.kernel,raising=False)
    from tests.windows_netsh_query_fake_sdk import install
    install(monkeypatch,net,state,fault)
    monkeypatch.setattr(net,'_dynamic_range_owner',None,raising=False)
    state.original_close=state.kernel.CloseHandle
    return state


def test_getter_close_failure_retains_original_integer_owner_and_refuses_requery(monkeypatch):
    import weakref
    from comsol_mcp import _windows_t037_network as net, _windows_owned_resources as owned
    s=transferred_getter_sdk(monkeypatch,'close_failure')
    with pytest.raises(owned.NativeResourceError):net.read_windows_ipv4_tcp_dynamic_range()
    owner=net._dynamic_range_owner;reference=weakref.ref(owner)
    entry,=owner.registry.unresolved();holder=entry.keepalive[1]
    assert entry.state=='UNKNOWN' and entry.handle==entry.value==903
    assert holder.value==903 and holder.returned is s.wrappers[-1]() and holder.close_function is s.original_close
    assert s.closed==[903,902,901,900] and len(s.queries)==1
    del owner;gc.collect()
    assert reference() is net._dynamic_range_owner and s.wrappers[-1]() is not None and not s.cleanup_owners
    with pytest.raises(owned.NativeResourceError):net.read_windows_ipv4_tcp_dynamic_range()
    assert len(s.created)==4 and len(s.queries)==1 and s.closed==[903,902,901,900]
    with pytest.raises(owned.NativeResourceError):reference().recover_owned(object())
    assert s.closed==[903,902,901,900]
    s.kernel.CloseHandle=lambda value:pytest.fail('replacement SDK closer used')
    s.fail_close=False
    reference().recover_owned(reference())
    assert s.closed==[903,902,901,900,903] and not entry.registry.unresolved() and net._dynamic_range_owner is None
    assert entry.state=='RELEASED' and not s.cleanup_owners
    with pytest.raises(owned.NativeResourceError):reference().recover_owned(reference())


@pytest.mark.parametrize('fault',['int_failure','detach_before','detach_after','detach_mismatch','detach_bool'])
def test_getter_uncertain_transfer_retains_sdk_return_and_never_redetaches_or_closes(monkeypatch,fault):
    from comsol_mcp import _windows_t037_network as net, _windows_owned_resources as owned
    s=transferred_getter_sdk(monkeypatch,fault)
    with pytest.raises(owned.NativeResourceError):net.read_windows_ipv4_tcp_dynamic_range()
    owner=net._dynamic_range_owner;entry,=owner.registry.unresolved()
    assert entry.state=='UNKNOWN' and entry.keepalive[0] is s.wrappers[0]()
    assert owner.pending_return is entry.keepalive[0] and not s.closed and not s.queries
    if fault=='int_failure':assert entry.value is None and not s.detached
    else:assert entry.handle==entry.value==900 and s.detached==[900]
    detached=list(s.detached)
    gc.collect();assert s.wrappers[0]() is not None and not s.cleanup_owners
    with pytest.raises(owned.NativeResourceError):net.read_windows_ipv4_tcp_dynamic_range()
    with pytest.raises(owned.NativeResourceError):owner.recover_owned(owner)
    with pytest.raises(owned.NativeResourceError):owner.registry.retry_owned(entry,object())
    assert s.detached==detached and not s.closed and not s.queries and len(s.created)==1
    assert net._dynamic_range_owner is owner


@pytest.mark.parametrize('returned',[None,False,True,1.0,'success',1<<32,-(1<<31)-1,
                                    'effect_then_raise','before_call_raise'])
def test_getter_indeterminate_close_never_retries_or_requeries_even_after_closer_replacement(monkeypatch,returned):
    from types import SimpleNamespace
    from comsol_mcp import _windows_t037_network as net, _windows_owned_resources as owned
    s=transferred_getter_sdk(monkeypatch)
    if returned in ('effect_then_raise','before_call_raise'):s.close_fault=returned
    else:s.close_result=returned
    with pytest.raises(owned.NativeResourceError):net.read_windows_ipv4_tcp_dynamic_range()
    owner=net._dynamic_range_owner;entries=owner.registry.unresolved()
    assert len(entries)==4 and all(e.state=='UNKNOWN' for e in entries)
    assert s.closed==[903,902,901,900] and len(s.queries)==1
    holders=[e.keepalive[1] for e in entries]
    assert all(h.close_function is s.original_close and h.returned is e.keepalive[0]
               and h.value==h.detached_return==e.value==e.handle for h,e in zip(holders,entries))
    assert s.close_effects==([903,902,901,900] if returned=='effect_then_raise' else [])
    fake=SimpleNamespace(registry=owner.registry,state='UNKNOWN')
    with pytest.raises(owned.NativeResourceError):owner.recover_owned(fake)
    with pytest.raises(owned.NativeResourceError):owner.registry.retry_owned(dict(entries[0].__dict__),owner)
    with pytest.raises(owned.NativeResourceError):net.read_windows_ipv4_tcp_dynamic_range()
    assert s.closed==[903,902,901,900] and len(s.created)==4 and len(s.queries)==1
    s.close_result=1;s.close_fault=None
    s.kernel.CloseHandle=lambda value:pytest.fail('replacement API closer used')
    with pytest.raises(owned.NativeResourceError):owner.recover_owned(owner)
    for entry,holder in zip(entries,holders):
        with pytest.raises(owned.NativeResourceError):owner.registry.retry_owned(entry,owner)
        with pytest.raises(owned.NativeResourceError):holder.close(entry.value)
    owner.registry.drain(owner.registry.scope())
    with pytest.raises(owned.NativeResourceError):net.read_windows_ipv4_tcp_dynamic_range()
    gc.collect()
    assert all(ref() is not None for ref in s.wrappers) and not s.cleanup_owners
    assert s.closed==[903,902,901,900] and len(s.created)==4 and len(s.queries)==1
    assert net._dynamic_range_owner is owner and all(e.state=='UNKNOWN' for e in entries)
    assert all(h.state=='CLOSE_UNKNOWN' for h in holders)


@pytest.mark.parametrize('fault',['effect_then_raise','before_call_raise',None,False,True,'bad'])
def test_getter_known_failure_permission_is_consumed_before_indeterminate_retry(monkeypatch,fault):
    from comsol_mcp import _windows_t037_network as net, _windows_owned_resources as owned
    s=transferred_getter_sdk(monkeypatch,'close_failure')
    with pytest.raises(owned.NativeResourceError):net.read_windows_ipv4_tcp_dynamic_range()
    owner=net._dynamic_range_owner;entry,=owner.registry.unresolved()
    s.fail_close=False
    if fault in ('effect_then_raise','before_call_raise'):s.close_fault=fault
    else:s.close_result=fault
    with pytest.raises(owned.NativeResourceError):owner.recover_owned(owner)
    s.close_fault=None;s.close_result=1
    with pytest.raises(owned.NativeResourceError):owner.recover_owned(owner)
    with pytest.raises(owned.NativeResourceError):owner.registry.retry_owned(entry,owner)
    with pytest.raises(owned.NativeResourceError):net.read_windows_ipv4_tcp_dynamic_range()
    assert s.closed==[903,902,901,900,903] and len(s.created)==4 and len(s.queries)==1
    assert entry.state=='UNKNOWN' and net._dynamic_range_owner is owner and not s.cleanup_owners


@pytest.mark.parametrize('fault',['detached','value','entry','closer'])
def test_getter_known_failure_cannot_recover_changed_original_binding(monkeypatch,fault):
    from comsol_mcp import _windows_t037_network as net, _windows_owned_resources as owned
    s=transferred_getter_sdk(monkeypatch,'close_failure')
    with pytest.raises(owned.NativeResourceError):net.read_windows_ipv4_tcp_dynamic_range()
    owner=net._dynamic_range_owner;entry,=owner.registry.unresolved();holder=entry.keepalive[1]
    if fault=='detached':holder.detached_return+=1
    elif fault=='value':holder.value+=1
    elif fault=='entry':entry.handle+=1
    else:holder.close_function=lambda value:pytest.fail('replacement holder closer used')
    s.fail_close=False
    with pytest.raises(owned.NativeResourceError):owner.recover_owned(owner)
    with pytest.raises(owned.NativeResourceError):owner.registry.retry_owned(entry,owner)
    assert s.closed==[903,902,901,900] and len(s.queries)==1 and entry.state=='UNKNOWN'
    assert net._dynamic_range_owner is owner and not s.cleanup_owners


@pytest.mark.parametrize('returned',[-(1<<31),-1,1,(1<<31)-1])
def test_getter_strict_signed_win32_bool_success_boundary_preserves_normal_cleanup(monkeypatch,returned):
    from comsol_mcp import _windows_t037_network as net
    s=transferred_getter_sdk(monkeypatch);s.close_result=returned
    assert net.read_windows_ipv4_tcp_dynamic_range().verification_state=='NOT_VERIFIED'
    assert s.closed==[903,902,901,900] and len(s.queries)==1 and net._dynamic_range_owner is None


def test_getter_no_sdk_return_does_not_leave_false_owner_or_attempt_close(monkeypatch):
    from comsol_mcp import _windows_t037_network as net
    s=transferred_getter_sdk(monkeypatch,'create_raises')
    with pytest.raises(OSError,match='no resource'):net.read_windows_ipv4_tcp_dynamic_range()
    assert net._dynamic_range_owner is None and not s.created and not s.closed and not s.queries


@pytest.mark.parametrize('fault',['sdk_none','sdk_integer'])
def test_getter_invalid_sdk_return_is_retained_without_fabricated_wrapper_or_close(monkeypatch,fault):
    from comsol_mcp import _windows_t037_network as net, _windows_owned_resources as owned
    s=transferred_getter_sdk(monkeypatch,fault)
    with pytest.raises(owned.NativeResourceError):net.read_windows_ipv4_tcp_dynamic_range()
    owner=net._dynamic_range_owner;entry,=owner.registry.unresolved()
    assert entry.state=='UNKNOWN' and entry.keepalive[0]==(None if fault=='sdk_none' else 900)
    with pytest.raises(owned.NativeResourceError):owner.recover_owned(owner)
    with pytest.raises(owned.NativeResourceError):net.read_windows_ipv4_tcp_dynamic_range()
    assert not s.closed and not s.queries and not s.detached


def test_getter_failed_explicit_recovery_keeps_original_owner_and_has_no_automatic_retry(monkeypatch):
    from comsol_mcp import _windows_t037_network as net, _windows_owned_resources as owned
    s=transferred_getter_sdk(monkeypatch,'close_failure')
    with pytest.raises(owned.NativeResourceError):net.read_windows_ipv4_tcp_dynamic_range()
    owner=net._dynamic_range_owner;entry,=owner.registry.unresolved()
    with pytest.raises(owned.NativeResourceError):owner.recover_owned(owner)
    assert entry.state=='UNKNOWN' and net._dynamic_range_owner is owner and s.closed==[903,902,901,900,903]
    owner.registry.drain(owner.registry.scope())
    with pytest.raises(owned.NativeResourceError):net.read_windows_ipv4_tcp_dynamic_range()
    assert s.closed==[903,902,901,900,903] and len(s.queries)==1
    s.fail_close=False;owner.recover_owned(owner)
    assert entry.state=='RELEASED' and s.closed[-1]==903 and net._dynamic_range_owner is None
