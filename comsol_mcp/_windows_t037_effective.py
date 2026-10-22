"""Read-only five-catalog and role App-ID comparison, never a launch proof."""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
import ntpath
from typing import Any
from ._execution_contract import ExecutionContractError


@dataclass(frozen=True)
class EffectiveWfpObservation:
    snapshot_sha256: str
    matched_filter_keys: tuple[str, ...]
    reasons: tuple[str, ...]
    state: str = 'UNVERIFIED'

    def __post_init__(self):
        if self.state != 'UNVERIFIED':
            raise ValueError('read-only policy comparison cannot mint an effective or launch proof')


def observe_effective_wfp(profile, api) -> EffectiveWfpObservation:
    """Sample one atomic five-catalog snapshot and compare supported endpoints.

    SDK enumeration can omit unreadable objects. Global visibility, arbitration
    and default blocking therefore remain explicit unverified gates even when
    all configured condition rows match native role App-IDs and package SIDs.
    """
    from ._windows_t037_native import (_canonical, _fail, MAX_ENUM_TOTAL_BYTES,
        MAX_ENUM_PAGE,MAX_ENUM_PAGES,MAX_ENUM_ROWS,MAX_ENUM_SECONDS,_SHA_RE)
    from ._windows_t037_wfp import (guid_key, normalize_endpoint_grant,
                                    CtypesWindowsWfpMutationApi, FWPM_CONDITION_ALE_APP_ID)
    snapshot = api.observe_wfp_policy_snapshot()
    fields = {'schema', 'state', 'counts', 'limits', 'snapshot_sha256', 'reasons',
              'providers', 'sublayers', 'filters', 'provider_contexts', 'layers'}
    if (not isinstance(snapshot, dict) or set(snapshot) != fields
            or snapshot['schema'] != 'comsol-mcp.wfp-five-catalog-readonly/1'
            or snapshot['state'] != 'COMPLETE_READ_ONLY'):
        _fail('T037_EFFECTIVE_SNAPSHOT_INVALID', 'effective gate requires the exact five-catalog observation')
    content = {k:snapshot[k] for k in ['providers','sublayers','filters','provider_contexts']}
    if not all(isinstance(v,list) for v in content.values()) or not isinstance(snapshot['layers'],list):
        _fail('T037_EFFECTIVE_SNAPSHOT_INVALID', 'policy catalogs must be bounded lists')
    filter_fields={'filter_key','display_data','provider_key','layer_key','sublayer_key','flags',
        'filter_id','action_type','action_callout_key','context','weight','effective_weight',
        'has_provider_context','provider_data_sha256','conditions','security_sha256'}
    for row in snapshot['filters']:
        if (not isinstance(row,dict) or set(row)!=filter_fields
                or not isinstance(row['conditions'],list)
                or any(type(row[k]) is not int for k in ('flags','filter_id','action_type'))
                or type(row['has_provider_context']) is not bool):
            _fail('T037_EFFECTIVE_SNAPSHOT_INVALID','filter row violates the closed native readback shape')
    content['providers']=sorted(content['providers'],key=lambda r:r['provider_key'] or '')
    content['sublayers']=sorted(content['sublayers'],key=lambda r:r['sublayer_key'] or '')
    content['filters']=sorted(content['filters'],key=lambda r:(r['layer_key'] or '',r['filter_id']))
    content['layers']=sorted(snapshot['layers'],key=lambda r:r['layer_key'])
    raw=_canonical(content)
    if len(raw)>MAX_ENUM_TOTAL_BYTES or hashlib.sha256(raw).hexdigest()!=snapshot['snapshot_sha256']:
        _fail('T037_EFFECTIVE_SNAPSHOT_INVALID', 'five-catalog canonical digest does not match its native readback')
    counts=snapshot['counts'];limits=snapshot['limits']
    count_fields={'providers','sublayers','filters','provider_contexts','layers','conditions','pages',
                  'pages_by_kind','total_records','total_bytes','elapsed_ms'}
    expected_limits={'max_page_size':MAX_ENUM_PAGE,'max_pages_per_kind':MAX_ENUM_PAGES,
                     'max_total_pages':MAX_ENUM_PAGES*5,'max_total_records':MAX_ENUM_ROWS,
                     'max_total_bytes':MAX_ENUM_TOTAL_BYTES,'max_elapsed_ms':MAX_ENUM_SECONDS*1000}
    if (not isinstance(counts,dict) or set(counts)!=count_fields or limits!=expected_limits
            or any(type(counts[k]) is not int or counts[k]<0 for k in count_fields-{'pages_by_kind'})
            or not isinstance(counts['pages_by_kind'],dict)
            or set(counts['pages_by_kind'])!={'Provider','SubLayer','Filter','ProviderContext','Layer'}
            or any(type(v) is not int or not 1<=v<=MAX_ENUM_PAGES for v in counts['pages_by_kind'].values())
            or counts['pages']!=sum(counts['pages_by_kind'].values())
            or any(counts[k]!=len(snapshot[k]) for k in ['providers','sublayers','filters','provider_contexts','layers'])
            or counts['total_records']!=sum(len(snapshot[k]) for k in ['providers','sublayers','filters','provider_contexts','layers'])
            or counts['conditions']!=sum(len(row['conditions']) for row in snapshot['filters'])
            or counts['total_records']>MAX_ENUM_ROWS or counts['total_bytes']!=len(raw)
            or counts['elapsed_ms']>MAX_ENUM_SECONDS*1000):
        _fail('T037_EFFECTIVE_SNAPSHOT_INVALID','five-catalog count/budget claims are incomplete or inconsistent')
    for layer in snapshot['layers']:
        if (set(layer)!={'layer_key','display_data','flags','fields','default_sublayer_key','layer_id'}
                or not isinstance(layer['fields'],list) or len(layer['fields'])>256
                or len({item['field_key'] for item in layer['fields']})!=len(layer['fields'])
                or any(set(item)!={'field_key','field_type','data_type'}
                       or type(item['field_type']) is not int or type(item['data_type']) is not int
                       for item in layer['fields'])
                or type(layer['flags']) is not int):
            _fail('T037_EFFECTIVE_SNAPSHOT_INVALID','layer row does not match the closed field schema')
    from . import _windows_filter_group as group
    from ._windows_t037_native import _validate_normalized_fwp_value
    policy=profile.windows_policy
    reasons=list(snapshot['reasons'])
    layers={guid_key(row['layer_key']):row for row in snapshot['layers']}
    if len(layers)!=len(snapshot['layers']):
        _fail('T037_EFFECTIVE_SNAPSHOT_INVALID', 'layer catalog has duplicate keys')
    configured={guid_key(key) for key in policy.wfp_filter_keys}
    filters={guid_key(row['filter_key']):row for row in snapshot['filters']}
    if len(filters)!=len(snapshot['filters']):
        _fail('T037_EFFECTIVE_SNAPSHOT_INVALID', 'filter catalog has duplicate keys')
    if not configured.issubset(filters):reasons.append('WFP_CONFIGURED_FILTER_SET_MISMATCH')
    # A complete group is distinct from the retained single/legacy exact leaf.
    group_mode=(len(configured)!=len(policy.wfp_grants)
        or any(g.local_port_range is not None or g.remote_port_range is not None for g in policy.wfp_grants)
        or any(filters[k]['action_type']==0x1001 for k in configured.intersection(filters)))
    role_ids={};expected=[]
    for grant in policy.wfp_grants:
        try:
            normalized=(group.normalize_grant(grant,profile.host_resource_profile) if group_mode else
                        normalize_endpoint_grant(grant,policy=policy,launch_images=profile.launch_images))
        except ExecutionContractError as exc:
            reasons.append(exc.code);continue
        if grant.role not in role_ids:
            role_ids[grant.role]=api.observe_application_id(grant.application_path)
        app_id=role_ids[grant.role]
        if (set(app_id)!= {'image_path','type','size','sha256'} or type(app_id['type']) is not int or app_id['type']!=12
                or type(app_id['size']) is not int or not 1<=app_id['size']<=256*1024
                or not isinstance(app_id['sha256'],str) or not _SHA_RE.fullmatch(app_id['sha256'])
                or app_id['image_path']!=ntpath.normcase(ntpath.normpath(grant.application_path))):
            _fail('T037_EFFECTIVE_APP_ID_INVALID','role App-ID observation is malformed')
        if group_mode:
            rows=group.expected_conditions({'action':'PERMIT','principal_sid':policy.appcontainer_sid},normalized,
                    app_identity={k:app_id[k] for k in ('type','size','sha256')})
        else:
            rows=CtypesWindowsWfpMutationApi._expected_conditions(None,normalized,None,policy.appcontainer_sid)
            for row in rows:
                if guid_key(row['field_key'])==guid_key(FWPM_CONDITION_ALE_APP_ID):
                    row['condition_value'].update({'size':app_id['size'],'sha256':app_id['sha256']})
        expected.append((guid_key(grant.layer_key),rows))
    def condition_key(rows):
        return _canonical(sorted([{**c,'field_key':guid_key(c['field_key'])} for c in rows],key=lambda c:c['field_key']))
    def schema_compatible(condition,schema):
        if (not isinstance(condition,dict) or set(condition)!={'field_key','match_type','condition_value'}
                or type(condition['match_type']) is not int):return False
        try:
            field=schema[guid_key(condition['field_key'])]
            _validate_normalized_fwp_value(condition['condition_value'])
        except (KeyError,ExecutionContractError,TypeError,ValueError):return False
        value=condition['condition_value'];kind=value['type']
        if kind==258:
            from ._windows_t037_wfp import FWPM_CONDITION_IP_LOCAL_PORT,FWPM_CONDITION_IP_REMOTE_PORT
            if (not group_mode or condition['match_type']!=5 or field['field_type']!=0 or field['data_type']!=2
                    or guid_key(condition['field_key']) not in {guid_key(FWPM_CONDITION_IP_LOCAL_PORT),guid_key(FWPM_CONDITION_IP_REMOTE_PORT)}
                    or value['low']['type']!=2 or value['high']['type']!=2):return False
            from ._windows_t037_network import validate_port_range
            try:validate_port_range((value['low']['value'],value['high']['value']))
            except ValueError:return False
            return True
        if condition['match_type']!=0:return False
        if field['field_type']==0:return field['data_type']==kind
        if field['field_type']==1:return (field['data_type'],kind) in {(3,256),(11,257)}
        return False
    matched=[];default_layers=[];permit_indices=[];group_weights=[]
    for key in configured.intersection(filters):
        row=filters[key]
        action=row['action_type']
        if (guid_key(row['provider_key'])!=guid_key(policy.wfp_provider_key)
                or guid_key(row['sublayer_key'])!=guid_key(policy.wfp_sublayer_key)
                or type(action) is not int or action not in ({0x1001,0x1002} if group_mode else {0x1002})
                or row.get('action_callout_key') is not None or row['has_provider_context'] is not False
                or type(row['flags']) is not int or row['flags'] not in ({1} if group_mode else {0,1})):
            reasons.append('WFP_CONFIGURED_FILTER_IDENTITY_ACTION_FLAGS_UNVERIFIED');continue
        if group_mode:
            weight=row.get('weight');effective=row.get('effective_weight')
            if (not isinstance(weight,dict) or set(weight)!={'type','value'}
                    or type(weight['type']) is not int or weight['type']!=4
                    or type(weight['value']) is not int or not 0<=weight['value']<2**64
                    or _canonical(effective)!=_canonical(weight)
                    or _canonical(row.get('context'))!=_canonical({'raw_context':0})):
                reasons.append('WFP_GROUP_WEIGHT_OR_CONTEXT_UNVERIFIED');continue
        layer=layers.get(guid_key(row['layer_key']))
        if layer is None:
            reasons.append('WFP_CONFIGURED_LAYER_MISSING');continue
        if layer['flags'] & ~0xf:
            reasons.append('WFP_LAYER_FLAGS_UNSUPPORTED');continue
        schema={guid_key(item['field_key']):item for item in layer['fields']}
        if len(schema)!=len(layer['fields']):
            _fail('T037_EFFECTIVE_SNAPSHOT_INVALID','duplicate normalized layer condition field')
        conditions=row['conditions']
        if (not isinstance(conditions,list) or any(not isinstance(c,dict) or set(c)!={'field_key','match_type','condition_value'} for c in conditions)
                or len({guid_key(c['field_key']) for c in conditions})!=len(conditions)
                or any(guid_key(c['field_key']) not in schema for c in conditions)):
            reasons.append('WFP_CONDITION_LAYER_SCHEMA_MISMATCH');continue
        if not all(schema_compatible(c,schema) for c in conditions):
            reasons.append('WFP_CONDITION_LAYER_DATA_TYPE_UNSUPPORTED');continue
        layer_key=guid_key(row['layer_key'])
        if group_mode and action==0x1001:
            wanted=group.expected_conditions({'action':'BLOCK','principal_sid':policy.appcontainer_sid},{})
            matching=condition_key(conditions)==condition_key(wanted) and layer_key in {guid_key(k) for k in group.LAYERS}
            if matching:default_layers.append(layer_key)
        else:
            indices=[index for index,(expected_layer,rows) in enumerate(expected)
                     if layer_key==expected_layer and condition_key(conditions)==condition_key(rows)]
            matching=len(indices)==1
            if matching:permit_indices.append(indices[0])
        if not matching:
            reasons.append('WFP_ROLE_APP_ID_PACKAGE_ENDPOINT_CONDITIONS_MISMATCH');continue
        if group_mode:group_weights.append((layer_key,action,row['weight']['value']))
        matched.append(key)
    if group_mode:
        if sorted(default_layers)!=sorted(guid_key(k) for k in group.LAYERS):
            reasons.append('WFP_GROUP_FOUR_DEFAULTS_MISSING_OR_DUPLICATED')
        if (len(configured)!=4+len(policy.wfp_grants) or len(expected)!=len(policy.wfp_grants)
                or sorted(permit_indices)!=list(range(len(expected)))):
            reasons.append('WFP_GROUP_COMPLETE_GRANT_SET_MISMATCH')
        for layer_key,action,weight in group_weights:
            blocks=[v for key,kind,v in group_weights if key==layer_key and kind==0x1001]
            if action==0x1002 and (len(blocks)!=1 or weight<=blocks[0]):
                reasons.append('WFP_GROUP_ALLOW_WEIGHT_NOT_ABOVE_DEFAULT')
    for key,row in filters.items():
        if row['action_type'] not in {0x1001,0x1002}:
            reasons.append('WFP_EXTERNAL_CALLOUT_OR_UNKNOWN_ACTION_PRESENT')
        if row['flags'] & ~1:
            reasons.append('WFP_FILTER_LIFETIME_TERMINATING_RIGHTS_OR_FLAGS_UNRESOLVED')
        if row['effective_weight'].get('type') not in {4}:
            reasons.append('WFP_EFFECTIVE_WEIGHT_NOT_OBSERVED_UINT64')
        if key not in configured:
            reasons.append('WFP_EXTERNAL_FILTER_ARBITRATION_UNRESOLVED')
    reasons.extend(('WFP_ENUMERATION_ACCESS_VISIBILITY_NOT_PROVEN',
                    'WFP_DEFAULT_BLOCK_NOT_PROVEN','WFP_EFFECTIVE_ARBITRATION_NOT_PROVEN'))
    return EffectiveWfpObservation(snapshot['snapshot_sha256'],tuple(sorted(matched)),tuple(dict.fromkeys(reasons)))
