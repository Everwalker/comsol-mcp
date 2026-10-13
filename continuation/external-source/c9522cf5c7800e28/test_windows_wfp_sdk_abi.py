from __future__ import annotations
import ctypes as C
import pytest
from comsol_mcp import _windows_t037_native as native
from comsol_mcp import _windows_t037_wfp as wfp


def test_wfp_address_mask_constants_match_sdk_enum_after_reserved_0xff_sentinel():
    # Independent SDK literal oracle, never derived from implementation constants.
    # https://learn.microsoft.com/en-us/windows/win32/api/fwptypes/ne-fwptypes-fwp_data_type
    assert wfp.FWP_V4_ADDR_MASK == 0x100


def test_wfp_native_v4_decoder_accepts_actual_sdk_type_0x100_and_rejects_reserved_19():
    api=native.CtypesWindowsPolicyApi.__new__(native.CtypesWindowsPolicyApi)
    address=native.FWP_V4_ADDR_AND_MASK(0x7f000001,0xffffffff)
    value=native.FWP_CONDITION_VALUE0()
    value.type=0x100
    value.v4AddrMask=C.cast(C.pointer(address),C.c_void_p)
    assert api._decode_value(value)=={'type':0x100,'address_u32':0x7f000001,'mask_u32':0xffffffff}
    value.type=19
    with pytest.raises(native.NativePolicyError):api._decode_value(value)


def test_layer_and_field_layout_match_independent_x64_sdk_alignment():
    assert C.sizeof(native.FWPM_FIELD0)==16
    assert C.sizeof(native.FWPM_LAYER0)==72
    assert {k:getattr(native.FWPM_FIELD0,k).offset for k in ['fieldKey','type','dataType']}=={'fieldKey':0,'type':8,'dataType':12}
    assert {k:getattr(native.FWPM_LAYER0,k).offset for k in ['layerKey','displayData','flags','numFields','field','defaultSubLayerKey','layerId']}=={'layerKey':0,'displayData':16,'flags':32,'numFields':36,'field':40,'defaultSubLayerKey':48,'layerId':64}


def _catalog_api(*,bad_field=None):
    class ReadOnlyExports:
        def __init__(self):
            self.events=[];self.layer_pages=0;self.kept=[]
        def FwpmEngineOpen0(self,*args):C.cast(args[-1],C.POINTER(native.HANDLE)).contents.value=1;self.events.append('open');return 0
        def FwpmEngineClose0(self,*_):self.events.append('close');return 0
        def FwpmTransactionBegin0(self,_engine,flags):assert flags==native.CtypesWindowsPolicyApi.FWPM_TXN_READ_ONLY;self.events.append('begin-readonly');return 0
        def FwpmTransactionCommit0(self,*_):self.events.append('commit');return 0
        def FwpmTransactionAbort0(self,*_):self.events.append('abort');return 0
        def FwpmFreeMemory0(self,*_):self.events.append('free')
        def __getattr__(self,name):
            if name.startswith('Fwpm') and name.endswith('CreateEnumHandle0'):
                def create(*args):C.cast(args[-1],C.POINTER(native.HANDLE)).contents.value=10;self.events.append(name);return 0
                return create
            if name.startswith('Fwpm') and name.endswith('DestroyEnumHandle0'):
                def destroy(*args):self.events.append(name);return 0
                return destroy
            if name.startswith('Fwpm') and name.endswith('Enum0'):
                def enum(_engine,_handle,_count,array,count):
                    self.events.append(name)
                    if name!='FwpmLayerEnum0' or self.layer_pages:
                        C.cast(count,C.POINTER(native.DWORD)).contents.value=0;return 0
                    self.layer_pages+=1
                    key=native.GUID();key.Data1=1
                    field=native.FWPM_FIELD0(C.pointer(key),0,256 if bad_field is None else bad_field)
                    fields=(native.FWPM_FIELD0*1)(field)
                    layer=native.FWPM_LAYER0();layer.numFields=1;layer.field=C.cast(fields,C.POINTER(native.FWPM_FIELD0));layer.flags=3
                    pointers=(C.POINTER(native.FWPM_LAYER0)*1)(C.pointer(layer))
                    self.kept.extend([key,fields,layer,pointers])
                    C.cast(array,C.POINTER(C.c_void_p)).contents.value=C.cast(pointers,C.c_void_p).value
                    C.cast(count,C.POINTER(native.DWORD)).contents.value=1;return 0
                return enum
            raise AssertionError('unexpected export '+name)
    api=native.CtypesWindowsPolicyApi.__new__(native.CtypesWindowsPolicyApi);api._fwpu=ReadOnlyExports();return api


def test_five_catalog_readonly_snapshot_reads_layers_in_same_transaction_and_keeps_access_gate():
    api=_catalog_api();result=api.observe_wfp_policy_snapshot()
    events=api._fwpu.events
    assert result['counts']['layers']==1
    assert result['layers'][0]['fields'][0]['data_type']==256
    assert set(result['counts']['pages_by_kind'])=={'Provider','SubLayer','Filter','ProviderContext','Layer'}
    assert events.index('begin-readonly')<events.index('FwpmLayerEnum0')<events.index('commit')<events.index('close')
    assert 'WFP_ENUMERATION_ACCESS_VISIBILITY_NOT_PROVEN' in result['reasons']
    assert 'WFP_EFFECTIVE_ARBITRATION_NOT_PROVEN' in result['reasons']
    assert events.count('free')==1
    assert sum(name.endswith('DestroyEnumHandle0') for name in events)==5


@pytest.mark.parametrize('reserved',[19,20,21,255,259])
def test_layer_unsupported_sdk_field_type_aborts_and_releases_allocations(reserved):
    api=_catalog_api(bad_field=reserved)
    with pytest.raises(native.NativePolicyError):api.observe_wfp_policy_snapshot()
    assert 'abort' in api._fwpu.events and 'commit' not in api._fwpu.events
    assert api._fwpu.events.count('free')==1 and api._fwpu.events[-1]=='close'


@pytest.mark.parametrize('status',[0,77])
def test_native_appid_observer_reuses_existing_sdk_reader_and_frees_even_failed_allocations(status):
    class Exports:
        def __init__(self):self.freed=[];self.kept=[];self.paths=[]
        def FwpmGetAppIdFromFileName0(self,path,output):
            self.paths.append(path);data=(C.c_ubyte*4)(1,2,3,4)
            blob=native.FWP_BYTE_BLOB(4,C.cast(data,C.POINTER(C.c_ubyte)))
            pointer=C.pointer(blob);self.kept.extend([data,blob,pointer])
            C.cast(output,C.POINTER(C.POINTER(native.FWP_BYTE_BLOB)))[0]=pointer;return status
        def FwpmFreeMemory0(self,output):self.freed.append(C.cast(output,C.POINTER(C.c_void_p)).contents.value)
    api=native.CtypesWindowsPolicyApi.__new__(native.CtypesWindowsPolicyApi);api._fwpu=Exports()
    if status:
        with pytest.raises(native.NativePolicyError):api.observe_application_id(r'C:\COMSOL\server.exe')
    else:
        import hashlib
        row=api.observe_application_id(r'C:\COMSOL\server.exe')
        assert row=={'image_path':r'c:\comsol\server.exe','type':12,'size':4,'sha256':hashlib.sha256(bytes([1,2,3,4])).hexdigest()}
    assert len(api._fwpu.freed)==1


def _effective_api():
    from tests.test_windows_host_change import _profile,_plan,_row
    from comsol_mcp._windows_host_change import _normalize_supported_plan
    from comsol_mcp._windows_t037 import WindowsT037ProfileV2
    import hashlib
    profile=_profile();plan=_plan(profile);_,action=_normalize_supported_plan(plan,profile)
    grant=wfp.normalize_endpoint_grant(profile.windows_isolation.wfp_grants[0],policy=profile.windows_isolation,launch_images=profile.launch_images)
    blob=b'actual-api-software-fixture-only'
    row=_row(profile,action)
    row['conditions']=wfp.CtypesWindowsWfpMutationApi._expected_conditions(None,grant,blob,profile.windows_isolation.appcontainer_sid)
    row['effective_weight']={'type':4,'value':1}
    fields=[]
    for condition in row['conditions']:
        kind=condition['condition_value']['type']
        fields.append({'field_key':condition['field_key'],'field_type':1 if kind==256 else 0,'data_type':3 if kind==256 else kind})
    snapshot={'schema':'comsol-mcp.wfp-five-catalog-readonly/1','state':'COMPLETE_READ_ONLY','counts':{},'limits':{},'snapshot_sha256':'','reasons':[],
              'providers':[],'sublayers':[],'filters':[row],'provider_contexts':[],
              'layers':[{'layer_key':row['layer_key'],'flags':3,'fields':fields,
                         'display_data':{'name':None,'description':None},'default_sublayer_key':row['sublayer_key'],'layer_id':1}]}
    class Api:
        def __init__(self):self.paths=[]
        def observe_wfp_policy_snapshot(self):
            content={k:snapshot[k] for k in ['providers','sublayers','filters','provider_contexts','layers']}
            raw=native._canonical(content);snapshot['snapshot_sha256']=hashlib.sha256(raw).hexdigest()
            snapshot['counts']={k:len(snapshot[k]) for k in ['providers','sublayers','filters','provider_contexts','layers']}
            snapshot['counts'].update(conditions=sum(len(r['conditions']) for r in snapshot['filters']),pages=5,
                pages_by_kind={k:1 for k in ['Provider','SubLayer','Filter','ProviderContext','Layer']},
                total_records=sum(snapshot['counts'].values()),total_bytes=len(raw),elapsed_ms=1)
            snapshot['limits']={'max_page_size':native.MAX_ENUM_PAGE,'max_pages_per_kind':native.MAX_ENUM_PAGES,
                'max_total_pages':native.MAX_ENUM_PAGES*5,'max_total_records':native.MAX_ENUM_ROWS,
                'max_total_bytes':native.MAX_ENUM_TOTAL_BYTES,'max_elapsed_ms':native.MAX_ENUM_SECONDS*1000}
            return snapshot
        def observe_application_id(self,path):
            self.paths.append(path)
            return {'image_path':path.lower(),'type':12,'size':len(blob),'sha256':hashlib.sha256(blob).hexdigest()}
    wrapper=WindowsT037ProfileV2(host_resource_profile=profile,source_sha256='d'*64,
                               source_identity={},security_semantics='SOFTWARE_ONLY')
    return wrapper,Api(),snapshot


def test_effective_gate_compares_typed_native_role_appid_sid_endpoint_but_cannot_mint_effective_proof():
    from comsol_mcp._windows_t037_effective import observe_effective_wfp,EffectiveWfpObservation
    profile,api,snapshot=_effective_api();result=observe_effective_wfp(profile,api)
    assert result.matched_filter_keys==(wfp.guid_key(profile.windows_policy.wfp_filter_keys[0]),)
    assert api.paths==[profile.windows_policy.wfp_grants[0].application_path]
    assert result.state=='UNVERIFIED'
    assert {'WFP_ENUMERATION_ACCESS_VISIBILITY_NOT_PROVEN','WFP_DEFAULT_BLOCK_NOT_PROVEN','WFP_EFFECTIVE_ARBITRATION_NOT_PROVEN'}.issubset(result.reasons)
    with pytest.raises(ValueError):EffectiveWfpObservation('a'*64,(),(),'VERIFIED')


@pytest.mark.parametrize('tamper',['app-id','sid','operator','flags','schema','callout','external'])
def test_effective_gate_rejects_condition_policy_and_arbitration_counterexamples(tamper):
    from comsol_mcp._windows_t037_effective import observe_effective_wfp
    profile,api,snapshot=_effective_api();row=snapshot['filters'][0]
    if tamper=='app-id':row['conditions'][0]['condition_value']['sha256']='0'*64
    elif tamper=='sid':row['conditions'][1]['condition_value']['value']='S-1-15-2-999'
    elif tamper=='operator':row['conditions'][0]['match_type']=1
    elif tamper=='flags':row['flags']=8
    elif tamper=='schema':snapshot['layers'][0]['fields'][0]['data_type']=13
    elif tamper=='callout':row['action_type']=0x5003
    else:
        import copy
        other=copy.deepcopy(row);other['filter_key']='{11111111-1111-1111-1111-111111111111}';other['filter_id']=99
        snapshot['filters'].append(other)
    result=observe_effective_wfp(profile,api)
    assert result.state=='UNVERIFIED'
    if tamper!='external':assert result.matched_filter_keys==()
    else:assert 'WFP_EXTERNAL_FILTER_ARBITRATION_UNRESOLVED' in result.reasons


def test_actual_native_policy_adapter_calls_five_catalog_gate_and_keeps_its_exact_digest():
    from tests.test_windows_t037_native import _FakeApi,_FakeProcessObserver
    profile,effective,snapshot=_effective_api()
    class Api(_FakeApi):
        def observe_wfp_policy_snapshot(self):return effective.observe_wfp_policy_snapshot()
        def observe_application_id(self,path):return effective.observe_application_id(path)
    result=native.WindowsNativePolicyAdapter(api=Api()).observe(
        profile,current_host_fingerprint=profile.host_fingerprint_sha256,process_observer=_FakeProcessObserver())
    assert result.effective_catalog_sha256==snapshot['snapshot_sha256']
    assert result.as_safe_dict()['effective_catalog_sha256']==snapshot['snapshot_sha256']
    assert effective.paths==[profile.windows_policy.wfp_grants[0].application_path]
    assert 'WFP_DEFAULT_BLOCK_NOT_PROVEN' in result.reasons
    assert result.verified is False and result.evidence_classification=='SOFTWARE_ONLY'


def test_sdk_literal_v6_mask_and_range_decoders_use_257_and_258():
    api=native.CtypesWindowsPolicyApi.__new__(native.CtypesWindowsPolicyApi)
    address=native.FWP_V6_ADDR_AND_MASK();address.addr[15]=1;address.prefixLength=128
    value=native.FWP_CONDITION_VALUE0();value.type=257;value.v6AddrMask=C.cast(C.pointer(address),C.c_void_p)
    assert api._decode_value(value)=={'type':257,'address':'00'*15+'01','prefix_length':128}
    interval=native.FWPM_RANGE0();interval.valueLow.type=2;interval.valueLow.uint16=100;interval.valueHigh.type=2;interval.valueHigh.uint16=200
    value.type=258;value.rangeValue=C.cast(C.pointer(interval),C.c_void_p)
    assert api._decode_value(value)=={'type':258,'low':{'type':2,'value':100},'high':{'type':2,'value':200}}
    for reserved in [19,20,21]:
        value.type=reserved
        with pytest.raises(native.NativePolicyError):api._decode_value(value)
        with pytest.raises(native.NativePolicyError):native._validate_normalized_fwp_value({'type':reserved,'value':0})
