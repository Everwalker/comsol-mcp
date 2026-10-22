"""Private SCM leaf on the original policy API, registry and mutation holder."""
from __future__ import annotations
import ctypes as C
import hashlib,re
from ._windows_service_sid import fail,canonical
from ._w26_os_resources import MAX_HOST_PLAN_BYTES
from ._windows_host_preflight_owner import scalar
from ._windows_owned_resources import registry_for
from . import _windows_t037_native as native
from ._windows_sid import normalize_scm_account_name
D=C.c_uint32;H=C.c_void_p

class Config(C.Structure):
    _fields_=[('service_type',D),('start_type',D),('error_control',D),('binary',H),('load_group',H),('tag',D),('dependencies',H),('account',H),('display',H)]
class Status(C.Structure):
    _fields_=[(name,D) for name in ('service_type','state','controls','win32_exit','service_exit','checkpoint','wait_hint','process_id','flags')]
class SidInfo(C.Structure):_fields_=[('sid_type',D)]


def context(adapter):
    from ._windows_t037_wfp import CtypesWindowsWfpMutationApi
    if type(adapter) is not CtypesWindowsWfpMutationApi or type(adapter.api) is not native.CtypesWindowsPolicyApi:
        fail('HOST_CHANGE_SCM_OWNER_UNKNOWN','SCM leaf requires original concrete mutation and policy APIs')
    ctx=getattr(adapter,'_original_scm_leaf_context',None)
    if ctx is None:
        api=adapter.api;r=registry_for(adapter)
        from ._windows_owned_resources import attach_registry
        attach_registry(api,r)
        sdk={name:getattr(api._advapi,name) for name in ('OpenSCManagerW','OpenServiceW','CloseServiceHandle','QueryServiceConfigW','QueryServiceConfig2W','QueryServiceStatusEx','QueryServiceObjectSecurity','LookupAccountNameW','ChangeServiceConfig2W')}
        shell=C.WinDLL('shell32',use_last_error=True)
        shell.CommandLineToArgvW.argtypes=[C.c_wchar_p,C.POINTER(C.c_int32)];shell.CommandLineToArgvW.restype=C.POINTER(H)
        sdk['CommandLineToArgvW']=shell.CommandLineToArgvW;sdk['LocalFree']=api._kernel32.LocalFree
        sdk['LocalSize']=api._kernel32.LocalSize
        sdk['LocalSize'].argtypes=[H];sdk['LocalSize'].restype=C.c_size_t
        sdk['ChangeServiceConfig2W'].argtypes=[H,D,C.c_void_p];sdk['ChangeServiceConfig2W'].restype=C.c_int32
        ctx={'adapter':adapter,'api':api,'registry':r,'sdk':sdk,'geterror':C.get_last_error,'winerror':C.WinError,'first_cause':None,'pending_return':None}
        adapter._original_scm_leaf_context=ctx
    if ctx['adapter'] is not adapter or ctx['api'] is not adapter.api or ctx['registry'] is not registry_for(adapter) or registry_for(ctx['api']) is not ctx['registry'] or ctx['registry'].owner is not adapter:
        fail('HOST_CHANGE_SCM_OWNER_UNKNOWN','original SCM owner changed')
    return ctx


def remember(ctx,error):
    if ctx['first_cause'] is None:ctx['first_cause']=error
    if getattr(ctx['adapter'],'_first_mutation_cause',None) is None:ctx['adapter']._first_mutation_cause=error


def hold(ctx,entry,*storage):
    if entry.registry is not ctx['registry'] or entry.adapter is not ctx['api'] or ctx['registry']._entries.get(entry.seq) is not entry or entry.state!='ACQUIRED':
        fail('HOST_CHANGE_SCM_OWNER_UNKNOWN','SCM query requires original owned handle')
    entry.keepalive=(*entry.keepalive,*storage)


def own(ctx,value,*,local=False,keepalive=()):
    r=ctx['registry'];api=ctx['api'];address=getattr(value,'value',value)
    if not isinstance(value,(int,H)):address=C.cast(value,H).value
    if not address:return None
    closer=ctx['sdk']['LocalFree' if local else 'CloseServiceHandle'];entry=None
    def validate(actual):
        raw=getattr(actual,'value',actual)
        if type(raw) is not int or not 0<raw<H(-1).value:
            fail('HOST_CHANGE_SCM_OWNER_UNKNOWN','SCM SDK returned malformed pointer-width handle')
        return raw
    def release(actual):
        if ctx is not context(ctx['adapter']) or r._entries.get(entry.seq) is not entry or entry.adapter is not api or r.owner is not ctx['adapter'] or C.cast(actual,H).value!=address or address<=0 or address==H(-1).value:
            fail('HOST_CHANGE_SCM_OWNER_UNKNOWN','foreign/malformed SCM resource')
        result=closer(actual)
        if local:
            raw=getattr(result,'value',result)
            if raw is not None and (type(raw) is not int or raw!=0):fail('HOST_CHANGE_SCM_RELEASE_UNKNOWN','original LocalFree refused')
        elif scalar(result,'BOOL')==0:fail('HOST_CHANGE_SCM_RELEASE_UNKNOWN','original CloseServiceHandle refused')
    entry=r.acquire(api,value,release,mode='VOID',label='scm_leaf_local' if local else 'scm_leaf_handle',domain='scm_leaf_local' if local else 'scm_leaf_handle',keepalive=(value,ctx,*keepalive),value_reader=validate)
    return entry


def returned_call(ctx,name,*args):
    ctx['pending_return']=name
    result=ctx['sdk'][name](*args)
    ctx['pending_return']=None
    return result


def session(adapter,name,callback,*,change=False):
    ctx=context(adapter);r=ctx['registry'];scope=r.scope();first=None;answer=None
    with r._lock:
        if r.unresolved() or ctx['pending_return'] is not None:fail('HOST_CHANGE_SCM_OWNER_UNKNOWN','original unresolved acquisition/cleanup forbids another SCM session')
        try:
            manager=returned_call(ctx,'OpenSCManagerW',None,None,1);manager_entry=own(ctx,manager)
            if manager_entry is None:raise ctx['winerror'](ctx['geterror']())
            service=returned_call(ctx,'OpenServiceW',manager,name,1|4|0x20000|(2 if change else 0));entry=own(ctx,service)
            if entry is None:raise ctx['winerror'](ctx['geterror']())
            answer=callback(ctx,service,entry)
        except BaseException as error:first=error;remember(ctx,error)
        finally:
            for entry in reversed(tuple(e for e in r.unresolved() if e.seq>scope.seq)):
                if entry.state=='UNKNOWN':continue
                try:entry.release_owned(adapter)
                except BaseException as error:
                    remember(ctx,first or error)
                    if first is None:first=error
        if first is not None:raise first
    return answer


def buffer(ctx,entry,fn,minimum=1,maximum=8192):
    needed=D();hold(ctx,entry,needed)
    result=scalar(fn(None,0,C.byref(needed)),'BOOL')
    if result!=0 or ctx['geterror']()!=122 or not minimum<=needed.value<=maximum:fail('HOST_CHANGE_SCM_ABI_INVALID','unexpected SCM buffer size query')
    storage=C.create_string_buffer(needed.value);returned=D();hold(ctx,entry,storage,returned)
    result=scalar(fn(storage,len(storage),C.byref(returned)),'BOOL')
    if result==0:raise ctx['winerror'](ctx['geterror']())
    # pcbBytesNeeded is a failure-only output. The first genuine insufficient
    # buffer query and original owned allocation bound this successful payload.
    return storage,len(storage)


def text(storage,length,pointer,*,multi=False):
    if not pointer:return [] if multi else ''
    base=C.addressof(storage);offset=int(pointer)-base
    if offset<0 or offset>=length or offset%2:fail('HOST_CHANGE_SCM_ABI_INVALID','SCM string pointer outside original buffer')
    end=offset;parts=[];start=offset
    while end+2<=length:
        if storage.raw[end:end+2]==b'\0\0':
            try:value=storage.raw[start:end].decode('utf-16-le')
            except UnicodeError:fail('HOST_CHANGE_SCM_ABI_INVALID','invalid SCM UTF16 string')
            if not multi:return value
            if not value:return parts
            parts.append(value);start=end+2
        end+=2
    fail('HOST_CHANGE_SCM_ABI_INVALID','unterminated SCM string')


def account_sid(ctx,entry,account):
    account=normalize_scm_account_name(account)
    n=D();dn=D();use=D();hold(ctx,entry,n,dn,use)
    fn=ctx['sdk']['LookupAccountNameW']
    result=scalar(fn(None,account,None,C.byref(n),None,C.byref(dn),C.byref(use)),'BOOL')
    if result!=0 or ctx['geterror']()!=122 or not 8<=n.value<=68 or dn.value>32768:fail('HOST_CHANGE_SCM_ABI_INVALID','account SID size invalid')
    cap=n.value;dcap=dn.value;sid=C.create_string_buffer(cap);domain=C.create_unicode_buffer(max(1,dcap));hold(ctx,entry,sid,domain)
    if scalar(fn(None,account,sid,C.byref(n),domain,C.byref(dn),C.byref(use)),'BOOL')==0:raise ctx['winerror'](ctx['geterror']())
    raw=sid.raw
    if not 8<=n.value<=cap or dn.value>dcap or raw[0]!=1 or raw[1]>15 or n.value!=8+4*raw[1]:fail('HOST_CHANGE_SCM_ABI_INVALID','account SID returned extent invalid')
    return 'S-1-'+str(int.from_bytes(raw[2:8],'big'))+''.join('-'+str(int.from_bytes(raw[i:i+4],'little')) for i in range(8,n.value,4))


def argv(ctx,entry,binary):
    count=C.c_int32();hold(ctx,entry,count);output=None;resource=None
    try:
        try:output=returned_call(ctx,'CommandLineToArgvW',binary,C.byref(count))
        finally:
            if output:resource=own(ctx,C.cast(output,H),local=True,keepalive=(output,count,entry))
        if resource is None or not 1<=count.value<=min(4096,len(binary)+1):fail('HOST_CHANGE_SCM_ABI_INVALID','service argv invalid')
        size_result=ctx['sdk']['LocalSize'](C.cast(output,H))
        raw_size=getattr(size_result,'value',size_result)
        if type(raw_size) is not int or not count.value*C.sizeof(H)<=raw_size<=MAX_HOST_PLAN_BYTES:
            fail('HOST_CHANGE_SCM_ABI_INVALID','original argv allocation extent invalid')
        allocation=C.string_at(output,raw_size);hold(ctx,entry,allocation)
        base=C.cast(output,H).value;rows=[]
        for i in range(count.value):
            pointer=int.from_bytes(allocation[i*C.sizeof(H):(i+1)*C.sizeof(H)],'little')
            offset=pointer-base
            if not count.value*C.sizeof(H)<=offset<raw_size or offset%2:
                fail('HOST_CHANGE_SCM_ABI_INVALID','argv string pointer outside original local allocation')
            end=offset
            while end+2<=raw_size and allocation[end:end+2]!=b'\0\0':end+=2
            if end+2>raw_size:fail('HOST_CHANGE_SCM_ABI_INVALID','argv string unterminated in original allocation')
            try:rows.append(allocation[offset:end].decode('utf-16-le'))
            except UnicodeError:fail('HOST_CHANGE_SCM_ABI_INVALID','argv string UTF16 invalid')
        if sum(len(row) for row in rows)>len(binary):fail('HOST_CHANGE_SCM_ABI_INVALID','service argv exceeds original command line extent')
        return rows
    except BaseException as error:
        remember(ctx,error)
        raise
    finally:
        if resource:resource.release_owned(ctx['adapter'])


def subject(api,service,ctx,entry):
    if ctx['api'] is not api:fail('HOST_CHANGE_SCM_OWNER_UNKNOWN','private SCM reader wrong original API')
    sdk=ctx['sdk'];storage,length=buffer(ctx,entry,lambda b,n,out:sdk['QueryServiceConfigW'](service,b,n,out),C.sizeof(Config))
    config=Config.from_buffer(storage)
    row={key:int(getattr(config,key)) for key in ('service_type','start_type','error_control','tag')}
    for key,field in [('binary_path','binary'),('load_order_group','load_group'),('service_start_name','account'),('display_name','display')]:row[key]=text(storage,length,getattr(config,field))
    row['dependencies']=text(storage,length,config.dependencies,multi=True)
    if not row['binary_path'] or not row['service_start_name']:fail('HOST_CHANGE_SCM_ABI_INVALID','empty service identity')
    sid,size=buffer(ctx,entry,lambda b,n,out:sdk['QueryServiceConfig2W'](service,5,b,n,out),C.sizeof(SidInfo))
    if size!=C.sizeof(SidInfo):fail('HOST_CHANGE_SCM_ABI_INVALID','SERVICE_SID_INFO extent invalid')
    row['sid_type']=int(SidInfo.from_buffer(sid).sid_type)
    if row['sid_type'] not in (0,1,3):fail('HOST_CHANGE_SCM_ABI_INVALID','unsupported service SID bitmask')
    status=Status();returned=D();hold(ctx,entry,status,returned)
    if scalar(sdk['QueryServiceStatusEx'](service,0,C.byref(status),C.sizeof(status),C.byref(returned)),'BOOL')==0:raise ctx['winerror'](ctx['geterror']())
    # Success supplies the fixed SERVICE_STATUS_PROCESS; pcbBytesNeeded is
    # unspecified here and cannot be treated as an actual returned length.
    row['status']={key:int(getattr(status,key)) for key,_ in Status._fields_}
    security,sl=buffer(ctx,entry,lambda b,n,out:sdk['QueryServiceObjectSecurity'](service,7,b,n,out),20, 8192)
    try:row['service_security_sha256']=native.canonical_security_descriptor_sha256(security.raw[:sl])
    except ValueError:fail('HOST_CHANGE_SCM_ABI_INVALID','SCM security descriptor invalid')
    row['argv']=argv(ctx,entry,row['binary_path']);row['service_binary_image_path']=row['argv'][0]
    row['service_binary_argv_sha256']=hashlib.sha256(canonical(row['argv'])).hexdigest()
    row['service_start_sid']=account_sid(ctx,entry,row['service_start_name'])
    return row


def observe(adapter,name):
    return session(adapter,name,lambda ctx,service,entry:{**ctx['api']._service_sid_subject(service,ctx,entry),'service_name':name,'service_sid':account_sid(ctx,entry,'NT SERVICE\\'+name)})


def configure(adapter,name,expected,target):
    if target not in (0,1,3) or type(target) is not int:fail('HOST_CHANGE_SCM_TARGET_INVALID','SID configuration target invalid')
    def work(ctx,service,entry):
        def current():return {**ctx['api']._service_sid_subject(service,ctx,entry),'service_name':name,'service_sid':account_sid(ctx,entry,'NT SERVICE\\'+name)}
        before=current()
        if before!=expected:fail('HOST_CHANGE_SCM_SUBJECT_CHANGED','actual service changed before effect')
        selected=SidInfo(target);hold(ctx,entry,selected)
        if scalar(ctx['sdk']['ChangeServiceConfig2W'](service,5,C.byref(selected)),'BOOL')==0:raise ctx['winerror'](ctx['geterror']())
        after=current();desired={**before,'sid_type':target}
        if after!=desired:fail('HOST_CHANGE_SCM_READBACK_UNKNOWN','same original handle readback differs')
        manager=returned_call(ctx,'OpenSCManagerW',None,None,1);me=own(ctx,manager)
        if me is None:raise ctx['winerror'](ctx['geterror']())
        fresh=returned_call(ctx,'OpenServiceW',manager,name,1|4|0x20000);fe=own(ctx,fresh)
        if fe is None:raise ctx['winerror'](ctx['geterror']())
        got={**ctx['api']._service_sid_subject(fresh,ctx,fe),'service_name':name,'service_sid':account_sid(ctx,fe,'NT SERVICE\\'+name)}
        if got!=after:fail('HOST_CHANGE_SCM_READBACK_UNKNOWN','fresh handle service readback differs')
        return after
    return session(adapter,name,work,change=True)


def hash_image(adapter,path):
    # Reuse original no-reparse component traversal, with original private
    # preflight root holding every native handle before validation.
    from ._windows_parameter_publish import _API,_FileInfo
    from ._windows_host_preflight_owner import hold_original,finish_original
    from ._windows_service_sid import _read_original_backup
    api=_API();owner=hold_original(api);primary=None
    owners=getattr(adapter,"_original_service_sid_preflight_owners",None)
    if owners is None:owners=[];adapter._original_service_sid_preflight_owners=owners
    owners.append(owner)
    for name in ('CloseHandle','ReadFile','GetFileInformationByHandle'):owner.function(name,api.k)
    owner._retained_service_sid_read=True
    owner._original_get_last_error=C.get_last_error;owner._original_win_error=C.WinError
    try:
        with owner.registry._lock:
            handles=api.open_security_path(path,directory=False,read_data=True)
            def identities(chain):
                answer=[]
                for index,handle in enumerate(chain):
                    storage=_FileInfo();owner.hold(handle,storage)
                    if scalar(owner.sdk['GetFileInformationByHandle'](handle,C.byref(storage)),'BOOL')==0:raise owner._original_win_error(owner._original_get_last_error())
                    if storage.attributes&0x400 or bool(storage.attributes&16)!=(index<len(chain)-1):fail('HOST_CHANGE_SCM_IMAGE_INVALID','launcher image traversal changed type')
                    answer.append({'device':int(storage.volume),'inode':int(storage.index_high)<<32|int(storage.index_low)})
                return answer
            first=identities(handles);metadata={'identity':first[-1]}
            result=_read_original_backup(api,handles[-1],metadata,_limit=2*1024**3,_hash_only=True)
            second=api.open_security_path(path,directory=False)
            if identities(second)!=first or identities(handles)!=first:fail('HOST_CHANGE_SCM_IMAGE_UNKNOWN','launcher image named identity changed')
            return result['sha256']
    except BaseException as error:
        primary=error;owner.remember(error);error._original_preflight_owner=owner;raise
    finally:finish_original(api,primary)
