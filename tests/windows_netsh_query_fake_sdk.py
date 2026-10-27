import ctypes as C
from types import SimpleNamespace as NS
OUTPUT=b'Protocol tcp Dynamic Port Range\n----------------\nStart Port : 40000\nNumber of Ports : 5001\n'
def install(monkeypatch,net,state,fault):
    from comsol_mcp import _windows_netsh_query as q
    log=[];fake=NS(log=log,error=0,n=100,mode=None,output=OUTPUT,exit=1 if fault=='query_error' else 0,t=0,queues={})
    if fault=='ambiguous_output':fake.output+=fake.output
    def event(name):
        owner=net._dynamic_range_owner;query=owner.process_owner
        assert query is fake.owner and query.pending is query.calls[-1] and query.pending['state']=='ENTERED'
        log.append(name);fake.error=0
        if fake.mode=='exception:'+name:raise RuntimeError('fake first uncertainty')
    def create_pipe(r,w,sa,size):
        event('CreatePipe');assert sa._obj.inherit==1;fake.n+=1;r._obj.value=fake.n;fake.n+=1;w._obj.value=fake.n;return 1
    def set_info(h,mask,flags):event('SetHandleInformation');assert mask==1 and flags==0;return 1
    def set_pipe(h,mode,a,b):event('SetNamedPipeHandleState');assert mode._obj.value==1;return 1
    def init(attr,n,flags,size):
        event('InitializeProcThreadAttributeList');assert n==1
        if attr is None:size._obj.value=128;fake.error=122;return 0
        return 1
    def update(attr,flags,key,handles,size,old,ret):event('UpdateProcThreadAttribute');assert key==0x20002 and [*handles]==[101,104,106];return 1
    def create(exe,command,pa,ta,inherit,flags,env,cwd,startup,pi):
        event('CreateProcessW');assert net._dynamic_range_owner.process_owner.pending['holders'][3] is pi._obj
        state.queries.append([exe,'int','ipv4','show','dynamicport','tcp']);assert inherit==1 and flags==0x80400
        assert command.value=='"'+exe+'" int ipv4 show dynamicport tcp' and cwd==r'C:\Windows\System32'
        assert ''.join(env[:]).rstrip('\0').split('\0')==[r'PATH=C:\Windows\System32',r'SystemRoot=C:\Windows',r'WINDIR=C:\Windows']
        assert [startup._obj.si.stdin,startup._obj.si.stdout,startup._obj.si.stderr]==[101,104,106]
        pi._obj.process=201;pi._obj.thread=202;pi._obj.pid=99;pi._obj.tid=100
        fake.queues={103:[fake.output,109],105:[109]};return 1
    def delete(attrs):event('DeleteProcThreadAttributeList')
    def read(h,buf,size,count,over):
        event('ReadFile:'+str(h))
        if fake.mode=='nodata':fake.error=232;return 0
        if fake.mode=='overflow' and h==103:data=b'x'*size
        else:
            item=fake.queues[h].pop(0)
            if item==109:fake.error=109;return 0
            data=item
        assert len(data)<=size;C.memmove(buf,data,len(data));count._obj.value=len(data);return 1
    def wait(h,ms):event('WaitForSingleObject');assert h==201 and 0<ms<=15000;return 0
    def exit_code(h,out):event('GetExitCodeProcess');out._obj.value=fake.exit;return 1
    original=state.kernel.CloseHandle
    def close(h):
        if h>=900:return original(h)
        event('CloseHandle:'+str(h));return 1
    k=state.kernel;k.CreatePipe=create_pipe;k.SetHandleInformation=set_info;k.SetNamedPipeHandleState=set_pipe;k.InitializeProcThreadAttributeList=init;k.UpdateProcThreadAttribute=update;k.CreateProcessW=create;k.DeleteProcThreadAttributeList=delete;k.ReadFile=read;k.WaitForSingleObject=wait;k.GetExitCodeProcess=exit_code;k.CloseHandle=close
    monkeypatch.setattr(q,'bind',lambda k:None);monkeypatch.setattr(C,'get_last_error',lambda:fake.error,raising=False)
    original_init=q.QueryOwner.__init__
    def init_owner(self,parent,kernel,last_error,**kw):
        original_init(self,parent,kernel,last_error,clock=lambda:fake.t,sleep=lambda x:setattr(fake,'t',fake.t+1));fake.owner=self
    monkeypatch.setattr(q.QueryOwner,'__init__',init_owner)
    state.query_fake=fake
    return fake
