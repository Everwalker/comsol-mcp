"""Finite A SOFTWARE_ONLY SDK boundaries; no host, child or issuer effects."""
from contextlib import contextmanager
from dataclasses import replace
from pathlib import PureWindowsPath
from types import SimpleNamespace as NS
import copy
import ctypes as C
import gc
import hashlib
import json
import ntpath
import os
import socket
import struct
import subprocess
import uuid

import pytest

from comsol_mcp import _windows_t037 as win, _windows_t037_targets as targets
from comsol_mcp import _windows_t037_admission as admission, _windows_t037_mic as mic
from comsol_mcp import _windows_job_limits as limits, _windows_owned_resources as owned
from comsol_mcp import _windows_parameter_publish as publish, _windows_t037_native as native
from comsol_mcp import _windows_filter_group as group, _w26_os_resources as resources
from comsol_mcp._execution_contract import ExecutionContractError
from tests.test_windows_network_intent import effective_group_fixture, network_profile_and_plan
from tests.test_windows_filter_group import GroupSDK
from tests.test_windows_scm_prebirth import running_factory_chain, static_sdk
from tests.test_windows_t037_mic import label_ace, descriptor as label_descriptor, Function
from comsol_mcp import _control_daemon as control, _windows_t037_resume as resume


@pytest.fixture(autouse=True)
def software_only(monkeypatch):
    def denied(*a, **k): pytest.fail('finite A software controls must not enter native SDK/Popen/RPC/remote')
    monkeypatch.setattr(C, 'WinDLL', denied, raising=False)
    monkeypatch.setattr(subprocess, 'Popen', denied)
    monkeypatch.setattr(subprocess, 'run', denied)
    monkeypatch.setattr(socket, 'create_connection', denied)
    monkeypatch.setattr(targets, '_ORIGINS', {})
    monkeypatch.setattr(targets, '_READS', {})
    monkeypatch.setattr(targets, '_HELD', {})
    monkeypatch.setattr(targets, '_LOADER_APIS', {})
    monkeypatch.setattr(mic, '_retained', {})


def sid(text):
    parts = list(map(int, text.split('-')[1:]))
    return bytes((parts[0], len(parts)-2)) + parts[1].to_bytes(6, 'big') + b''.join(v.to_bytes(4, 'little') for v in parts[2:])


def sd(owner='S-1-5-18', *, extra=None, include_group=True, system_mask=0x1F01FF):
    owner_bytes = sid(owner); system = sid('S-1-5-18')
    aces = [struct.pack('<BBHI', 0, 0, 8+len(system), system_mask) + system]
    if extra:
        subject, mask = extra; raw = sid(subject)
        aces.append(struct.pack('<BBHI', 0, 0, 8+len(raw), mask) + raw)
    acl = struct.pack('<BBHHH', 2, 0, 8+sum(map(len, aces)), len(aces), 0) + b''.join(aces)
    group_bytes = system if include_group else b''
    return struct.pack('<BBHIIII', 1, 0, 0x9004, 20, 20+len(owner_bytes) if include_group else 0, 0,
                       20+len(owner_bytes)+len(group_bytes)) + owner_bytes + group_bytes + acl


def wfp_sd(subject):
    # Exact official fwpmu.h FWPM_GENERIC_ALL and READ_CONTROL|ACTRL_READ;
    # file access masks and WFP access masks are deliberately kept separate.
    return sd(extra=(subject,0x20080),system_mask=0xF07FF)


def case(*, service_leaf_write=False, child_acl=False):
    original, plan = network_profile_and_plan()
    contents = {i.path: (i.role+'-software-bytes').encode() for i in original.launch_images}
    host = replace(original, root=replace(original.root, path=r'C:\Projects\workspace',
        volume_id='55', file_id='222', owner_id='S-1-5-18'),
        service_account_id='S-1-5-18',launcher_account_id='S-1-5-18',
        launch_images=tuple(replace(i, image_sha256=hashlib.sha256(contents[i.path]).hexdigest()) for i in original.launch_images),
        file_grants=(resources.PathGrant(r'C:\Projects\workspace',('READ',)),
            resources.PathGrant(r'C:\Projects\workspace\data',('READ','WRITE')),
            resources.PathGrant(r'C:\Projects\workspace\temp',('READ','WRITE'))),
        windows_isolation=replace(original.windows_isolation,service_start_name='LocalSystem',service_logon_sid='S-1-5-18',acl_grants=tuple(
            replace(g,path=r'C:\Projects\workspace',security_descriptor_sha256=
                native.canonical_security_descriptor_sha256(sd()) if child_acl else g.security_descriptor_sha256)
            for g in original.windows_isolation.acl_grants)))
    raw = sd().hex(); files = []
    for path, roles in ((r'C:\COMSOL\install', ['installation_rx']), (r'C:\COMSOL\code', ['code_rx']),
            (r'C:\COMSOL\config', ['config_rx']), (r'C:\COMSOL\runtime', ['runtime_rx']),
            (host.root.path, ['project_root']), (host.root.path+r'\data', ['data_leaf']),
            (host.root.path+r'\temp', ['temp_leaf'])):
        leaf_raw = sd(extra=(host.windows_isolation.service_sid, 0x12019F)).hex() if service_leaf_write and set(roles) & {'data_leaf', 'temp_leaf'} else raw
        files.append(dict(path=path, kind='directory', roles=roles, descriptor_hex=leaf_raw,
                          mandatory_label=dict(rid=0x2000, mask=1, flags=0), content_sha256=None))
    for image in host.launch_images:
        files.append(dict(path=image.path, kind='file', roles=[image.role+'_image'], descriptor_hex=raw,
            mandatory_label=dict(rid=0x2000, mask=1, flags=0), content_sha256=image.image_sha256))
    policy = host.windows_isolation
    policy_raw=wfp_sd(policy.service_sid).hex()
    protection = [dict(kind='Provider', key=policy.wfp_provider_key, descriptor_hex=policy_raw),
                  dict(kind='SubLayer', key=policy.wfp_sublayer_key, descriptor_hex=policy_raw)]
    protection += [dict(kind='Filter', key=k, descriptor_hex=policy_raw) for k in policy.wfp_filter_keys]
    document = dict(schema=win.PROFILE_SCHEMA_V4, host_resource_profile=host.as_dict(),
        job_limits=limits.JobLimitsV1(4096, 16384, 4).as_dict(), finite_targets=dict(schema=targets.SCHEMA,
            filesystem_security=targets.FS_SECURITY, policy_security=targets.WFP_SECURITY, files=files, policy=protection))
    payload = json.dumps(document, sort_keys=True).encode()
    source_paths = ['c:\\',r'c:\comsol',r'c:\comsol\config',r'c:\comsol\config\profile.json']
    from comsol_mcp._host_scoped_provider import _canonical as private_canonical
    path_chain = [dict(identity=dict(device=55,inode=999 if path.endswith('profile.json') else 300+sum(path.encode())),
        descriptor_sha256=hashlib.sha256(sd(include_group=False)).hexdigest(),owner='daemon') for path in source_paths]
    source_chain_sha = hashlib.sha256(private_canonical(path_chain)).hexdigest()
    profile = win.parse_profile_v4_document(document, source_sha256=hashlib.sha256(payload).hexdigest(),
        source_identity=dict(device=55, inode=999, security_sha256=source_chain_sha), security_semantics='SOFTWARE_ONLY')
    contents[r'C:\COMSOL\config\profile.json'] = payload
    # Recompute the configured group after the host intent changed; never use
    # an old group hash as a fixture bypass for the real effective parser.
    actions = copy.deepcopy(plan.as_dict()['change_manifest'])
    members = [a['desired'] for a in actions if a['resource_kind']=='wfp_filter']
    fingerprint = group.group_fingerprint(host, members)
    for m in members: m['group_sha256'] = fingerprint
    _old, api, snapshot = effective_group_fixture()
    builder = GroupSDK(host)
    normalized = group.normalize_actions(dict(change_manifest=actions), host, 'f'*64)
    snapshot['filters'][:] = [builder._row(host, a) for a in normalized if a['resource_kind']=='wfp_filter']
    security_hash = native.canonical_security_descriptor_sha256(bytes.fromhex(policy_raw))
    for row in snapshot['filters']: row['security_sha256'] = security_hash
    snapshot['providers'].append(dict(provider_key=policy.wfp_provider_key, display_data=dict(name=None,description=None),
        flags=1, provider_data_sha256=hashlib.sha256(b'').hexdigest(), service_name=profile.service_name, security_sha256=security_hash))
    snapshot['sublayers'].append(dict(sublayer_key=policy.wfp_sublayer_key, display_data=dict(name=None,description=None),
        flags=1, provider_key=policy.wfp_provider_key, provider_data_sha256=hashlib.sha256(b'').hexdigest(), weight=100, security_sha256=security_hash))
    return document, profile, contents, api, snapshot


class PathSdk:
    """Real ctypes storage and parsers; only fixed OS exports are software."""
    def __init__(self, document, contents, fault=None):
        self.fault=fault; self.calls=[]; self.handles={}; self.positions={}; self.buffers={}; self.free_calls=[]
        self.rows={ntpath.normcase(t['path']):t for t in document['finite_targets']['files']}
        self.contents={ntpath.normcase(k):v for k,v in contents.items()}
        self.path_api=None; self.sd_slots=[]; self.api=None; self.full_reads={}; self.sd_reads={}
        self.k=NS(); self.a=NS(); self.nt=NS()
        for name, fn in (('CreateFileW',self.root),('CloseHandle',self.close),('GetFileInformationByHandle',self.file),
                ('GetFileInformationByHandleEx',self.full),('ReadFile',self.read),('LocalFree',self.free),
                ('VirtualQuery',self.region),('GetFileType',lambda h:1),('SetFilePointerEx',self.rewind)):
            setattr(self.k,name,Function(fn))
        for name in ('WriteFile','FlushFileBuffers','SetFileInformationByHandle','GetFileAttributesW','DeviceIoControl'):
            setattr(self.k,name,Function(lambda *a:pytest.fail('unexpected mutable/unused path export')))
        for name,fn in (('GetSecurityInfo',self.security),('GetSecurityDescriptorLength',self.length),
                ('GetSecurityDescriptorControl',self.control),('GetSecurityDescriptorSacl',self.sacl)):
            setattr(self.a,name,Function(fn))
        self.nt.NtCreateFile=Function(self.relative)
        self.nt.NtSetInformationFile=Function(lambda *a:pytest.fail('no mutable native file operation'))
        self.nt.RtlNtStatusToDosError=Function(lambda status:5)

    def alloc_handle(self, path):
        handle=1000+len(self.handles); self.handles[handle]=ntpath.normcase(path); self.positions[handle]=0
        self.calls.append(('open',handle,self.handles[handle])); return handle
    def root(self,path,access,share,security,disposition,flags,template):
        assert (access,share,security,disposition,flags,template)==(0x20000,3,None,3,0x2200000,None)
        if self.fault=='root_return_lost': raise OSError('software root return lost')
        return self.alloc_handle(path)
    def relative(self,output,access,attributes,io_status,allocation,attrs,share,disposition,options,ea,size):
        value=C.cast(attributes,C.POINTER(publish._ObjectAttributes)).contents
        name=value.ObjectName.contents
        leaf=C.wstring_at(name.Buffer,name.Length//2)
        path=ntpath.join(self.handles[value.RootDirectory],leaf)
        if self.fault=='relative_error_zero':return -1
        C.cast(output,C.POINTER(C.c_void_p))[0]=self.alloc_handle(path)
        C.cast(io_status,C.POINTER(publish._IoStatusBlock)).contents.Information=1
        assert disposition==1 and not ea and size==0 and options&0x200000
        assert access&0x20000 or path.endswith('profile.json')  # original legacy loader open_source is data-only
        if self.fault=='relative_written_raise':raise OSError('software output HANDLE written then SDK boundary raises')
        if self.fault=='relative_return_bool':return True
        if self.fault=='relative_pending':return 0x103
        return 0
    def identity(self,path):
        return 999 if path.endswith('profile.json') else 222 if path==r'c:\projects\workspace' else 300+sum(path.encode())
    def row(self,handle):
        path=self.handles[getattr(handle,'value',handle)]
        row=self.rows.get(path,dict(kind='file' if path.endswith('profile.json') else 'directory',descriptor_hex=sd().hex()))
        attrs=0x10 if row['kind']=='directory' else 0x20
        if self.fault=='kind' and path.endswith('launcher.exe'):attrs=0x10
        if self.fault=='reparse' and path.endswith('launcher.exe'):attrs|=0x400
        return path,row,attrs
    def file(self,handle,output):
        path,row,attrs=self.row(handle); out=C.cast(output,C.POINTER(mic._FileInfo)).contents
        out.attributes=attrs;out.volume=55;out.id_low=self.identity(path);out.creation=100;out.write=300
        out.size_low=len(self.contents.get(path,b''));out.links=2 if self.fault=='links' else 1
        return 1
    def full(self,handle,kind,output,size):
        path,row,attrs=self.row(handle);count=self.full_reads.get(path,0)+1;self.full_reads[path]=count
        if kind==18:
            out=C.cast(output,C.POINTER(publish._FullIdInfo)).contents;out.volume=55
            identity=self.identity(path)+(1 if self.fault=='identity_drift' and count>3 else 0)
            out.file_id[:]=identity.to_bytes(16,'little')
        elif kind==0:
            out=C.cast(output,C.POINTER(publish._BasicInfo)).contents
            out.created=100;out.changed=200;out.written=300;out.attributes=attrs
        elif kind==1:
            out=C.cast(output,C.POINTER(publish._StandardInfo)).contents
            out.size=len(self.contents.get(path,b''));out.links=2 if self.fault=='links' else 1;out.directory=int(bool(attrs&0x10))
        else:pytest.fail('unexpected FileInformationClass')
        return 1
    def read(self,handle,buffer,size,count,overlapped):
        path=self.handles[getattr(handle,'value',handle)];position=self.positions[getattr(handle,'value',handle)]
        data=self.contents.get(path,b'')[position:position+size]
        if self.fault=='truncated' and data:data=b''
        C.memmove(buffer,data,len(data));C.cast(count,C.POINTER(C.c_uint32))[0]=len(data)
        self.positions[getattr(handle,'value',handle)]+=len(data);return 1
    def rewind(self,handle,offset,output,method):
        assert (offset,output,method)==(0,None,0)
        self.positions[getattr(handle,'value',handle)]=0
        return 1
    def security(self,handle,kind,flags,owner,group_,dacl,sacl,output):
        path,row,attrs=self.row(handle); assert kind==1 and flags in (5,7,0x10)
        holder=self.api._finite_pending_sd if flags in (5,7) else self.api.pending
        assert holder is not None and C.addressof(holder.storage)==C.addressof(C.cast(output,C.POINTER(C.c_void_p)).contents)
        raw=label_descriptor(label_ace(0x2000)) if flags==0x10 else sd(include_group=False) if flags==5 else bytes.fromhex(row['descriptor_hex'])
        key=path,flags;self.sd_reads[key]=self.sd_reads.get(key,0)+1
        if self.fault=='label' and path.endswith('launcher.exe'):raw=label_descriptor(label_ace(0x3000)) if flags==0x10 else raw
        if self.fault=='source_label' and path.endswith('profile.json') and flags==0x10:raw=label_descriptor(label_ace(0x3000))
        if self.fault=='label_absent' and flags==0x10:raw=label_descriptor(control=0x8000,offset=0)
        if self.fault=='legacy_source_security' and flags==5:raw=sd(extra=('S-1-1-0',0x120089),include_group=False)
        if self.fault=='sd_drift' and flags==7 and self.sd_reads[key]>1:raw=sd(extra=('S-1-1-0',0x120089))
        if self.fault=='source_security' and path.endswith('profile.json') and flags==7:raw=sd(extra=('S-1-1-0',0x120089))
        buffer=C.create_string_buffer(raw);pointer=C.addressof(buffer);self.buffers[pointer]=(buffer,raw)
        C.cast(output,C.POINTER(C.c_void_p))[0]=pointer;self.sd_slots.append(pointer)
        if self.fault=='getter_written_raise':raise OSError('software SD out pointer written then exception')
        return 5 if self.fault=='getter_error' else 0
    def length(self,pointer):return len(self.buffers[getattr(pointer,'value',pointer)][1])
    def control(self,pointer,control,revision):
        raw=self.buffers[pointer][1];C.cast(control,C.POINTER(C.c_uint16))[0]=struct.unpack_from('<H',raw,2)[0]
        C.cast(revision,C.POINTER(C.c_uint32))[0]=1;return 1
    def sacl(self,pointer,present,acl,defaulted):
        C.cast(present,C.POINTER(C.c_int32))[0]=1;C.cast(acl,C.POINTER(C.c_void_p))[0]=pointer+20
        C.cast(defaulted,C.POINTER(C.c_int32))[0]=0;return 1
    def region(self,pointer,output,capacity):
        pointer=getattr(pointer,'value',pointer)
        match=next(((base,b) for base,(b,raw) in self.buffers.items() if base<=pointer<base+C.sizeof(b)),None)
        if match is None:return 0
        base,buffer=match;out=C.cast(output,C.POINTER(mic._MemoryInfo)).contents
        out.base=base;out.allocation_base=base;out.size=C.sizeof(buffer);out.state=0x1000;out.protect=4
        return C.sizeof(out)
    def free(self,pointer):
        self.free_calls.append(pointer)
        if self.fault=='free_written_raise':raise OSError('software free possibly effective')
        return None
    def close(self,handle):
        self.calls.append(('close',getattr(handle,'value',handle)))
        if self.fault=='close_written_raise':raise OSError('software CloseHandle possibly effective')
        return 1


class CatalogSdk:
    """SOFTWARE exports; production transaction/enum/row/SD parsers stay real."""
    def __init__(self,api,profile,snapshot,paths):
        self.api=api;self.profile=profile;self.snapshot=snapshot;self.paths=paths
        self.kept=[];self.events=[];self.queries=[];self.freed=[];self.pages={};self.sd_reads={};self.sd_allocations=[];self.snapshots=[]
        api._fwpu=self;api.k=api._kernel32=paths.k;api.a=api._advapi=paths.a
        paths.a.IsValidSid=Function(lambda pointer:1)
        paths.a.ConvertSidToStringSidW=Function(self.sid_text)
        self.types={'Provider':native.FWPM_PROVIDER0,'SubLayer':native.FWPM_SUBLAYER0,
            'Filter':native.FWPM_FILTER0,'ProviderContext':native.FWPM_PROVIDER_CONTEXT0,'Layer':native.FWPM_LAYER0}
        self.catalogs=dict(Provider='providers',SubLayer='sublayers',Filter='filters',ProviderContext='provider_contexts',Layer='layers')
    def keep(self,value):self.kept.append(value);return value
    def text(self,value):
        return None if value is None else C.addressof(self.keep(C.create_string_buffer(value.encode('utf-16-le')+b'\0\0')))
    def guid(self,value):return self.keep(native.GUID.from_buffer_copy(uuid.UUID(value.strip('{}')).bytes_le))
    def value(self,row,kind=native.FWP_CONDITION_VALUE0):
        result=kind();result.type=tag=row['type']
        if tag in (1,2,3):setattr(result,{1:'uint8',2:'uint16',3:'uint32'}[tag],row['value'])
        elif tag==4:result.uint64=C.pointer(self.keep(C.c_uint64(row['value'])))
        elif tag==12:
            raw=b'public-synthetic-app-id';assert (len(raw),hashlib.sha256(raw).hexdigest())==(row['size'],row['sha256'])
            data=self.keep((native.BYTE*len(raw)).from_buffer_copy(raw))
            result.byteBlob=C.pointer(self.keep(native.FWP_BYTE_BLOB(len(raw),data)))
        elif tag==13:result.sid=C.addressof(self.keep(C.create_string_buffer(sid(row['value']))))
        elif tag==256:result.v4AddrMask=C.addressof(self.keep(native.FWP_V4_ADDR_AND_MASK(row['address_u32'],row['mask_u32'])))
        elif tag==258:
            interval=native.FWPM_RANGE0(self.value(row['low'],native.FWP_VALUE0),self.value(row['high'],native.FWP_VALUE0))
            result.rangeValue=C.addressof(self.keep(interval))
        else:pytest.fail('unsupported software fixture value '+str(tag))
        return result
    def sid_text(self,pointer,output):
        count=C.c_ubyte.from_address(pointer+1).value;subject=targets._sid(C.string_at(pointer,8+count*4))
        text=self.keep(C.create_unicode_buffer(subject))
        C.cast(output,C.POINTER(C.c_void_p))[0]=C.addressof(text);return 1
    def record(self,kind,row):
        result=self.types[kind]();result.flags=row['flags']
        result.displayData.name=self.text(row['display_data']['name']);result.displayData.description=self.text(row['display_data']['description'])
        if kind=='Provider':
            result.providerKey=self.guid(row['provider_key']);result.serviceName=self.text(row['service_name'])
        elif kind=='SubLayer':
            result.subLayerKey=self.guid(row['sublayer_key']);result.weight=row['weight']
            if row['provider_key']:result.providerKey=C.pointer(self.guid(row['provider_key']))
        elif kind=='Layer':
            result.layerKey=self.guid(row['layer_key']);result.defaultSubLayerKey=self.guid(row['default_sublayer_key']);result.layerId=row['layer_id']
            fields=self.keep((native.FWPM_FIELD0*len(row['fields']))(*[
                native.FWPM_FIELD0(C.pointer(self.guid(r['field_key'])),r['field_type'],r['data_type']) for r in row['fields']]))
            result.numFields=len(fields);result.field=fields
        else:
            assert kind=='Filter' and not row['has_provider_context']
            result.filterKey=self.guid(row['filter_key']);result.layerKey=self.guid(row['layer_key'])
            result.subLayerKey=self.guid(row['sublayer_key']);result.filterId=row['filter_id']
            if row['provider_key']:result.providerKey=C.pointer(self.guid(row['provider_key']))
            result.action.type=row['action_type'];result.rawContext=row['context']['raw_context']
            result.weight=self.value(row['weight'],native.FWP_VALUE0);result.effectiveWeight=self.value(row['effective_weight'],native.FWP_VALUE0)
            conditions=self.keep((native.FWPM_FILTER_CONDITION0*len(row['conditions']))(*[
                native.FWPM_FILTER_CONDITION0(self.guid(r['field_key']),r['match_type'],self.value(r['condition_value'])) for r in row['conditions']]))
            result.numFilterConditions=len(conditions);result.filterCondition=conditions
        return self.keep(result)  # providerData is an actual empty SDK blob in this fixture.
    def FwpmEngineOpen0(self,*args):
        self.events.append('open');C.cast(args[-1],C.POINTER(native.HANDLE))[0]=native.HANDLE(700);return 0
    def FwpmEngineClose0(self,*args):self.events.append('close');return 0
    def FwpmTransactionBegin0(self,engine,flags):
        assert flags==native.CtypesWindowsPolicyApi.FWPM_TXN_READ_ONLY
        self.events.append('begin-readonly');return 0
    def FwpmTransactionCommit0(self,*args):self.events.append('commit');return 0
    def FwpmTransactionAbort0(self,*args):self.events.append('abort');return 0
    def FwpmFreeMemory0(self,output):
        self.freed.append(C.cast(output,C.POINTER(C.c_void_p))[0]);C.cast(output,C.POINTER(C.c_void_p))[0]=None
    def __getattr__(self,name):
        if name.endswith('CreateEnumHandle0'):
            kind=name[4:-len('CreateEnumHandle0')]
            def create(engine,template,output):
                handle=800+len(self.pages);self.pages[handle]=kind
                C.cast(output,C.POINTER(native.HANDLE))[0]=native.HANDLE(handle);self.events.append(('create',kind));return 0
            return create
        if name.endswith('DestroyEnumHandle0'):
            kind=name[4:-len('DestroyEnumHandle0')]
            def destroy(*args):self.events.append(('destroy',kind));return 0
            return destroy
        if name.endswith('Enum0'):
            kind=name[4:-len('Enum0')]
            def enum(engine,handle,maximum,output,count):
                assert self.pages[handle.value]==kind;self.events.append(('enum',kind))
                records=[self.record(kind,row) for row in self.snapshot[self.catalogs[kind]]]
                C.cast(count,C.POINTER(native.DWORD))[0]=len(records)
                if records:
                    pointers=self.keep((C.POINTER(self.types[kind])*len(records))(*[C.pointer(r) for r in records]))
                    C.cast(output,C.POINTER(C.c_void_p))[0]=C.addressof(pointers)
                return 0
            return enum
        if name.endswith('GetSecurityInfoByKey0'):
            kind=name[4:-len('GetSecurityInfoByKey0')]
            def security(engine,key,flags,owner,group_,dacl,sacl,output):
                identity=(kind,targets.guid_key(self.api._guid(C.cast(key,C.POINTER(native.GUID)).contents)))
                self.queries.append(identity)
                assert identity in self.api._finite_expected_keys,'foreign SD getter must not be entered'
                assert flags==7 and owner is group_ is dacl is sacl is None
                target=next(t for t in self.profile.finite_targets.policy if (t.kind,t.key)==identity)
                raw=bytes.fromhex(target.descriptor_hex);fault=getattr(self.paths,'policy_fault',None)
                self.sd_reads[identity]=self.sd_reads.get(identity,0)+1
                if fault=='drift' and self.sd_reads[identity]>1:raw=wfp_sd('S-1-1-0')
                buffer=self.keep(C.create_string_buffer(raw));pointer=C.addressof(buffer);self.paths.buffers[pointer]=(buffer,raw)
                self.sd_allocations.append(pointer)
                C.cast(output,C.POINTER(C.c_void_p))[0]=pointer
                if fault=='written_raise':raise OSError('SOFTWARE owned SD written then lost return')
                return 5 if fault=='getter_error' else 0
            return security
        raise AssertionError('unexpected SDK export '+name)


def setup_reader(monkeypatch, document, contents, *, owner=None, fault=None):
    sdk=PathSdk(document,contents,fault)
    monkeypatch.setattr(publish,'os',NS(**dict(vars(os),name='nt')))
    monkeypatch.setattr(mic,'os',NS(name='nt'))
    monkeypatch.setattr(C,'WinDLL',lambda name,**kw: sdk.a if name=='advapi32' else sdk.nt if name=='ntdll' else sdk.k,raising=False)
    reader=targets.NativeTargetReader(owner);sdk.api=reader.mic;sdk.path_api=reader.api
    return reader,sdk


def test_v4_same_exact_wrapper_limits_truthful_hash_and_no_origin_from_parser():
    document,p,*_=case()
    assert type(p) is win.WindowsT037ProfileV2 and p.wrapper_schema==win.PROFILE_SCHEMA_V4
    assert limits.require_limits(p) is p.job_limits and type(p.job_limits) is limits.JobLimitsV1
    assert p.as_safe_dict()['schema']==win.PROFILE_SCHEMA_V4 and p.as_safe_dict()['verification_state']=='NOT_VERIFIED'
    assert not targets._ORIGINS and p.sha256!=p.host_resource_profile.sha256
    changed=copy.deepcopy(document);changed['finite_targets']['files'][0]['mandatory_label']['rid']=0x3000
    assert win.parse_profile_v4_document(changed,source_sha256='b'*64).sha256!=p.sha256


@pytest.mark.parametrize('fault',['missing_role','kind','duplicate','label','unknown','no_sd','broad_write','self_hash','missing_job','old_schema'])
def test_closed_v4_intent_negative_controls(fault):
    document,p,*_=case();row=document['finite_targets']['files'][0]
    if fault=='missing_role':document['finite_targets']['files'].pop(0)
    elif fault=='kind':row['kind']='file'
    elif fault=='duplicate':document['finite_targets']['files'].append(copy.deepcopy(row))
    elif fault=='label':row['mandatory_label']['mask']=0
    elif fault=='unknown':row['verified']=True
    elif fault=='no_sd':row['descriptor_hex']='00'
    elif fault=='broad_write':row['descriptor_hex']=sd(extra=('S-1-1-0',0x10000000)).hex()
    elif fault=='self_hash':document['source_sha256']='f'*64
    elif fault=='missing_job':document['job_limits']=None
    else:document['schema']=win.PROFILE_SCHEMA_V3
    with pytest.raises(ExecutionContractError):win.parse_profile_v4_document(document,source_sha256='b'*64)
    assert not targets._ORIGINS and not targets._READS


def test_actual_readonly_file_reader_sdk_layout_bytes_kind_hash_mic_and_cleanup(monkeypatch):
    document,p,contents,*_=case();reader,sdk=setup_reader(monkeypatch,document,contents)
    fact=reader.read(p.launch_images[0].path,directory=False,content=True)
    assert fact['content_sha256']==p.launch_images[0].image_sha256 and fact['descriptor']==sd()
    assert fact['label'].ace.rid==0x2000 and fact['label'].state=='UNVERIFIED'
    assert fact['fingerprint'][0]==(55,sdk.identity(ntpath.normcase(p.launch_images[0].path)))
    assert sdk.free_calls and not any(c[0]=='close' for c in sdk.calls)
    reader.finish()
    assert not reader.registry.unresolved() and not targets._READS
    assert len([c for c in sdk.calls if c[0]=='open'])==len([c for c in sdk.calls if c[0]=='close'])
    calls=list(sdk.calls);reader.finish()
    with pytest.raises(ExecutionContractError):reader.read(p.launch_images[0].path,directory=False,content=True)
    assert sdk.calls==calls


@pytest.mark.parametrize('fault',['kind','reparse','links','identity_drift','sd_drift','truncated',
    'getter_error','getter_written_raise','relative_written_raise','relative_return_bool','relative_pending','relative_error_zero',
    'root_return_lost','free_written_raise','close_written_raise'])
def test_real_reader_error_boundaries_original_owner_unknown_no_native_retry(monkeypatch,fault):
    document,p,contents,*_=case();reader,sdk=setup_reader(monkeypatch,document,contents,fault=fault)
    with pytest.raises(Exception):
        try:reader.read(p.launch_images[0].path,directory=False,content=True)
        finally:reader.finish()
    unknown=[e for e in reader.registry.unresolved() if e.state=='UNKNOWN']
    if fault in {'getter_error','getter_written_raise','relative_written_raise','relative_return_bool','relative_pending','root_return_lost','free_written_raise','close_written_raise'}:
        assert unknown and targets._READS[id(reader)] is reader
        if fault in {'getter_error','getter_written_raise'}:
            assert sdk.free_calls==[]  # Failed/lost getter output never transferred allocation ownership.
        effects=(list(sdk.calls),list(sdk.free_calls))
        with pytest.raises(Exception):reader.finish()
        assert (sdk.calls,sdk.free_calls)==effects
        for entry in unknown:
            if entry.label.startswith('finite_') and 'descriptor' in entry.label or fault=='close_written_raise':
                with pytest.raises(Exception):reader.registry.retry_owned(entry,reader.owner)
                assert (sdk.calls,sdk.free_calls)==effects
    else:
        assert not reader.registry.unresolved() and not targets._READS


@contextmanager
def full_chain(monkeypatch,tmp_path,*,service_leaf_write=False,catalog_sdk=False,child_acl=False):
    document,p,contents,wfp,snapshot=case(service_leaf_write=service_leaf_write,child_acl=child_acl)
    daemon=control.ControlDaemon(tmp_path/'control',registry={})
    daemon._windows_t037_profile=p
    factory=daemon._new_t037_session_factory(p.host_resource_profile.project_id,'finite-session')
    authority=daemon.project_authority
    # Actual original ProjectAuthority/SQLite record. Only the Windows path
    # lstat/resolve boundary is synthetic on macOS; native root identity is
    # independently read through actual ctypes path/MIC reader functions.
    record=dict(schema_version=1,project_id=p.host_resource_profile.project_id,workspace=p.workspace_root,revision=1)
    with authority.store.lock:
        authority.store.db.execute('INSERT INTO projects(project_id,workspace,schema_version,revision,record_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
            (record['project_id'],record['workspace'],1,1,json.dumps(record),'software','software'))
    monkeypatch.setattr(authority,'_verify_workspace_binding',lambda path:None)
    monkeypatch.setattr(win,'os',NS(**dict(vars(os),name='nt')))
    Adapter=native.WindowsNativePolicyAdapter
    events,process_sdk=static_sdk(monkeypatch,p)
    actual=native.WindowsNativePolicyAdapter()
    service=actual.api.observe_service
    monkeypatch.setattr(native,'WindowsNativePolicyAdapter',Adapter)
    def finite_policy_boundary(self,profile):
        # Explicit SOFTWARE ONLY catalog SDK boundary. Getter allocation/ABI
        # logic has separate raw ctypes tests below, never native acceptance.
        self._finite_profile=profile
        self._finite_expected_keys={(t.kind,t.key) for t in profile.finite_targets.policy}
        if catalog_sdk:
            self._finite_security_reads={};exports=CatalogSdk(self,profile,snapshot,sdk);sdk.catalogs.append(exports)
            original=self.observe_wfp_policy_snapshot
            def capture():
                result=original();exports.snapshots.append(result);return result
            self.observe_wfp_policy_snapshot=capture
        else:
            self._finite_security_reads={(t.kind,t.key):native.canonical_security_descriptor_sha256(bytes.fromhex(t.descriptor_hex)) for t in profile.finite_targets.policy}
            self.observe_wfp_policy_snapshot=lambda:copy.deepcopy(wfp.observe_wfp_policy_snapshot())
        self.observe_application_id=wfp.observe_application_id
        self.observe_service=service
    monkeypatch.setattr(targets.FinitePolicyApi,'__init__',finite_policy_boundary)
    sdk=PathSdk(document,contents);sdk.catalogs=[]
    monkeypatch.setattr(publish,'os',NS(**dict(vars(os),name='nt')))
    monkeypatch.setattr(mic,'os',NS(name='nt'))
    monkeypatch.setattr(C,'WinDLL',lambda name,**kw:sdk.a if name=='advapi32' else sdk.nt if name=='ntdll' else sdk.k,raising=False)
    original_init=targets.NativeTargetReader.__init__
    def reader_init(self,owner=None):
        original_init(self,owner);sdk.api=self.mic;sdk.path_api=self.api
    monkeypatch.setattr(targets.NativeTargetReader,'__init__',reader_init)
    path=r'C:\COMSOL\config\profile.json'
    targets._retain_loaded_origin(p,path)  # Actual production source reader over the explicit SDK exports above.
    try:
        with running_factory_chain(daemon,factory,tmp_path,monkeypatch,profile_path=path):
            yield daemon,factory,sdk,snapshot,events
    finally:
        daemon.close()


@pytest.mark.parametrize('service_leaf_write',[False,True])
def test_full_original_SCM_daemon_factory_A_success_then_original_Job_and_B_issuer_refusal(monkeypatch,tmp_path,service_leaf_write):
    from tests.test_windows_job_limits import api as job_api
    with full_chain(monkeypatch,tmp_path,service_leaf_write=service_leaf_write) as (daemon,factory,sdk,snapshot,events):
        assert factory.profile.service_sid != factory.profile.service_account_sid == 'S-1-5-18'
        for t in factory.profile.finite_targets.files:
            if set(t.roles) & {'data_leaf','temp_leaf'}:
                assert t.descriptor_hex==(sd(extra=(factory.profile.service_sid,0x12019F)) if service_leaf_write else sd()).hex()
        assert factory.preflight() is None
        assert factory._job is None and factory._api is None and not owned.registry_for(factory).unresolved()
        api=job_api();factory._api=api
        api.hash_image=lambda path:factory.profile.server_image_sha256
        def B_boundary(command,options,*,profile,job,role,session_factory):
            assert session_factory is factory and job==300 and role=='server'
            limits.require_configured_job(api,job,profile)
            # Unimplemented child SDK is not simulated as B passed. Exercise
            # the unchanged actual issuer refusal; no capability registration.
            resume.issue_native_resume(None,profile,None,None)
        api.create_suspended_appcontainer_child=B_boundary
        with pytest.raises(ExecutionContractError) as error:factory([factory.profile._launch_image('server').path])
        assert error.value.code=='NATIVE_CHILD_PROOF_UNAVAILABLE'
        assert api.k.calls[:4]==['create',('query',300,9,144),('set',300,9,144),('query',300,9,144)]
        assert not resume._pending and not targets._READS
        factory.close()
        assert api.k.calls.count(('close',300))==1


@pytest.mark.parametrize('fault',[None,'extra_filter','extra_sublayer','missing_filter','missing_provider','getter_error','written_raise','drift'])
def test_original_SCM_static_chain_real_five_catalog_parser_keeps_foreign_rows_without_SD_and_rejects_own_faults(monkeypatch,tmp_path,fault):
    from comsol_mcp._windows_t037_effective import observe_effective_wfp
    with full_chain(monkeypatch,tmp_path,service_leaf_write=True,catalog_sdk=True) as (daemon,factory,sdk,snapshot,events):
        foreign=lambda n:'{'+str(uuid.UUID(int=41000+n)).upper()+'}'
        provider=copy.deepcopy(snapshot['providers'][0]);provider.update(provider_key=foreign(1),service_name='UNRELATED')
        sublayer=copy.deepcopy(snapshot['sublayers'][0]);sublayer.update(sublayer_key=foreign(2),provider_key=foreign(1))
        row=copy.deepcopy(snapshot['filters'][0]);row.update(filter_key=foreign(3),filter_id=41003,provider_key=foreign(1),sublayer_key=foreign(2))
        snapshot['providers'].append(provider);snapshot['sublayers'].append(sublayer);snapshot['filters'].append(row)
        if fault=='extra_filter':row['provider_key']=factory.profile.windows_policy.wfp_provider_key
        elif fault=='extra_sublayer':sublayer['provider_key']=factory.profile.windows_policy.wfp_provider_key
        elif fault=='missing_filter':snapshot['filters'].pop(0)
        elif fault=='missing_provider':snapshot['providers'].pop(0)
        elif fault in {'getter_error','written_raise','drift'}:sdk.policy_fault=fault
        if fault is None:assert factory.preflight() is None
        else:
            with pytest.raises(Exception) as failure:factory.preflight()
            reason={'extra_filter':'FINITE_OWNED_POLICY_EXTRA_OBJECT','extra_sublayer':'FINITE_OWNED_POLICY_EXTRA_OBJECT',
                'missing_filter':'STATIC_OWNED_FOUR_LAYER_CONFIGURATION_MISMATCH','missing_provider':'FINITE_OWNED_POLICY_EXTRA_OBJECT',
                'getter_error':'FINITE_NATIVE_RESOURCE_UNKNOWN','written_raise':'FINITE_NATIVE_RESOURCE_UNKNOWN',
                'drift':'FINITE_OWNED_POLICY_SECURITY_CHANGED'}[fault]
            assert reason in failure.value.details['reasons']
        exports,=sdk.catalogs;api=exports.api
        expected={(t.kind,t.key) for t in factory.profile.finite_targets.policy}
        assert exports.queries and set(exports.queries)<=expected
        assert factory._job is None and factory._api is None and not resume._pending
        if fault in {'getter_error','written_raise'}:
            entries=[e for e in owned.registry_for(factory).unresolved() if e.domain=='finite_sd']
            assert len(entries)==1 and entries[0].state=='UNKNOWN'
            holder=entries[0].keepalive[0]
            assert entries[0].adapter is api and entries[0].error is holder.error
            assert not holder.getter_succeeded and not holder.entered and not holder.confirmed
            if fault=='written_raise':assert isinstance(holder.error,OSError)
            else:assert 'FINITE_SECURITY_GETTER_FAILED' in holder.error.details['reasons']
            assert not set(exports.sd_allocations).intersection(exports.freed)
            before=(list(exports.events),list(exports.queries),list(exports.freed),list(sdk.calls))
            with pytest.raises(Exception):factory.preflight()
            with pytest.raises(Exception):factory.close()
            assert (exports.events,exports.queries,exports.freed,sdk.calls)==before
        else:
            assert not owned.registry_for(factory).unresolved()
            assert set(exports.sd_allocations)<=set(exports.freed)
        if fault is None:
            assert len(exports.snapshots)==2 and set(api._finite_security_reads)==expected
            assert len(exports.queries)==2*len(expected)
            for observed in exports.snapshots:
                assert observed['counts']['pages_by_kind']==dict.fromkeys(('Provider','SubLayer','Filter','ProviderContext','Layer'),1)
                assert observed['counts']['providers']==2 and observed['counts']['sublayers']==2 and observed['counts']['filters']==8
                for name,key,identity in (('providers','provider_key',foreign(1)),('sublayers','sublayer_key',foreign(2)),('filters','filter_key',foreign(3))):
                    actual=next(r for r in observed[name] if targets.guid_key(r[key])==targets.guid_key(identity))
                    assert actual['security_sha256'] is None  # NOT_OBSERVED, not a fake digest or omitted row.
                    assert actual['provider_data_sha256']==hashlib.sha256(b'').hexdigest()
                external=next(r for r in observed['filters'] if targets.guid_key(r['filter_key'])==targets.guid_key(foreign(3)))
                for field in ('provider_key','sublayer_key','layer_key','filter_id','flags','action_type','context','conditions','weight','effective_weight','display_data'):
                    assert external[field]==row[field]
                compared=observe_effective_wfp(factory.profile,NS(observe_wfp_policy_snapshot=lambda:observed,observe_application_id=api.observe_application_id))
                assert compared.state=='UNVERIFIED' and set(compared.matched_filter_keys)=={key for kind,key in expected if kind=='Filter'}
                assert {'WFP_EXTERNAL_FILTER_ARBITRATION_UNRESOLVED','WFP_ENUMERATION_ACCESS_VISIBILITY_NOT_PROVEN','WFP_EFFECTIVE_ARBITRATION_NOT_PROVEN'}<=set(compared.reasons)
                targets.require_owned_policy(factory.profile,observed,api)
            assert exports.events.count('begin-readonly')==exports.events.count('commit')==exports.events.count('close')==2
            assert 'abort' not in exports.events


@pytest.mark.parametrize('fault',['wrong_project','wrong_root','root_identity','source_identity','source_path','source_bytes','source_security',
    'legacy_source_security','source_label','label_absent','source_oversize',
    'source_origin','copied_profile','kind','label','sd_drift','own_extra_filter','own_extra_sublayer','own_security',
    'getter_error','getter_written_raise','free_written_raise'])
def test_full_registered_chain_static_failures_never_enter_Job_child_or_Resume(monkeypatch,tmp_path,fault):
    with full_chain(monkeypatch,tmp_path) as (daemon,factory,sdk,snapshot,events):
        if fault=='wrong_project':
            oldkey=next(iter(daemon._session_t037_process_factories));key=('foreign-project',factory.session_id)
            daemon._session_t037_process_factories[key]=daemon._session_t037_process_factories.pop(oldkey)
            binding=factory._scm_binding;factory._scm_binding=(binding[0],binding[1],key,binding[3])
        elif fault=='wrong_root':
            authority=daemon.project_authority;pid=factory.profile.host_resource_profile.project_id
            with authority.store.lock:
                record=authority.get_project(pid);record['workspace']=r'C:\Projects\foreign'
                authority.store.db.execute('UPDATE projects SET workspace=?,record_json=? WHERE project_id=?',
                    (record['workspace'],json.dumps(record),pid))
        elif fault=='root_identity':
            original=sdk.identity;sdk.identity=lambda path:223 if path==r'c:\projects\workspace' else original(path)
        elif fault=='source_path':daemon._windows_t037_profile_path=r'C:\COMSOL\config\other.json'
        elif fault=='source_bytes':sdk.contents[r'c:\comsol\config\profile.json']+=b' '
        elif fault=='source_oversize':sdk.contents[r'c:\comsol\config\profile.json']=b'x'*65537
        elif fault=='source_security':sdk.fault='source_security'
        elif fault=='source_identity':
            original=sdk.identity;sdk.identity=lambda path:1000 if path.endswith('profile.json') else original(path)
        elif fault in {'legacy_source_security','source_label','label_absent'}:sdk.fault=fault
        elif fault=='source_origin':targets._ORIGINS.clear()
        elif fault=='copied_profile':
            # Even identical parsed bytes/value types cannot supply the actual
            # loader association. Preserve the old SCM identity guard too.
            daemon._windows_t037_profile=replace(factory.profile)
        elif fault in {'kind','sd_drift','getter_error','getter_written_raise','free_written_raise'}:sdk.fault=fault
        elif fault=='label':sdk.fault='label'
        elif fault=='own_extra_filter':
            row=copy.deepcopy(snapshot['filters'][0]);row['filter_key']='{00000000-0000-0000-0000-000000009999}'
            row['filter_id']=999;snapshot['filters'].append(row)
        elif fault=='own_extra_sublayer':
            row=copy.deepcopy(snapshot['sublayers'][0]);row['sublayer_key']='{00000000-0000-0000-0000-000000009999}'
            snapshot['sublayers'].append(row)
        elif fault=='own_security':snapshot['filters'][0]['security_sha256']='e'*64
        with pytest.raises(Exception):factory.preflight()
        assert factory._job is None and factory._api is None and not resume._pending
        if fault in {'getter_error','getter_written_raise','free_written_raise'}:
            registry=owned.registry_for(factory);assert registry.unresolved()
            before=(list(sdk.calls),list(sdk.free_calls))
            with pytest.raises(Exception):factory.preflight()
            with pytest.raises(Exception):factory.close()
            assert (sdk.calls,sdk.free_calls)==before


@pytest.mark.parametrize('payload',[None,{},NS(path=r'C:\COMSOL\config\profile.json',verified=True)])
def test_caller_metadata_does_not_mint_loaded_source_or_static_pass(monkeypatch,tmp_path,payload):
    with full_chain(monkeypatch,tmp_path) as (daemon,factory,sdk,snapshot,events):
        targets._ORIGINS.clear()
        object.__setattr__(factory.profile,'source_origin',payload)
        with pytest.raises(ExecutionContractError) as error:factory.preflight()
        assert 'FINITE_PROFILE_SOURCE_ORIGIN_REQUIRED' in error.value.details['reasons']
        assert factory._job is None and factory._api is None and not resume._pending


@pytest.mark.parametrize('fault',[None,'getter_error','written_raise','free_raise','malformed','length','pointer','drift'])
def test_wfp_actual_captured_getter_correct_allocator_canonical_semantics_and_unknown_owner(monkeypatch,fault):
    document,p,contents,*_=case();sdk=PathSdk(document,contents)
    api=targets.FinitePolicyApi.__new__(targets.FinitePolicyApi)
    api.k=sdk.k;api.a=sdk.a;api._advapi=sdk.a;api._kernel32=sdk.k
    api._finite_profile=p;api._finite_expected_keys={(t.kind,t.key) for t in p.finite_targets.policy}
    api._finite_security_reads={};calls=[];allocations=[]
    def get(engine,key,flags,owner,group_,dacl,sacl,output):
        assert engine==77 and flags==7 and owner is group_ is dacl is sacl is None
        holder=api._finite_pending_sd;assert holder is not None
        assert C.addressof(holder.storage)==C.addressof(C.cast(output,C.POINTER(C.c_void_p)).contents)
        raw=wfp_sd('S-1-1-0') if fault=='drift' and allocations else wfp_sd(p.service_sid)
        if fault=='malformed':raw=bytes((2,))+raw[1:]
        buffer=C.create_string_buffer(raw);pointer=C.addressof(buffer);sdk.buffers[pointer]=(buffer,raw)
        allocations.append(pointer);C.cast(output,C.POINTER(C.c_void_p))[0]=1 if fault=='pointer' else pointer
        if fault=='written_raise':raise OSError('SOFTWARE WFP SD output written then exception')
        return 5 if fault=='getter_error' else 0
    def free(slot):
        pointer=C.cast(slot,C.POINTER(C.c_void_p))[0];calls.append(pointer)
        if fault=='free_raise':raise OSError('SOFTWARE FwpmFreeMemory possibly effective')
        C.cast(slot,C.POINTER(C.c_void_p))[0]=None
    api._fwpu=NS(FwpmFilterGetSecurityInfoByKey0=get,FwpmFreeMemory0=free)
    if fault=='length':sdk.a.GetSecurityDescriptorLength=Function(lambda p:24)
    key=native.GUID.from_buffer_copy(uuid.UUID(p.windows_policy.wfp_filter_keys[0]).bytes_le)
    if fault is None:
        value=api._wfp_security_sha(77,'Filter',key)
        assert value==native.canonical_security_descriptor_sha256(wfp_sd(p.service_sid))
        assert value!=hashlib.sha256(wfp_sd(p.service_sid)).hexdigest() and not owned.registry_for(api).unresolved()
    elif fault=='drift':
        api._wfp_security_sha(77,'Filter',key)
        with pytest.raises(ExecutionContractError):api._wfp_security_sha(77,'Filter',key)
        assert not owned.registry_for(api).unresolved()
    else:
        with pytest.raises(Exception):api._wfp_security_sha(77,'Filter',key)
        unknown=owned.registry_for(api).unresolved()
        if fault in {'getter_error','written_raise','pointer','free_raise'}:
            assert len(unknown)==1 and unknown[0].state=='UNKNOWN'
            if fault in {'getter_error','written_raise'}:assert calls==[]
            before=list(calls)
            with pytest.raises(Exception):owned.registry_for(api).retry_owned(unknown[0],api)
            assert calls==before
        else:assert not unknown
    assert sdk.free_calls==[]  # WFP allocation must never use LocalFree.


@pytest.mark.parametrize('family',['local','wfp'])
@pytest.mark.parametrize('outcome',['error_status','lost_return','bool_status','wrapped_status','none_status'])
def test_failed_or_opaque_SD_return_retains_original_storage_bindings_cause_and_never_queries_or_frees(monkeypatch,family,outcome):
    adapter=NS();query=NS();argument=NS();args=(argument,)
    buffer=C.create_string_buffer(64);address=C.addressof(buffer)
    returned={'error_status':5,'lost_return':None,'bool_status':False,
              'wrapped_status':C.c_uint32(0),'none_status':None}[outcome]
    failure=OSError('SOFTWARE original getter return lost');calls=[];frees=[];queries=[]
    def getter(actual,output):
        assert actual is argument
        holder=adapter._finite_pending_sd
        assert holder.function is getter and holder.args is args and holder.query is query
        assert C.addressof(holder.storage)==C.addressof(C.cast(output,C.POINTER(C.c_void_p)).contents)
        calls.append(output);C.cast(output,C.POINTER(C.c_void_p))[0]=address
        if outcome=='lost_return':raise failure
        return returned
    def free(pointer):
        frees.append(pointer)
        pytest.fail('an unsuccessful getter did not transfer this output allocation')
    def readable(*args):
        queries.append(args)
        pytest.fail('readability cannot recover a missing ownership transfer')
    monkeypatch.setattr(mic.NativeFileMicApi,'_readable',staticmethod(readable))
    with pytest.raises(Exception) as original:
        targets._read_sd(adapter,query,getter,args,closer=free,family=family)
    registry=owned.registry_for(adapter);entry,=registry.unresolved();holder=entry.keepalive[0]
    assert entry.state=='UNKNOWN' and entry.value==address and holder.storage.value==address
    assert entry.error is holder.error is original.value and holder.status is returned
    assert not holder.getter_succeeded and not holder.entered and not holder.confirmed
    assert holder.function is getter and holder.args is args and holder.query is query and holder.closer is free
    assert any(value is holder.storage for value in entry.keepalive)
    if outcome=='lost_return':assert original.value is failure
    gc.collect()
    with pytest.raises(Exception) as retry:registry.retry_owned(entry,adapter)
    assert retry.value is original.value
    with pytest.raises(Exception) as replacement:
        targets._read_sd(adapter,query,getter,args,closer=free,family=family)
    assert replacement.value is original.value
    assert registry.unresolved()==(entry,) and entry.error is original.value
    assert len(calls)==1 and not frees and not queries


@pytest.mark.parametrize('family',['local','wfp'])
def test_lost_SD_return_with_invalid_partial_output_keeps_getter_first_cause_and_pending_storage(monkeypatch,family):
    adapter=NS();query=NS();failure=OSError('SOFTWARE original getter raised before return')
    invalid=C.c_void_p(-1).value;calls=[]
    def getter(output):
        calls.append(1);C.cast(output,C.POINTER(C.c_void_p))[0]=invalid
        raise failure
    def unexpected(*args):pytest.fail('invalid partial output must not be queried or freed')
    monkeypatch.setattr(mic.NativeFileMicApi,'_readable',staticmethod(unexpected))
    with pytest.raises(Exception):targets._read_sd(adapter,query,getter,(),closer=unexpected,family=family)
    registry=owned.registry_for(adapter);entry,=registry.unresolved();holder=adapter._finite_pending_sd
    assert entry.state=='UNKNOWN' and entry.value is None and entry.handle is holder.storage
    assert holder.value==holder.storage.value==invalid and holder.error is failure
    assert holder.function is getter and holder.query is query and not holder.getter_succeeded
    assert any(value is holder for value in entry.keepalive)
    with pytest.raises(Exception):registry.retry_owned(entry,adapter)
    with pytest.raises(Exception):targets._read_sd(adapter,query,getter,(),closer=unexpected,family=family)
    assert calls==[1] and registry.unresolved()==(entry,) and adapter._finite_pending_sd is holder


@pytest.mark.parametrize('fault',['malformed','length'])
def test_known_successful_LocalFree_allocation_with_invalid_descriptor_releases_exactly_once(monkeypatch,fault):
    document,p,contents,*_=case();sdk=PathSdk(document,contents)
    adapter=NS();query=NS(k=sdk.k,a=sdk.a);raw=sd()
    if fault=='malformed':raw=bytes((2,))+raw[1:]
    buffer=C.create_string_buffer(raw);address=C.addressof(buffer);sdk.buffers[address]=(buffer,raw)
    if fault=='length':sdk.a.GetSecurityDescriptorLength=Function(lambda pointer:24)
    captured=[]
    def getter(output):
        captured.append(adapter._finite_pending_sd)
        C.cast(output,C.POINTER(C.c_void_p))[0]=address
        return 0
    with pytest.raises(Exception) as error:
        targets._read_sd(adapter,query,getter,(),closer=sdk.free,family='local')
    holder,=captured
    assert holder.getter_succeeded and holder.readable and holder.entered and holder.confirmed
    assert holder.error is error.value and sdk.free_calls==[address]
    assert not owned.registry_for(adapter).unresolved() and adapter._finite_pending_sd is None


@pytest.mark.parametrize('role',sorted(targets._ROLES-{'data_leaf','temp_leaf'}))
def test_exact_service_SID_cannot_write_protected_targets(role):
    document,p,*_=case(service_leaf_write=True)
    row=next(t for t in document['finite_targets']['files'] if role in t['roles'])
    row['descriptor_hex']=sd(extra=(p.service_sid,0x12019F)).hex()
    with pytest.raises(ExecutionContractError) as error:win.parse_profile_v4_document(document,source_sha256='1'*64)
    assert 'FINITE_DACL_UNTRUSTED_WRITE' in error.value.details['reasons']


@pytest.mark.parametrize('role',['data_leaf','temp_leaf'])
def test_writable_leaf_does_not_admit_an_unconfigured_service_SID(role):
    document,p,*_=case(service_leaf_write=True)
    row=next(t for t in document['finite_targets']['files'] if role in t['roles'])
    foreign='S-1-5-80-999'
    assert foreign not in {p.service_sid,p.service_account_sid,p.appcontainer_sid}
    row['descriptor_hex']=sd(extra=(foreign,0x12019F)).hex()
    with pytest.raises(ExecutionContractError) as error:win.parse_profile_v4_document(document,source_sha256='1'*64)
    assert 'FINITE_DACL_UNTRUSTED_WRITE' in error.value.details['reasons']


def test_trusted_loader_retains_actual_origin_without_recursive_hash_and_caller_parse_does_not(monkeypatch):
    document,p,contents,*_=case();sdk=PathSdk(document,contents)
    monkeypatch.setattr(win,'os',NS(**dict(vars(os),name='nt')))
    monkeypatch.setattr(win,'Path',PureWindowsPath)
    monkeypatch.setattr(publish,'os',NS(**dict(vars(os),name='nt')))
    monkeypatch.setattr(mic,'os',NS(name='nt'))
    monkeypatch.setattr(C,'WinDLL',lambda name,**kw:sdk.a if name=='advapi32' else sdk.nt if name=='ntdll' else sdk.k,raising=False)
    monkeypatch.setattr(C,'get_last_error',lambda:0,raising=False)
    monkeypatch.setattr(C,'WinError',lambda code=0:OSError(code,'SOFTWARE Win32 error'),raising=False)
    import comsol_mcp._host_scoped_provider as provider
    semantics='windows-read-control-owner-dacl-persistent-acls'
    monkeypatch.setattr(provider,'verify_private_path_security',lambda *a,**k:dict(identity=p.source_identity,
        security_sha256=p.source_identity['security_sha256'],security_semantics=semantics))
    init=targets.NativeTargetReader.__init__
    def reader_init(self,owner=None):init(self,owner);sdk.api=self.mic
    monkeypatch.setattr(targets.NativeTargetReader,'__init__',reader_init)
    loaded=win.load_trusted_profile(r'C:\COMSOL\config\profile.json',host_fingerprint_reader=lambda:p.host_fingerprint_sha256)
    assert loaded.source_sha256==p.source_sha256 and loaded.wrapper_schema==win.PROFILE_SCHEMA_V4
    assert targets._ORIGINS[id(loaded)][0]() is loaded and id(p) not in targets._ORIGINS
    assert not targets._READS and not targets._LOADER_APIS and not owned.registry_for(sdk.api).unresolved()
    assert 'source_sha256' not in document and 'source_identity' not in document


def test_foreign_global_filter_churn_is_diagnostic_and_not_an_extra_finite_A_gate(monkeypatch,tmp_path):
    with full_chain(monkeypatch,tmp_path) as (daemon,factory,sdk,snapshot,events):
        init=targets.FinitePolicyApi.__init__
        def changing_sdk(self,profile):
            init(self,profile);read=self.observe_wfp_policy_snapshot;calls=[]
            def read_catalogs():
                calls.append(1)
                if len(calls)==2:
                    row=copy.deepcopy(snapshot['filters'][0]);row['filter_id']=999
                    row['filter_key']='{00000000-0000-0000-0000-000000009991}'
                    row['provider_key']='{00000000-0000-0000-0000-000000009992}'
                    row['sublayer_key']='{00000000-0000-0000-0000-000000009993}'
                    snapshot['filters'].append(row)
                return read()
            self.observe_wfp_policy_snapshot=read_catalogs
        monkeypatch.setattr(targets.FinitePolicyApi,'__init__',changing_sdk)
        assert factory.preflight() is None
        assert factory._job is None and factory._api is None and not resume._pending


@pytest.mark.parametrize('value',[None,{},NS(verified=True), 'v4'])
def test_require_limits_V4_keeps_exact_type_and_full_limit_validation(value):
    _,p,*_=case()
    with pytest.raises(ExecutionContractError):limits.require_limits(replace(p,job_limits=value))
    bad=limits.JobLimitsV1(4096,16384,4);object.__setattr__(bad,'active_process_limit',0)
    with pytest.raises(ExecutionContractError):limits.require_limits(replace(p,job_limits=bad))
    with pytest.raises(ExecutionContractError):limits.require_limits(NS(wrapper_schema=win.PROFILE_SCHEMA_V4,job_limits=p.job_limits))


@pytest.mark.parametrize('extra_right',[1,2,8,0x400,0x40000,0x80000,0x10000000])
def test_WFP_readable_intent_preserves_minimum_READ_and_rejects_foreign_management_right(extra_right):
    document,p,*_=case()
    raw=wfp_sd(p.service_sid);targets._descriptor(raw,wfp=True)
    assert targets.FWPM_ACTRL_READ==0x80 and targets.WFP_OBJECT_READ==0x20080
    row=document['finite_targets']['policy'][0]
    row['descriptor_hex']=sd(extra=(p.service_sid,0x20080|extra_right),system_mask=0xF07FF).hex()
    with pytest.raises(ExecutionContractError):win.parse_profile_v4_document(document,source_sha256='f'*64)


def test_original_private_source_composite_digest_and_daemon_owner_boundary_are_explicit(monkeypatch,tmp_path):
    from comsol_mcp._host_scoped_provider import _validate_windows_acl_policy
    with full_chain(monkeypatch,tmp_path) as (daemon,factory,sdk,snapshot,events):
        origin=targets._ORIGINS[id(factory.profile)][3]
        assert origin['source_chain_sha256']==factory.profile.source_identity['security_sha256']
        assert origin['source_chain_sha256']!=origin['legacy_security_sha256']
        assert origin['legacy_security_sha256']==hashlib.sha256(sd(include_group=False)).hexdigest()
        assert factory.profile.service_account_sid=='S-1-5-18'  # explicit fixture, not a production identity change
        assert factory.preflight() is None
    # Original helper's final owner requirement is stronger than mere TCB
    # ownership. A non-TCB daemon with SYSTEM-owned config needs Main's later
    # source-bootstrap/TCB decision, not a synthetic helper success here.
    with pytest.raises(ExecutionContractError):
        _validate_windows_acl_policy(owner_trusted=True,owner_is_daemon=False,require_daemon_owner=True,
            dacl_present=True,ace_rows=[])
