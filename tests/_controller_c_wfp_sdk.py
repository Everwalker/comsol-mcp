"""Inert WFP exports feeding the unmodified five-catalog/SD/App-ID readers."""
import copy
import ctypes as C
import hashlib
import os
import struct
import subprocess
from types import SimpleNamespace as NS
from comsol_mcp import _windows_t037_native as n,_windows_t037_targets as targets
from comsol_mcp import _windows_t037_wfp as wfp
from comsol_mcp import _windows_t037_mic as mic
from tests.test_windows_host_preflight_owner import Function
from tests._prepared_launch_software_fixture import _REAL_FINITE_POLICY_INIT


class Exports:
    def __init__(self,sdk):self.sdk=sdk;self.functions={}
    def __getattr__(self,name):
        if name not in self.functions:
            method=getattr(self.sdk,name,None)
            if method is None:
                for kind in ('Provider','SubLayer','Filter','ProviderContext','Layer'):
                    if name=='Fwpm'+kind+'CreateEnumHandle0':method=lambda *a,k=kind:self.sdk.create_enum(k,*a)
                    elif name=='Fwpm'+kind+'Enum0':method=lambda *a,k=kind:self.sdk.enum(k,*a)
                    elif name=='Fwpm'+kind+'DestroyEnumHandle0':method=lambda *a,k=kind:self.sdk.destroy_enum(k,*a)
                    elif name=='Fwpm'+kind+'GetSecurityInfoByKey0':method=lambda *a,k=kind:self.sdk.security(k,*a)
                    if method is not None:break
            if method is None:
                def unexpected(*args):raise AssertionError('unpermitted inert WFP export '+name)
                method=unexpected
            self.functions[name]=Function(method)
        return self.functions[name]


class CatalogSdk:
    def __init__(self,env,snapshot):
        self.env=env;self.snapshot=copy.deepcopy(snapshot);self.calls=[];self.keep=[];self.live={};self.next=100
        self.app_id=b'public-synthetic-app-id';self.dll=Exports(self);self.last_error=0
        self.descriptors={(t.kind,t.key):bytes.fromhex(t.descriptor_hex) for t in targets.require_contract(env.profile).policy}
        host=env.profile.host_resource_profile
        self.markers={a['filter_key']:wfp.CtypesWindowsWfpMutationApi._owned_marker(None,host,a)
            for a in env._software_policy_actions if a['resource_kind']=='wfp_filter'}

    def pointer(self,value):self.keep.append(value);return C.pointer(value)
    def string(self,text):
        if text is None:return None
        value=C.create_string_buffer((text+'\0').encode('utf-16-le'));self.keep.append(value);return C.addressof(value)
    def load(self,name,**options):
        assert name.casefold().removesuffix('.dll') in {'advapi32','kernel32','fwpuclnt','shell32'}
        self.calls.append(('bind_sdk',name));return self.dll
    def blob(self,raw):
        value=(n.BYTE*len(raw)).from_buffer_copy(raw);self.keep.append(value)
        return n.FWP_BYTE_BLOB(len(raw),C.cast(value,C.POINTER(n.BYTE)))
    def allocate(self,value,extras=()):
        address=C.addressof(value);assert address not in self.live
        self.live[address]=(value,extras);return address
    def setptr(self,out,address):C.cast(out,C.POINTER(C.c_void_p))[0]=address
    def value(self,row,typ=n.FWP_VALUE0):
        value=typ();kind=row['type'];value.type=kind
        if kind==0:return value
        if kind in (1,2,3):setattr(value,{1:'uint8',2:'uint16',3:'uint32'}[kind],row['value'])
        elif kind==4:value.uint64=self.pointer(C.c_uint64(row['value']))
        elif kind==12:
            assert row['sha256']==hashlib.sha256(self.app_id).hexdigest() and row['size']==len(self.app_id)
            value.byteBlob=self.pointer(self.blob(self.app_id))
        elif kind==13:
            fields=row['value'].split('-');raw=bytes((1,len(fields)-3))+int(fields[2]).to_bytes(6,'big')+b''.join(int(x).to_bytes(4,'little') for x in fields[3:])
            holder=C.create_string_buffer(raw);self.keep.append(holder);value.sid=C.addressof(holder)
        elif kind==256:value.v4AddrMask=C.cast(self.pointer(n.FWP_V4_ADDR_AND_MASK(row['address_u32'],row['mask_u32'])),C.c_void_p)
        elif kind==257:
            obj=n.FWP_V6_ADDR_AND_MASK();obj.addr[:]=bytes.fromhex(row['address']);obj.prefixLength=row['prefix_length']
            value.v6AddrMask=C.cast(self.pointer(obj),C.c_void_p)
        elif kind==258:
            obj=n.FWPM_RANGE0(self.value(row['low']),self.value(row['high']))
            value.rangeValue=C.cast(self.pointer(obj),C.c_void_p)
        else:raise AssertionError('unimplemented fixture value type '+str(kind))
        return value
    def display(self,row):return n.FWPM_DISPLAY_DATA0(self.string(row['name']),self.string(row['description']))
    def row(self,kind,row):
        if kind=='Provider':
            value=n.FWPM_PROVIDER0();value.providerKey=wfp.guid_struct(row['provider_key']);value.serviceName=self.string(row['service_name'])
            value.providerData=self.blob(b'')
        elif kind=='SubLayer':
            value=n.FWPM_SUBLAYER0();value.subLayerKey=wfp.guid_struct(row['sublayer_key']);value.providerKey=self.pointer(wfp.guid_struct(row['provider_key']))
            value.weight=row['weight'];value.providerData=self.blob(b'')
        elif kind=='Layer':
            value=n.FWPM_LAYER0();value.layerKey=wfp.guid_struct(row['layer_key'])
            value.defaultSubLayerKey=wfp.guid_struct(row['default_sublayer_key']);value.layerId=row['layer_id']
            fields=(n.FWPM_FIELD0*len(row['fields']))()
            for out,field in zip(fields,row['fields']):out.fieldKey=self.pointer(wfp.guid_struct(field['field_key']));out.type=field['field_type'];out.dataType=field['data_type']
            self.keep.append(fields);value.numFields=len(fields);value.field=fields
        elif kind=='Filter':
            value=n.FWPM_FILTER0();value.filterKey=wfp.guid_struct(row['filter_key'])
            value.providerKey=self.pointer(wfp.guid_struct(row['provider_key']));value.providerData=self.blob(self.markers[row['filter_key']])
            assert hashlib.sha256(self.markers[row['filter_key']]).hexdigest()==row['provider_data_sha256']
            value.layerKey=wfp.guid_struct(row['layer_key']);value.subLayerKey=wfp.guid_struct(row['sublayer_key'])
            value.weight=self.value(row['weight']);value.effectiveWeight=self.value(row['effective_weight'])
            value.filterId=row['filter_id'];value.action.type=row['action_type'];value.rawContext=row['context']['raw_context']
            conditions=(n.FWPM_FILTER_CONDITION0*len(row['conditions']))()
            for out,condition in zip(conditions,row['conditions']):
                out.fieldKey=wfp.guid_struct(condition['field_key']);out.matchType=condition['match_type']
                out.conditionValue=self.value(condition['condition_value'],n.FWP_CONDITION_VALUE0)
            self.keep.append(conditions);value.numFilterConditions=len(conditions);value.filterCondition=conditions
        else:raise AssertionError(kind)
        value.flags=row['flags'];value.displayData=self.display(row['display_data']);self.keep.append(value)
        return value

    def FwpmEngineOpen0(self,_name,_auth,_identity,_session,out):
        self.next+=1;self.calls.append(('engine_open',self.next));self.setptr(out,self.next);return 0
    def FwpmEngineClose0(self,engine):self.calls.append(('engine_close',engine.value));return 0
    def FwpmTransactionBegin0(self,engine,flags):self.calls.append(('txn_begin',flags));return 0
    def FwpmTransactionCommit0(self,engine):self.calls.append(('txn_commit',));return 0
    def FwpmTransactionAbort0(self,engine):self.calls.append(('txn_abort',));return 0
    def create_enum(self,kind,engine,template,out):
        assert template is None;self.next+=1;self.setptr(out,self.next);self.calls.append(('enum_create',kind,self.next));return 0
    def enum(self,kind,engine,handle,page,out,count):
        field={'Provider':'providers','SubLayer':'sublayers','Filter':'filters','ProviderContext':'provider_contexts','Layer':'layers'}[kind]
        rows=self.snapshot[field];assert len(rows)<page
        self.calls.append(('enum',kind,len(rows)))
        C.cast(count,C.POINTER(n.DWORD))[0]=len(rows)
        if not rows:self.setptr(out,None);return 0
        values=[self.row(kind,r) for r in rows];typ=type(values[0]);array=(C.POINTER(typ)*len(values))(*(C.pointer(v) for v in values))
        self.setptr(out,self.allocate(array,values));return 0
    def destroy_enum(self,kind,engine,handle):self.calls.append(('enum_close',kind,handle.value));return 0
    def security(self,kind,engine,key,flags,owner,group,dacl,sacl,out):
        assert flags==7 and owner is group is dacl is sacl is None
        guid=n.CtypesWindowsPolicyApi._guid(None,C.cast(key,C.POINTER(n.GUID)).contents)
        raw=self.descriptors[kind,wfp.guid_key(guid)];holder=C.create_string_buffer(raw)
        pointer=self.allocate(holder);self.env.sdk.buffers[pointer]=(holder,raw)
        self.calls.append(('security',kind,guid));self.setptr(out,pointer);return 0
    def FwpmGetAppIdFromFileName0(self,path,out):
        obj=self.blob(self.app_id);self.calls.append(('appid',path));self.setptr(out,self.allocate(obj));return 0
    def FwpmFreeMemory0(self,out):
        pointer=C.cast(out,C.POINTER(C.c_void_p)).contents.value
        assert pointer in self.live;self.calls.append(('free',pointer));self.live.pop(pointer);self.setptr(out,None)
    def LocalFree(self,ptr):
        pointer=getattr(ptr,'value',ptr);assert pointer in self.live
        self.calls.append(('local_free',pointer));self.live.pop(pointer);return None
    def VirtualQuery(self,*args):return self.env.sdk.region(*args)
    def GetSecurityDescriptorLength(self,*args):return self.env.sdk.length(*args)
    def IsValidSid(self,ptr):return int(bool(ptr))
    def ConvertSidToStringSidW(self,ptr,out):
        address=getattr(ptr,'value',ptr);head=C.string_at(address,8)
        text='S-1-'+str(int.from_bytes(head[2:8],'big'))+''.join('-'+str(x) for x in struct.unpack('<'+'I'*head[1],C.string_at(address+8,head[1]*4)))
        holder=C.create_unicode_buffer(text);self.setptr(out,self.allocate(holder));return 1

    # The same actual FinitePolicyApi also performs its static SCM read.
    # Its original parser/query/close bodies execute; these are only exports.
    def OpenSCManagerW(self,*args):self.calls.append(('scm_open',));return 7001
    def OpenServiceW(self,manager,name,access):
        assert name==self.env.profile.service_name;self.calls.append(('service_open',name));return 7002
    def CloseServiceHandle(self,handle):self.calls.append(('service_close',getattr(handle,'value',handle)));return 1
    def QueryServiceConfigW(self,service,buffer,capacity,used):
        class Config(C.Structure):
            _fields_=[('kind',n.DWORD),('start',n.DWORD),('error',n.DWORD),
                ('binary',C.c_void_p),('group',C.c_void_p),('tag',C.c_void_p),
                ('dependencies',C.c_void_p),('account',C.c_void_p),('display',C.c_void_p)]
        argv=list(self.env.installer.intent.argv)
        values=[('binary',subprocess.list2cmdline(argv)),('account',self.env.profile.service_start_name),
                ('group',''),('dependencies','\0'),('display','Source-only SCM exports')]
        strings=[(name,C.create_string_buffer((value+'\0').encode('utf-16-le'))) for name,value in values]
        required=C.sizeof(Config)+sum(C.sizeof(value) for _,value in strings)
        C.cast(used,C.POINTER(n.DWORD))[0]=required;self.last_error=122 if buffer is None else 0
        if buffer is None:return 0
        assert capacity>=required
        row=Config.from_buffer(buffer);row.kind=16;row.start=3;row.error=1
        offset=C.sizeof(Config)
        for name,value in strings:
            C.memmove(C.addressof(buffer)+offset,C.addressof(value),C.sizeof(value))
            setattr(row,name,C.addressof(buffer)+offset);offset+=C.sizeof(value)
        return 1
    def QueryServiceConfig2W(self,service,kind,buffer,capacity,used):
        assert kind==5;C.cast(used,C.POINTER(n.DWORD))[0]=4;self.last_error=122 if buffer is None else 0
        if buffer is None:return 0
        assert capacity==4;C.cast(buffer,C.POINTER(n.DWORD))[0]=3;return 1
    def QueryServiceStatusEx(self,service,kind,out,size,used):
        assert size==36
        row=C.cast(out,C.POINTER(n.DWORD));row[0]=16;row[1]=4;row[7]=os.getpid()
        C.cast(used,C.POINTER(n.DWORD))[0]=36;return 1
    def QueryServiceObjectSecurity(self,service,flags,buffer,capacity,used):
        raw=next(iter(self.descriptors.values()))
        C.cast(used,C.POINTER(n.DWORD))[0]=len(raw);self.last_error=122 if buffer is None else 0
        if buffer is None:return 0
        assert capacity>=len(raw);C.memmove(buffer,raw,len(raw));return 1
    def LookupAccountNameW(self,system,name,buffer,length,domain,domain_length,use):
        text=self.env.profile.service_sid if name.startswith('NT SERVICE') else self.env.profile.service_account_sid
        fields=text.split('-');raw=bytes((1,len(fields)-3))+int(fields[2]).to_bytes(6,'big')+b''.join(int(x).to_bytes(4,'little') for x in fields[3:])
        C.cast(length,C.POINTER(n.DWORD))[0]=len(raw);C.cast(domain_length,C.POINTER(n.DWORD))[0]=1
        C.cast(use,C.POINTER(n.DWORD))[0]=1;self.last_error=122 if buffer is None else 0
        if buffer is None:return 0
        C.memmove(buffer,raw,len(raw));domain.value='';return 1
    def CommandLineToArgvW(self,text,count):
        argv=list(self.env.installer.intent.argv);assert text==subprocess.list2cmdline(argv)
        strings=[C.create_string_buffer((item+'\0').encode('utf-16-le')) for item in argv]
        array=(C.c_void_p*len(strings))(*(C.addressof(item) for item in strings))
        self.allocate(array,strings);C.cast(count,C.POINTER(C.c_int32))[0]=len(strings)
        return C.cast(array,C.POINTER(C.c_void_p))


def install(driver):
    env=driver.env;m=env.monkeypatch
    # These are inert SDK input catalogs from the existing finite fixture.
    # All consuming production readers below are restored, not replaced.
    seed=targets.FinitePolicyApi(env.profile).observe_wfp_policy_snapshot()
    for index,row in enumerate(seed['filters']):row['filter_id']=1001+index
    sdk=CatalogSdk(env,seed);driver.catalog_sdk=sdk;driver.policy_readers=[]
    m.setattr(n,'os',NS(**dict(vars(n.os),name='nt')));m.setattr(n,'sys',NS(platform='win32'))
    # Module-local ctypes SDK facade: other original SCM/token API fixtures
    # retain their own captured exports and UTF-16 buffer contracts.
    m.setattr(n,'C',NS(**dict(vars(C),WinDLL=sdk.load,get_last_error=lambda:sdk.last_error)))
    def initialize(api,profile):
        # FinitePolicyApi's original body calls the original full ctypes
        # constructor/bind/layout path. Only its DLL loader returns SDK exports.
        _REAL_FINITE_POLICY_INIT(api,profile)
        driver.policy_readers.append(api)
    m.setattr(targets.FinitePolicyApi,'__init__',initialize)
    def snapshot(profile):return targets.FinitePolicyApi(profile).observe_wfp_policy_snapshot()
    driver.policy_snapshot=snapshot
