"""Explicit four-ALE-layer owned filter groups; intent and readback, never proof."""
from __future__ import annotations
import hashlib,ipaddress,json,uuid
from typing import Mapping
from ._execution_contract import ExecutionContractError

SCHEMA='comsol-mcp.windows-wfp-filter-group-member/1'
SEQUENCE='comsol-mcp.host-control-mutation-sequence/3'
LAYERS={
 '{C38D57D1-05A7-4C33-904F-7FBCEEE60E82}':('OUTBOUND','IPV4','ALE_AUTH_CONNECT'),
 '{4A72393B-319F-44BC-84C3-BA54DCB3B6B4}':('OUTBOUND','IPV6','ALE_AUTH_CONNECT'),
 '{E1CD9FE7-F4B5-4273-96C0-592E487B8650}':('INBOUND','IPV4','ALE_AUTH_RECV_ACCEPT'),
 '{A3B42C97-9F04-4672-B87E-CEE9C483257F}':('INBOUND','IPV6','ALE_AUTH_RECV_ACCEPT'),
}
FIELDS={'schema','group_id','group_sha256','filter_key','provider_key','sublayer_key',
        'action','weight','flags','layer_key','direction','address_family','principal_sid','grant_sha256'}

def fail(message):raise ExecutionContractError('WFP_GROUP_INVALID',message,safe_retry=False,stage='validation')
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
def digest(value):return hashlib.sha256(canonical(value).encode()).hexdigest()
def guid(value):
    if not isinstance(value,str):fail('explicit GUID required')
    try:normal='{'+str(uuid.UUID(value.strip('{}'))).upper()+'}'
    except ValueError:fail('GUID malformed')
    if value!=normal:fail('GUID must be explicit canonical SDK form')
    return normal

def is_group(manifest):
    if not isinstance(manifest,(list,tuple)):return False
    return any(isinstance(a,Mapping) and isinstance(a.get('desired'),Mapping)
               and a['desired'].get('schema')==SCHEMA for a in manifest)


def normalize_grant(grant,profile):
    from ._windows_t037_wfp import guid_key
    row=grant.as_dict();key=next((k for k in LAYERS if guid_key(k)==guid_key(row['layer_key'])),None)
    if key is None:fail('grant layer outside finite CONNECT/RECV_ACCEPT v4/v6 catalog')
    direction,family,layer=LAYERS[key]
    if (set(row)!={'role','direction','layer','layer_key','address_family','protocol','application_path',
        'principal_sid','local_addresses','remote_addresses','local_ports','remote_ports'}
        or row['role'] not in {'server','worker'} or row['direction']!=direction or row['layer']!=layer
        or row['address_family']!=family or type(row['protocol']) is not int or row['protocol'] not in {6,17}
        or row['principal_sid'].casefold()!=profile.windows_isolation.appcontainer_sid.casefold()
        or any(len(row[name])!=1 for name in ['local_addresses','remote_addresses','local_ports','remote_ports'])):
        fail('group allows require every exact approved TCP/UDP address/port selector; no dropped fields')
    image=[i for i in profile.launch_images if i.role==row['role']]
    if len(image)!=1 or image[0].path!=row['application_path']:fail('allow image differs from exact role binding')
    networks={}
    for name in ['local','remote']:
        raw=row[name+'_addresses'][0]
        try:net=ipaddress.ip_network(raw,strict=True)
        except ValueError:fail('CIDR malformed')
        if net.with_prefixlen!=raw or net.version!=(4 if family=='IPV4' else 6):fail('canonical address family/CIDR required')
        port=row[name+'_ports'][0]
        if type(port) is not int or not 1<=port<=65535:fail('explicit supported port required')
        networks[name+'_address']=raw;networks[name+'_port']=port
    return {'role':row['role'],'direction':direction,'layer':layer,'layer_key':key,'address_family':family,
        'protocol':row['protocol'],'application_path':row['application_path'],'principal_sid':row['principal_sid'],**networks}


def validate_member(desired,profile):
    from ._windows_t037_wfp import guid_key
    if not isinstance(desired,Mapping) or set(desired)!=FIELDS or desired['schema']!=SCHEMA:fail('closed explicit group-member schema required')
    p=profile.windows_isolation
    if profile.platform!='windows' or p is None:fail('Windows isolation profile required')
    for name in ['group_id','filter_key','provider_key','sublayer_key','layer_key']:guid(desired[name])
    if guid_key(desired['filter_key']) not in {guid_key(k) for k in p.wfp_filter_keys}:fail('filter GUID outside complete approved profile set')
    if (guid_key(desired['provider_key'])!=guid_key(p.wfp_provider_key)
        or guid_key(desired['sublayer_key'])!=guid_key(p.wfp_sublayer_key)
        or desired['principal_sid']!=p.appcontainer_sid):fail('exact provider/sublayer/package subject required')
    if desired['layer_key'] not in LAYERS or (desired['direction'],desired['address_family'])!=LAYERS[desired['layer_key']][:2]:fail('exact four-layer family/direction required')
    if desired['action'] not in {'BLOCK','PERMIT'} or type(desired['flags']) is not int or desired['flags']!=1:fail('ordinary persistent filter action/flags required')
    if type(desired['weight']) is not int or not 0<=desired['weight']<=2**64-1:fail('explicit UINT64 weight required')
    if not isinstance(desired['group_sha256'],str) or len(desired['group_sha256'])!=64 or any(c not in '0123456789abcdef' for c in desired['group_sha256']):fail('group fingerprint malformed')
    if desired['action']=='BLOCK':
        if desired['grant_sha256'] is not None:fail('default BLOCK is package-only, no grant/image selector')
        return {'layer_key':desired['layer_key'],'direction':desired['direction'],'address_family':desired['address_family'],'principal_sid':p.appcontainer_sid}
    matching=[g for g in p.wfp_grants if digest(g.as_dict())==desired['grant_sha256']]
    if len(matching)!=1:fail('PERMIT must bind one unique whole approved grant')
    normalized=normalize_grant(matching[0],profile)
    if normalized['layer_key']!=desired['layer_key']:fail('PERMIT layer differs from approved grant')
    return normalized


def group_fingerprint(profile,members):
    if not members:fail('empty group')
    return digest({'schema':SCHEMA,'profile_sha256':profile.sha256,'group_id':members[0]['group_id'],
        'members':sorted([{k:v for k,v in m.items() if k!='group_sha256'} for m in members],key=lambda m:m['filter_key'])})


def normalize_actions(payload,profile,plan_sha256):
    from ._windows_t037_wfp import CtypesWindowsWfpMutationApi,guid_key
    rows=payload['change_manifest'];filters=[a for a in rows if a['resource_kind']=='wfp_filter'];members=[a['desired'] for a in filters]
    if not 4<=len(filters)<=4096 or len(rows)!=len(filters)+2:fail('complete group requires 4..4096 filters plus owned provider/sublayer')
    kinds=[a['resource_kind'] for a in rows]
    if kinds.count('wfp_provider')!=1 or kinds.count('wfp_sublayer')!=1 or any(k not in {'wfp_provider','wfp_sublayer','wfp_filter'} for k in kinds):fail('unsupported/missing group dependency resources')
    if any(a['operation']!='CREATE' for a in rows):fail('group applies only absent-only CREATE')
    if len({a['action_id'] for a in rows})!=len(rows):fail('duplicate action ID')
    keys=[guid_key(a['desired'][{'wfp_provider':'provider_key','wfp_sublayer':'sublayer_key','wfp_filter':'filter_key'}[a['resource_kind']]]) for a in rows]
    if len(set(keys))!=len(keys):fail('duplicate/colliding whole group resource GUID')
    if {guid_key(m['filter_key']) for m in members}!={guid_key(k) for k in profile.windows_isolation.wfp_filter_keys}:fail('group must cover exact complete profile filter set')
    for m in members:validate_member(m,profile)
    if len({m['group_id'] for m in members})!=1 or {m['group_sha256'] for m in members}!={group_fingerprint(profile,members)}:fail('whole group fingerprint mismatch')
    blocks=[m for m in members if m['action']=='BLOCK'];permits=[m for m in members if m['action']=='PERMIT']
    if len(blocks)!=4 or {m['layer_key'] for m in blocks}!=set(LAYERS):fail('every exact default BLOCK layer required')
    required=[digest(g.as_dict()) for g in profile.windows_isolation.wfp_grants]
    if len(set(required))!=len(required) or sorted(m['grant_sha256'] for m in permits)!=sorted(required):fail('every approved grant required exactly once')
    if len(rows)!=6+len(required):fail('derived whole-group action count mismatch')
    for m in permits:
        b=next(b for b in blocks if b['layer_key']==m['layer_key'])
        if m['weight']<=b['weight']:fail('allow weight must strictly exceed its same-layer default BLOCK')
    out=[]
    for index,a in enumerate(rows):
        kind=a['resource_kind'];desired=a['desired']
        action={'action_id':a['action_id'],'resource_name':a['resource_name'],'resource_kind':kind,'resource_key':'{'+keys[index].upper()+'}',
            'desired':dict(desired),'desired_identity_sha256':a['desired_identity_sha256'],'ownership_guid':a['ownership_guid'],'plan_sha256':plan_sha256}
        # guid_key returns the body without braces.
        if kind=='wfp_filter':
            action.update(filter_key=desired['filter_key'],provider_key=desired['provider_key'],sublayer_key=desired['sublayer_key'],
                grant=validate_member(desired,profile),grant_sha256=desired['grant_sha256'],group_member=True)
        else:action['expected_resource']=getattr(CtypesWindowsWfpMutationApi,'_expected_'+('provider' if kind=='wfp_provider' else 'sublayer')+'_row')(desired)
        out.append(action)
    return tuple(sorted(out,key=lambda a:({'wfp_provider':0,'wfp_sublayer':1,'wfp_filter':2}[a['resource_kind']],
        0 if a['desired'].get('action')=='BLOCK' else 1,a['resource_key'])))


def validate_journal_group(baseline,row):
    """Reconstruct exact approved whole-plan targets inside the original Store ledger."""
    from . import _w26_os_resources as resources
    proof=baseline.get('filter_group_plan')
    if not isinstance(proof,dict):fail('sequence3 requires the exact immutable complete group plan')
    raw=canonical(proof);plan=resources.HostChangePlan(raw,digest(proof))
    payload=resources.validate_host_change_plan(plan)
    profile=resources.parse_host_resource_profile(payload['profile'])
    expected_source=row['plan_sha256'] if row['phase']=='APPLY' else row['source_plan_sha256']
    if plan.sha256!=expected_source or profile.sha256!=row['profile_sha256']:fail('journal group plan/profile differs from original admitted source identity')
    actions=normalize_actions(payload,profile,plan.sha256)
    ordered=actions if row['phase']=='APPLY' else tuple(reversed(actions))
    steps=baseline['mutation_sequence']['steps']
    if len(steps)!=len(ordered):fail('sequence3 does not match the derived complete four-default/all-allow group')
    for step,a in zip(steps,ordered,strict=True):
        if (step['action_id']!=a['action_id'] or step['resource_kind']!=a['resource_kind']
            or step['resource_key']!=a['resource_key'] or step['identity_sha256']!=a['desired_identity_sha256']):
            fail('sequence3 step differs from exact approved group member')
    if len(canonical(baseline).encode())>resources.MAX_HOST_PLAN_BYTES:fail('group ledger exceeds original bounded serialized plan budget')


# All declarations below use the existing fixed Win64 SDK structures and original API owner.
import ctypes as C
from . import _windows_t037_native as native

V6_ADDRESS=native.FWP_V6_ADDR_AND_MASK


def conditions(api,desired,grant,app_blob=None,package_pointer=None):
    from . import _windows_t037_wfp as w
    package=api._condition(w.FWPM_CONDITION_ALE_PACKAGE_ID,w.FWP_SID)
    package.conditionValue.sid=package_pointer
    rows=[package];backing=[]
    if desired['action']=='PERMIT':
        app=api._condition(w.FWPM_CONDITION_ALE_APP_ID,w.FWP_BYTE_BLOB_TYPE)
        app.conditionValue.byteBlob=app_blob;rows.insert(0,app)
        for field,name in [(w.FWPM_CONDITION_IP_LOCAL_ADDRESS,'local_address'),(w.FWPM_CONDITION_IP_REMOTE_ADDRESS,'remote_address')]:
            net=ipaddress.ip_network(grant[name],strict=True)
            if net.version==4:
                value=native.FWP_V4_ADDR_AND_MASK(int(net.network_address),int(net.netmask));kind=256
            else:
                value=V6_ADDRESS((C.c_uint8*16).from_buffer_copy(net.network_address.packed),net.prefixlen);kind=257
            backing.append(value);condition=api._condition(field,kind)
            if kind==256:condition.conditionValue.v4AddrMask=C.cast(C.pointer(value),C.c_void_p)
            else:condition.conditionValue.v6AddrMask=C.cast(C.pointer(value),C.c_void_p)
            rows.append(condition)
        for field,name,kind in [(w.FWPM_CONDITION_IP_PROTOCOL,'protocol',1),
            (w.FWPM_CONDITION_IP_LOCAL_PORT,'local_port',2),(w.FWPM_CONDITION_IP_REMOTE_PORT,'remote_port',2)]:
            condition=api._condition(field,kind)
            if kind==1:condition.conditionValue.uint8=grant[name]
            else:condition.conditionValue.uint16=grant[name]
            rows.append(condition)
    return (native.FWPM_FILTER_CONDITION0*len(rows))(*rows),backing


def expected_conditions(desired,grant,app_bytes=None):
    from . import _windows_t037_wfp as w
    rows=[(w.FWPM_CONDITION_ALE_PACKAGE_ID,{'type':13,'value':desired['principal_sid']})]
    if desired['action']=='PERMIT':
        if type(app_bytes) is not bytes or not app_bytes:fail('actual native App-ID bytes required')
        rows.append((w.FWPM_CONDITION_ALE_APP_ID,{'type':12,'size':len(app_bytes),'sha256':hashlib.sha256(app_bytes).hexdigest()}))
        for field,name in [(w.FWPM_CONDITION_IP_LOCAL_ADDRESS,'local_address'),(w.FWPM_CONDITION_IP_REMOTE_ADDRESS,'remote_address')]:
            net=ipaddress.ip_network(grant[name],strict=True)
            value={'type':256,'address_u32':int(net.network_address),'mask_u32':int(net.netmask)} if net.version==4 else {'type':257,'address':net.network_address.packed.hex(),'prefix_length':net.prefixlen}
            rows.append((field,value))
        for field,name,kind in [(w.FWPM_CONDITION_IP_PROTOCOL,'protocol',1),
            (w.FWPM_CONDITION_IP_LOCAL_PORT,'local_port',2),(w.FWPM_CONDITION_IP_REMOTE_PORT,'remote_port',2)]:rows.append((field,{'type':kind,'value':grant[name]}))
    return [{'field_key':field.upper(),'match_type':0,'condition_value':value} for field,value in rows]


def make_filter(api,policy,action,marker,condition_rows):
    from . import _windows_t037_wfp as w
    desired=action['desired'];obj=api._make_filter(policy,action,marker,condition_rows)
    value=C.c_uint64(desired['weight']);obj.weight.type=4;obj.weight.uint64=C.pointer(value)
    obj.action.type=0x1001 if desired['action']=='BLOCK' else 0x1002
    display_name=C.create_string_buffer(('COMSOL MCP T037 '+action['action_id']).encode('utf-16-le')+b'\0\0')
    obj.displayData.name=C.cast(display_name,C.c_void_p)
    obj._keepalive=(*obj._keepalive,value,display_name)
    return obj


def validate_readback(api,row,profile,action,marker,app_bytes):
    from . import _windows_t037_wfp as w
    desired=action['desired'];policy=profile.windows_isolation
    if (w.guid_key(row.get('filter_key'))!=w.guid_key(desired['filter_key'])
        or w.guid_key(row.get('provider_key'))!=w.guid_key(policy.wfp_provider_key)
        or w.guid_key(row.get('sublayer_key'))!=w.guid_key(policy.wfp_sublayer_key)
        or w.guid_key(row.get('layer_key'))!=w.guid_key(desired['layer_key'])
        or type(row.get('flags')) is not int or row['flags']!=1
        or type(row.get('action_type')) is not int
        or row.get('action_type')!=(0x1001 if desired['action']=='BLOCK' else 0x1002)
        or row.get('action_callout_key') is not None or row.get('has_provider_context') is not False
        or row.get('context')!={'raw_context':0}
        or not isinstance(row.get('weight'),dict) or any(type(row['weight'].get(k)) is not int for k in ('type','value'))
        or row.get('weight')!={'type':4,'value':desired['weight']}
        or not isinstance(row.get('effective_weight'),dict) or any(type(row['effective_weight'].get(k)) is not int for k in ('type','value'))
        or row.get('effective_weight')!={'type':4,'value':desired['weight']}
        or row.get('provider_data_sha256')!=hashlib.sha256(marker).hexdigest()
        or row.get('display_data')!={'name':'COMSOL MCP T037 '+action['action_id'],'description':None}):
        fail('group native filter identity/action/flags/weight/ownership-marker readback mismatch')
    expected=expected_conditions(desired,action['grant'],app_bytes);actual=row.get('conditions')
    if (not isinstance(actual,list) or len(actual)!=len(expected)
        or canonical(sorted(actual,key=lambda c:c.get('field_key',''))) != canonical(sorted(expected,key=lambda c:c['field_key']))):
        fail('group native package/App-ID/all-endpoint condition readback mismatch')


def create_filter(api,profile,action,dependency_sha):
    from . import _windows_t037_wfp as w
    from ._windows_wfp_mutation_owner import hold_buffers
    desired=action['desired'];marker=api._owned_marker(profile,action);policy=profile.windows_isolation
    def write(engine):
        api._check_dependencies(engine,profile,dependency_sha)
        if api._filter_row_or_none(engine,desired['filter_key']) is not None:fail('group key collision; never overwrite')
        package=C.c_void_p();blob=C.POINTER(native.FWP_BYTE_BLOB)();app_bytes=None
        try:
            api._convert_sid(desired['principal_sid'],sid_out=package)
            if desired['action']=='PERMIT':
                result=api._sdk_alloc('FwpmGetAppIdFromFileName0',blob,action['grant']['application_path'],C.byref(blob))
                api._check(result,'FwpmGetAppIdFromFileName0')
                if not blob or not blob.contents.data or not 0<blob.contents.size<=256*1024:fail('App-ID allocation malformed')
                app_bytes=C.string_at(blob.contents.data,blob.contents.size)
            rows,backing=conditions(api,desired,action['grant'],blob,package)
            obj=make_filter(api,policy,action,marker,rows);identifier=C.c_uint64(0)
            hold_buffers(api,obj,rows,backing,blob,package,identifier,app_bytes)
            api._check(api._sdk_status('FwpmFilterAdd0',engine,C.byref(obj),None,C.byref(identifier)),'FwpmFilterAdd0')
            row=api._filter_row_or_none(engine,desired['filter_key'])
            if not 0<identifier.value<2**64 or row is None or row.get('filter_id')!=identifier.value:fail('actual group filter runtime ID readback missing/mismatch')
            validate_readback(api,row,profile,action,marker,app_bytes)
            return row
        except BaseException as primary:
            from ._windows_wfp_mutation_owner import remember_cause
            remember_cause(api, primary)
            raise
        finally:
            # Both families are attempted; registry retains failures with exact original closers.
            errors=[]
            if blob:
                try:api._free(C.cast(blob,C.c_void_p).value)
                except BaseException as e:errors.append(e)
            if package.value:
                try:api._free_local(package)
                except BaseException as e:errors.append(e)
            if errors:raise errors[0]
    row=api._session(readonly=False,callback=write)
    persisted=api.read_filter(desired['filter_key'])
    if persisted is None or w.sha256(persisted)!=w.sha256(row):fail('committed full group filter drifted after transaction')
    blob=api._app_id_blob(action['grant']['application_path']) if desired['action']=='PERMIT' else None
    validate_readback(api,persisted,profile,action,marker,blob)
    return persisted


def delete_filter(api,profile,action,resource_sha,dependency_sha):
    from . import _windows_t037_wfp as w
    marker=api._owned_marker(profile,action);desired=action['desired']
    def write(engine):
        api._check_dependencies(engine,profile,dependency_sha)
        row=api._filter_row_or_none(engine,desired['filter_key'])
        if row is None or w.sha256(row)!=resource_sha:fail('receipt-bound original group filter drifted; preserved')
        blob=api._app_id_blob(action['grant']['application_path']) if desired['action']=='PERMIT' else None
        validate_readback(api,row,profile,action,marker,blob)
        key=w.guid_struct(desired['filter_key'])
        api._check(api._sdk_status('FwpmFilterDeleteByKey0',engine,C.byref(key)),'FwpmFilterDeleteByKey0')
        if api._filter_row_or_none(engine,desired['filter_key']) is not None:fail('group delete transaction readback not absent')
    api._session(readonly=False,callback=write)
    if api.read_filter(desired['filter_key']) is not None:fail('group committed delete readback not absent')
