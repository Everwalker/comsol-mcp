"""Fixed private netsh query ownership; no DLLs are loaded at import."""
import ctypes as C
import time
from ._windows_owned_resources import NativeResourceError

U32=C.c_uint32; I32=C.c_int32; PTR=C.c_void_p
class SA(C.Structure):
    _fields_=[('length',U32),('descriptor',PTR),('inherit',I32)]
class PROCESSINFO(C.Structure):
    _fields_=[('process',PTR),('thread',PTR),('pid',U32),('tid',U32)]
class STARTUPINFO(C.Structure):
    _fields_=[('cb',U32),('reserved',PTR),('desktop',PTR),('title',PTR),('x',U32),('y',U32),('xs',U32),('ys',U32),('xc',U32),('yc',U32),('fill',U32),('flags',U32),('show',C.c_uint16),('reserved2size',C.c_uint16),('reserved2',PTR),('stdin',PTR),('stdout',PTR),('stderr',PTR)]
class STARTUPINFOEX(C.Structure):
    _fields_=[('si',STARTUPINFO),('attributes',PTR)]

class QueryOwner:
    def __init__(self,parent,kernel,last_error,*,clock=time.monotonic,sleep=time.sleep):
        self.parent=parent;self.k=kernel;self.last_error=last_error;self.clock=clock;self.sleep=sleep
        self.calls=[];self.handles=[];self.outputs={'stdout':bytearray(),'stderr':bytearray()};self.eof=set()
        self.state='PREPARED';self.error=None;self.pending=None;self.raw_wait=None;self.exit_value=None
        parent.process_owner=self
    def unknown(self,message):
        if self.state=='UNKNOWN':
            raise self.error
        err=NativeResourceError('NATIVE_RESOURCE_RELEASE_UNKNOWN',message,safe_retry=False,stage='pre_dispatch')
        self.state='UNKNOWN';self.parent.state='UNKNOWN';self.error=err
        if self.parent.operation_error is None:self.parent.operation_error=err
        raise err
    def call(self,fn,args,holders=()):
        if self.state=='UNKNOWN':self.unknown('original query owner is already unknown')
        rec={'function':fn,'args':args,'holders':tuple(holders),'state':'ENTERED','raw':None,'error':None};self.calls.append(rec);self.pending=rec
        try:
            raw=fn(*args);rec['raw']=raw;rec['error']=self.last_error();rec['state']='RETURNED';self.pending=None
            return raw,rec['error']
        except BaseException as exc:
            if self.state!='UNKNOWN':
                self.error=exc;self.state='UNKNOWN';self.parent.state='UNKNOWN'
                if self.parent.operation_error is None:self.parent.operation_error=exc
            raise self.error
    def boolean(self,raw,label):
        if type(raw) is not int or not -(1 << 31)<=raw<(1 << 31) or raw==0:
            self.unknown(label+' did not return a valid nonzero BOOL')
    def own(self,value,label):
        if type(value) is not int or value<=0 or value==PTR(-1).value:self.unknown(label+' invalid handle')
        if any(x['value']==value for x in self.handles):self.unknown(label+' duplicate handle')
        entry={'value':value,'label':label,'state':'ACQUIRED','closer':self.k.CloseHandle};self.handles.append(entry);return entry
    def close(self,entry):
        if entry not in self.handles or entry['state']!='ACQUIRED':self.unknown('invalid original close')
        entry['state']='CLOSE_ENTERED';raw,_=self.call(entry['closer'],(entry['value'],),(entry,));self.boolean(raw,'CloseHandle');entry['state']='RELEASED'
    def execute(self,exe,cwd,env):
        if self.state=='UNKNOWN':raise self.error
        try:
            return self.run(exe,cwd,env)
        except BaseException as exc:
            if self.state!='UNKNOWN':
                self.error=exc;self.state='UNKNOWN';self.parent.state='UNKNOWN'
                if self.parent.operation_error is None:self.parent.operation_error=exc
            raise
    def run(self,exe,cwd,env):
        if self.state=='UNKNOWN':raise self.error
        deadline=self.clock()+15
        if not exe or '"' in exe or set(env)!={'SystemRoot','WINDIR','PATH'}:self.unknown('fixed argv/environment invalid')
        for label in ('stdin','stdout','stderr'):
            rd=PTR();wr=PTR();sa=SA(C.sizeof(SA),None,1);raw,_=self.call(self.k.CreatePipe,(C.byref(rd),C.byref(wr),C.byref(sa),0),(rd,wr,sa))
            r=self.own(rd.value,label+'_read') if rd.value else None;w=self.own(wr.value,label+'_write') if wr.value else None
            self.boolean(raw,'CreatePipe')
            if r is None or w is None:self.unknown('pipe outputs incomplete')
        entries={x['label']:x for x in self.handles}
        for label in ('stdin_write','stdout_read','stderr_read'):
            entry=entries[label];raw,_=self.call(self.k.SetHandleInformation,(entry['value'],1,0),(entry,));self.boolean(raw,'SetHandleInformation')
        self.close(entries['stdin_write'])
        for label in ('stdout_read','stderr_read'):
            mode=U32(1);entry=entries[label];raw,_=self.call(self.k.SetNamedPipeHandleState,(entry['value'],C.byref(mode),None,None),(mode,entry));self.boolean(raw,'SetNamedPipeHandleState')
        need=C.c_size_t();raw,err=self.call(self.k.InitializeProcThreadAttributeList,(None,1,0,C.byref(need)),(need,))
        if type(raw) is not int or raw!=0 or type(err) is not int or err!=122 or not 0<need.value<=65536:self.unknown('attribute list size probe invalid')
        attrs=C.create_string_buffer(need.value);raw,_=self.call(self.k.InitializeProcThreadAttributeList,(attrs,1,0,C.byref(need)),(attrs,need));self.boolean(raw,'attribute list init')
        if not 0<need.value<=C.sizeof(attrs):self.unknown('attribute list initialized extent invalid')
        inherit=(PTR*3)(entries['stdin_read']['value'],entries['stdout_write']['value'],entries['stderr_write']['value'])
        raw,_=self.call(self.k.UpdateProcThreadAttribute,(attrs,0,0x20002,inherit,C.sizeof(inherit),None,None),(attrs,inherit));self.boolean(raw,'handle list')
        startup=STARTUPINFOEX();startup.si.cb=C.sizeof(startup);startup.si.flags=0x100;startup.si.stdin=inherit[0];startup.si.stdout=inherit[1];startup.si.stderr=inherit[2];startup.attributes=C.addressof(attrs)
        command=C.create_unicode_buffer('"'+exe+'" int ipv4 show dynamicport tcp');environment=C.create_unicode_buffer('\0'.join(k+'='+env[k] for k in sorted(env,key=str.upper))+'\0\0');pi=PROCESSINFO()
        raw,_=self.call(self.k.CreateProcessW,(exe,command,None,None,1,0x00080400,environment,cwd,C.byref(startup),C.byref(pi)),(command,environment,startup,pi,attrs,inherit))
        process=self.own(pi.process,'process') if pi.process else None;thread=self.own(pi.thread,'thread') if pi.thread else None
        self.boolean(raw,'CreateProcessW')
        if process is None or thread is None or not pi.pid or not pi.tid:self.unknown('process output incomplete')
        raw,_=self.call(self.k.DeleteProcThreadAttributeList,(attrs,),(attrs,))
        if raw is not None:self.unknown('attribute list delete result invalid')
        for label in ('stdin_read','stdout_write','stderr_write'):self.close(entries[label])
        iterations=0
        while len(self.eof)<2:
            if self.clock()>=deadline or iterations>=65536:self.unknown('pipe deadline or iteration bound')
            iterations+=1;progress=False
            for name in ('stdout','stderr'):
                if name in self.eof:continue
                if self.clock()>=deadline:self.unknown('pipe deadline')
                size=min(4096,16384-len(self.outputs[name])+1);buf=C.create_string_buffer(size);count=U32();entry=entries[name+'_read']
                raw,err=self.call(self.k.ReadFile,(entry['value'],buf,size,C.byref(count),None),(entry,buf,count))
                if type(raw) is not int or not -(1 << 31)<=raw<(1 << 31) or type(err) is not int or count.value>size:self.unknown('invalid ReadFile result')
                if raw==0 and err==232 and count.value==0:continue
                if raw==0 and err==109 and count.value==0:self.eof.add(name);progress=True;continue
                if raw==0 or count.value==0:self.unknown('ReadFile EOF or result ambiguous')
                self.outputs[name].extend(buf.raw[:count.value]);progress=True
                if len(self.outputs[name])>16384 or sum(map(len,self.outputs.values()))>32768:self.unknown('pipe output cap exceeded')
            if not progress:self.sleep(0.005)
        remaining=deadline-self.clock()
        if remaining<=0:self.unknown('original wait deadline')
        self.raw_wait,_=self.call(self.k.WaitForSingleObject,(process['value'],min(15000,max(1,int(remaining*1000)))),(process,))
        if type(self.raw_wait) is not int or self.raw_wait!=0:self.unknown('original wait not signaled')
        code=U32();raw,_=self.call(self.k.GetExitCodeProcess,(process['value'],C.byref(code)),(process,code));self.boolean(raw,'GetExitCodeProcess')
        self.exit_value=code.value
        for entry in (entries['stdout_read'],entries['stderr_read'],thread,process):self.close(entry)
        self.state='RELEASED';return self.exit_value,bytes(self.outputs['stdout']),bytes(self.outputs['stderr'])

def bind(kernel):
    """x64 ABI binding only; caller supplied kernel32, no SDK call here."""
    if C.sizeof(PTR)!=8 or C.sizeof(C.c_wchar)!=2 or C.sizeof(PROCESSINFO)!=24 or C.sizeof(STARTUPINFOEX)!=112:raise ValueError('Windows x64 ABI mismatch')
    specs={'CreatePipe':(I32,(PTR,PTR,PTR,U32)),'SetHandleInformation':(I32,(PTR,U32,U32)),'SetNamedPipeHandleState':(I32,(PTR,PTR,PTR,PTR)),'InitializeProcThreadAttributeList':(I32,(PTR,U32,U32,PTR)),'UpdateProcThreadAttribute':(I32,(PTR,U32,C.c_size_t,PTR,C.c_size_t,PTR,PTR)),'DeleteProcThreadAttributeList':(None,(PTR,)),'CreateProcessW':(I32,(C.c_wchar_p,PTR,PTR,PTR,I32,U32,PTR,C.c_wchar_p,PTR,PTR)),'ReadFile':(I32,(PTR,PTR,U32,PTR,PTR)),'WaitForSingleObject':(U32,(PTR,U32)),'GetExitCodeProcess':(I32,(PTR,PTR)),'CloseHandle':(I32,(PTR,))}
    for name,(ret,args) in specs.items():fn=getattr(kernel,name);fn.restype=ret;fn.argtypes=list(args)
