"""Task-only default-owner normalization on one duplicate thread token.

Never changes the process primary token, account, privileges, ACLs or an old
installation. This is not a T037 isolation proof. UNKNOWN originals stay rooted
in this process; no persisted token, automatic retry or cleanup API is supplied.
"""
from contextlib import contextmanager
import ctypes as C,hashlib,os,sys,threading
from comsol_mcp._windows_parameter_publish import _API
from comsol_mcp._windows_host_preflight_owner import hold_original,scalar,finish_original
from comsol_mcp._windows_t037_acl import TOKEN_STATISTICS

_UNKNOWN_THREAD_OWNERS=[]


def _unknown(record,failure):
    if record['first_cause'] is None:record['first_cause']=failure
    record['owner'].remember(record['first_cause']);record['state']='UNKNOWN'
    if not any(r is record for r in _UNKNOWN_THREAD_OWNERS):_UNKNOWN_THREAD_OWNERS.append(record)
    error=RuntimeError('original task thread/token state UNKNOWN; no next effects or retry')
    error.original_task_thread_owner=record
    raise error from record['first_cause']


def _bind(api):
    a=C.WinDLL('advapi32',use_last_error=True);h=C.c_void_p;d=C.c_uint32;pp=C.POINTER(h)
    for name,args in {'OpenProcessToken':[h,d,pp],'OpenThreadToken':[h,d,C.c_int32,pp],
        'DuplicateTokenEx':[h,d,C.c_void_p,C.c_int32,C.c_int32,pp],
        'SetTokenInformation':[h,C.c_int32,C.c_void_p,d],'GetTokenInformation':[h,C.c_int32,C.c_void_p,d,C.POINTER(d)],
        'SetThreadToken':[pp,h],'RevertToSelf':[],'IsValidSid':[C.c_void_p],'GetLengthSid':[C.c_void_p]}.items():
        f=getattr(a,name);f.argtypes=args;f.restype=d if name=='GetLengthSid' else C.c_int32
    for name in ('GetCurrentProcess','GetCurrentThread'):
        f=getattr(api.k,name);f.argtypes=[];f.restype=h
    api.k.GetCurrentProcessId.argtypes=[];api.k.GetCurrentProcessId.restype=d
    return a


def _info(record,token,kind):
    o=record['owner'];a=record['advapi'];needed=C.c_uint32()
    record['calls'].append('GetTokenInformation:'+str(kind))
    ok=scalar(o.function('GetTokenInformation',a)(token,kind,None,0,C.byref(needed)),'BOOL')
    if ok or record['get_last_error']()!=122 or not 8<=needed.value<=1024*1024:raise ValueError('bounded original token query expected')
    buffer=C.create_string_buffer(needed.value);o.hold(token,buffer,needed)
    if not scalar(o.function('GetTokenInformation',a)(token,kind,buffer,len(buffer),C.byref(needed)),'BOOL') or not 8<=needed.value<=len(buffer):raise ValueError('original token query failed')
    return buffer,needed.value


def _sid(record,token,kind):
    buffer,size=_info(record,token,kind);pointer=C.cast(buffer,C.POINTER(C.c_void_p)).contents.value
    start=C.addressof(buffer);end=start+size
    if pointer is None or not start<=pointer<=end-8:raise ValueError('SID not in original token buffer')
    header=C.string_at(pointer,8);extent=8+4*header[1]
    if header[0]!=1 or header[1]>15 or pointer+extent>end:raise ValueError('SID extent invalid')
    o=record['owner'];a=record['advapi']
    if not scalar(o.function('IsValidSid',a)(pointer),'BOOL') or scalar(o.function('GetLengthSid',a)(pointer),'DWORD')!=extent:raise ValueError('SID invalid')
    raw=C.string_at(pointer,extent);text='S-1-'+str(int.from_bytes(raw[2:8],'big'))+''.join('-'+str(int.from_bytes(raw[i:i+4],'little')) for i in range(8,extent,4))
    return pointer,raw,text


def _stats(record,token):
    buf,size=_info(record,token,10)
    if size!=C.sizeof(TOKEN_STATISTICS):raise ValueError('TokenStatistics ABI unsupported')
    st=TOKEN_STATISTICS.from_buffer_copy(buf)
    return {'token_id':[st.TokenId.LowPart,st.TokenId.HighPart],
            'authentication_id':[st.AuthenticationId.LowPart,st.AuthenticationId.HighPart],
            'type':st.TokenType,'level':st.ImpersonationLevel,
            'modified_id':[st.ModifiedId.LowPart,st.ModifiedId.HighPart]}


def _allocate(record,name,storage,*args,allow_no_token=False):
    o=record['owner'];a=record['advapi'];record['calls'].append(name)
    try:
        ok=scalar(o.allocation_call(name,a,storage,*args,family='handle',keepalive=(record,)),'BOOL')
        if allow_no_token and not ok and not storage.value and record['get_last_error']()==1008:return False
        if not ok or not storage.value:raise ValueError(name+' not confirmed')
        return True
    except BaseException as failure:_unknown(record,failure)


@contextmanager
def task_thread_owner():
    if _UNKNOWN_THREAD_OWNERS:raise RuntimeError('previous task original token state UNKNOWN; no replay')
    api=_API();o=hold_original(api);a=_bind(api)
    record={'api':api,'owner':o,'advapi':a,'first_cause':None,'state':'PREPARING',
            'get_last_error':C.get_last_error,'thread':threading.get_ident(),
            'primary':C.c_void_p(),'previous':C.c_void_p(),'duplicate':C.c_void_p(),
            'observed':C.c_void_p(),'restored':C.c_void_p(),'calls':[]}
    # Freeze effect/restore/close functions before acquiring any output.
    for name in ('SetThreadToken','RevertToSelf','SetTokenInformation'):o.function(name,a)
    o.function('CloseHandle',api.k)
    try:
        primary=record['primary'];previous=record['previous'];dup=record['duplicate']
        _allocate(record,'OpenProcessToken',primary,o.function('GetCurrentProcess',api.k)(),0x0a,C.byref(primary))
        p_sid,p_raw,text=_sid(record,primary,1);p_owner,po_raw,_=_sid(record,primary,4)
        p_stats=_stats(record,primary);privileges=bytes(_info(record,primary,3)[0])
        had_thread=_allocate(record,'OpenThreadToken',previous,o.function('GetCurrentThread',api.k)(),0x0e,1,C.byref(previous),allow_no_token=True)
        previous_stats=_stats(record,previous) if had_thread else None
        if had_thread and _sid(record,previous,1)[1]!=p_raw:raise ValueError('original thread identity differs from process user')
        _allocate(record,'DuplicateTokenEx',dup,primary,0x8c,None,2,2,C.byref(dup))
        if _sid(record,dup,1)[1]!=p_raw or bytes(_info(record,dup,3)[0])!=privileges:raise ValueError('duplicate changes actual user/privileges')
        owner_info=C.c_void_p(p_sid);o.hold(dup,owner_info,p_sid,p_raw,record)
        record['calls'].append('SetTokenInformation:TokenOwner:duplicate-only')
        if not scalar(o.function('SetTokenInformation',a)(dup,4,C.byref(owner_info),C.sizeof(owner_info)),'BOOL'):raise ValueError('duplicate TokenOwner assignment not confirmed')
        dup_stats=_stats(record,dup)
        if (dup_stats['type']!=2 or dup_stats['level']!=2 or dup_stats['authentication_id']!=p_stats['authentication_id']
                or _sid(record,dup,4)[1]!=p_raw or _sid(record,primary,4)[1]!=po_raw):raise ValueError('duplicate owner or unchanged primary proof differs')
        record['state']='ACTIVATION_INTENT'
        record['calls'].append('SetThreadToken:current-thread')
        if not scalar(o.function('SetThreadToken',a)(None,dup),'BOOL'):raise ValueError('thread assignment not confirmed')
        observed=record['observed']
        _allocate(record,'OpenThreadToken',observed,o.function('GetCurrentThread',api.k)(),0x08,1,C.byref(observed))
        if _stats(record,observed)!=dup_stats or _sid(record,observed,4)[1]!=p_raw:raise ValueError('actual attached thread token differs')
        record['state']='NORMALIZED'
        receipt={'PID':scalar(o.function('GetCurrentProcessId',api.k)(),'DWORD'),'thread':record['thread'],
                 'UserSID':text,'owner_sid_sha256':hashlib.sha256(p_raw).hexdigest(),'python':sys.version,
                 'primary':p_stats,'duplicate':dup_stats,'original_thread':previous_stats,
                 'primary_unchanged':True,'calls':record['calls'],'scope':'TASK_THREAD_DUPLICATE_NOT_PROCESS_PRIMARY_OR_T037','restored':False}
        primary_failure=None
        try:yield receipt
        except BaseException as failure:primary_failure=failure;record['first_cause']=failure;o.remember(failure);raise
        finally:
            # No automatic restoration retry if any native call is ambiguous.
            try:
                record['state']='RESTORE_INTENT'
                fn=o.function('SetThreadToken',a) if had_thread else o.function('RevertToSelf',a)
                record['calls'].append('SetThreadToken:original' if had_thread else 'RevertToSelf:original-absence')
                rc=fn(None,previous) if had_thread else fn()
                if not scalar(rc,'BOOL'):raise ValueError('original thread restoration not confirmed')
                restored=record['restored'];exists=_allocate(record,'OpenThreadToken',restored,o.function('GetCurrentThread',api.k)(),0x08,1,C.byref(restored),allow_no_token=True)
                if exists!=had_thread or had_thread and _stats(record,restored)!=previous_stats:raise ValueError('original thread identity not restored')
                if _stats(record,primary)!=p_stats or _sid(record,primary,4)[1]!=po_raw or bytes(_info(record,primary,3)[0])!=privileges:raise ValueError('primary token changed')
                record['state']='RESTORED';receipt['restored']=True
                finish_original(api,primary_failure)
            except BaseException as failure:_unknown(record,record['first_cause'] if record['first_cause'] is not None else failure)
    except BaseException as failure:
        if record['state']=='UNKNOWN':raise
        if record['state'] in ('ACTIVATION_INTENT','NORMALIZED','RESTORE_INTENT'):_unknown(record,failure)
        try:finish_original(api,failure)
        except BaseException:_unknown(record,failure)
        raise


def stable_native_bytes(path):
    """Compare birth/change time within their own domains on one original FD.

    The CRT handle is borrowed only for metadata and is never adopted/closed by
    the native API registry. Original Python file object owns its lifetime.
    """
    from pathlib import Path
    import msvcrt
    path=Path(path);assert not path.is_symlink()
    named_before=path.stat();api=_API();query=api.full_fingerprint
    with path.open('rb') as stream:
        handle=C.c_void_p(msvcrt.get_osfhandle(stream.fileno()))
        first=os.fstat(stream.fileno());native_before=query(handle)
        raw=stream.read();named_after=path.stat()
        native_after=query(handle);last=os.fstat(stream.fileno())
        fd=lambda v:(v.st_dev,v.st_ino,v.st_size,v.st_mtime_ns,v.st_ctime_ns,v.st_birthtime_ns)
        named=lambda v:(v.st_dev,v.st_ino,v.st_size,v.st_mtime_ns,v.st_birthtime_ns)
        assert fd(first)==fd(last) and native_before==native_after
        assert fd(named_before)==fd(named_after) and named(first)==named(named_after)
        assert native_before[0]==(first.st_dev,first.st_ino) and native_before[1]==len(raw)
        return raw,hashlib.sha256(raw).hexdigest(),{'full_native_fingerprint':native_before,
            'FD_change_time_before_after':[first.st_ctime_ns,last.st_ctime_ns],
            'named_creation_time_before_after':[named_before.st_birthtime_ns,named_after.st_birthtime_ns]}
