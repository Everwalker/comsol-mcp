"""One internal birth/deadline/sampled-RSS owner for the complete zero-solve unit."""
from pathlib import Path
import time,math,json,os,subprocess
_TOKEN=object()
_RETAINED_OWNERS={}

class _CoupledAdmission:
    def __init__(self,token,server,evidence):
        if token is not _TOKEN:raise ValueError('admission owner is internal, not public JSON')
        self.server=server;self.evidence=Path(evidence);self.monitor=None
        self.birth_epoch=None;self.deadline_epoch=None;self.deadline_monotonic=None;self.stop_reason=None
        self.handles={};self.groups={};self._token=token;self._route_count=0;self._observe_count=0;self.cleanup_transport_allowed=False
    def _record(self,event,**fields):
        from tools.run_native_w24_cure_preflight import append_event
        append_event(self.evidence/'coupled_admission_events.jsonl',event,**fields)
    def on_server_birth(self):
        # Hold the original handle before probing or recording can fail.
        self.handles['server']=self.server.proc
        self.groups['server']=self.server.proc.pid
        try:
            from comsol_mcp._platform_process import process_identity
            from tools.run_native_w24_static_shape_setup_campaign import SampledRssMonitor
            identity=process_identity(self.server.proc.pid)
            if identity.get('alive') is not True or type(identity.get('start_epoch_ms')) is not int:
                raise RuntimeError('owned Server actual Popen birth is unavailable')
            self.birth_epoch=identity['start_epoch_ms']/1000.0;self.deadline_epoch=self.birth_epoch+1800.0
            self.deadline_monotonic=time.monotonic()+self.deadline_epoch-time.time()
            self.monitor=SampledRssMonitor(server_pid=self.server.proc.pid,
                server_birth_ms=identity['start_epoch_ms'],worker_search_root=self.server.work,
                events_path=self.evidence/'coupled_rss_events.jsonl',
                probe_timeout_reader=lambda preferred:self.wait_cap(preferred),include_owned_descendants=True)
            self.monitor.start()
            self._record('owned_server_birth_admission_started',pid=self.server.proc.pid,
                         birth_epoch=self.birth_epoch,deadline_epoch=self.deadline_epoch,
                         cleanup_reserve_seconds=600.0,hard_rss_cap=False)
        except BaseException as exc:
            self.fail('server_birth_identity_monitor_or_record_failed',exc);raise
    def fail(self,label,exc):
        self.stop_reason=self.stop_reason or label
        try:self._record('admission_stopped_original_handles_retained',label=label,error=str(exc),
            handle_pids={k:getattr(v,'pid',None) for k,v in self.handles.items()})
        except BaseException:pass
    def wait_cap(self,preferred,*,cleanup=False):
        if self.deadline_epoch is None:raise RuntimeError('owned birth deadline not established')
        if type(preferred) not in (int,float) or not math.isfinite(preferred) or preferred<=0:
            raise ValueError('wait preference must be finite positive')
        usable=self.deadline_epoch-time.time()-(0.0 if cleanup else 600.0)
        if not math.isfinite(usable) or usable<=1.0:raise TimeoutError('same birth deadline is inside cleanup reserve; no next effect')
        return min(float(preferred),usable)
    def before_effect(self,label,preferred):
        try:
            if self.server.proc is not self.handles.get('server'):
                raise RuntimeError('original Server Popen owner changed; no next effect')
            if self.stop_reason:raise RuntimeError('prior admission failure stops all subsequent effects: '+self.stop_reason)
            self.wait_cap(preferred)
            if self.monitor is None:raise RuntimeError('RSS owner was not started at actual Server birth')
            self.monitor.sample_once()
            if self.monitor.stop_reason:raise RuntimeError('sampled RSS admission stop: '+self.monitor.stop_reason)
            timeout=self.wait_cap(preferred)
            self._record('next_effect_admitted',label=label,wait_cap_seconds=timeout,deadline_epoch=self.deadline_epoch)
            return timeout
        except BaseException as exc:
            self.fail('first_admission_effect_failure_'+label,exc);raise
    def allow_exact_cleanup_transport(self):
        self.cleanup_transport_allowed=True
    def worker_transport_cap(self,kind,preferred):
        if self.cleanup_transport_allowed and kind in {'health','status','disconnect','shutdown'}:
            return self.wait_cap(preferred,cleanup=True)
        if self.stop_reason and kind in {'health','status'}:
            # Existing ownership observations only; no new numerical/model route.
            return min(float(preferred),1.0)
        return self.before_effect('Worker_transport_'+kind,preferred)
    def bind_child(self,process,label):
        self.handles[label]=process
        self.groups[label]=process.pid
        try:
            from comsol_mcp._platform_process import process_identity
            identity=process_identity(process.pid)
            if identity.get('alive') is not True or type(identity.get('start_epoch_ms')) is not int:
                raise RuntimeError('actual owned child Popen birth missing')
            if label=='worker':
                self.monitor.bind_worker(process.pid,identity['start_epoch_ms']);self.monitor.set_worker_required_live(True)
            else:self.monitor.bind_owned_child(process,identity['start_epoch_ms'])
            self._record('actual_child_birth_bound',label=label,pid=process.pid,start_epoch_ms=identity['start_epoch_ms'])
        except BaseException as exc:self.fail('child_birth_binding_failed',exc);raise
    def wait_child(self,process,label,preferred=60.0):
        self.bind_child(process,label)
        try:
            output=process.communicate(timeout=self.before_effect(label+'_wait',preferred))
            self.monitor.retire_owned_child(process)
            return output
        except BaseException as exc:self.fail(label+'_wait_unknown_handle_retained',exc);raise
    def stop_monitor_before_cleanup(self):
        if self.monitor is not None:self.monitor.stop(timeout_seconds=self.wait_cap(12.0,cleanup=True))
    def observe_owned_terminal_once(self):
        """Observe/reap original handles and their original private groups; no signals."""
        rows=[];nonterminal=[];uncertain=[]
        for label,process in self.handles.items():
            try:
                code=process.poll()
                if code is None:nonterminal.append(label)
                else:code=process.wait(timeout=0.1)
                rows.append({'label':label,'pid':process.pid,'returncode':code,'reaped':code is not None})
            except BaseException as exc:uncertain.append({'label':label,'error':str(exc)})
        probe=subprocess.run(['/bin/ps','-axo','pid=,ppid=,pgid=,command='],
                             capture_output=True,text=True,check=False,timeout=1.0)
        owned_groups=set(self.groups.values());remaining=[];observer_seen=False
        if probe.returncode!=0 or probe.stderr.strip():uncertain.append({'group_probe':'UNVERIFIED'})
        else:
            for line in probe.stdout.splitlines():
                fields=line.strip().split(None,3)
                if len(fields)>=3 and all(x.isdigit() for x in fields[:3]) and int(fields[0])==os.getpid():
                    observer_seen=True
                if len(fields)>=3 and all(x.isdigit() for x in fields[:3]) and int(fields[2]) in owned_groups:
                    remaining.append({'pid':int(fields[0]),'ppid':int(fields[1]),'pgid':int(fields[2]),
                                      'command':fields[3] if len(fields)==4 else ''})
        if not observer_seen:uncertain.append({'group_probe':'OBSERVING_CLI_PROCESS_NOT_CAPTURED'})
        self._observe_count+=1
        receipt={'status':'TERMINAL_REAPED_AND_ORIGINAL_GROUPS_ABSENT' if not nonterminal and not uncertain and not remaining
            else 'UNKNOWN_RETAINED_OWNER_OBSERVE_ONLY','handles':rows,'nonterminal':nonterminal,
            'uncertain':uncertain,'remaining_owned_group_processes':remaining,
            'group_probe':{'returncode':probe.returncode,'stdout':probe.stdout,'stderr':probe.stderr},
            'actual_elapsed_from_birth':None if self.birth_epoch is None else time.time()-self.birth_epoch,
            'original_stop_reason':self.stop_reason,'signals_sent':False,'new_dispatch':False}
        from tools.run_native_w24_cure_preflight import _write_new_mesh_artifact
        _write_new_mesh_artifact(self.evidence/('coupled_owner_observe_%06d.json'%self._observe_count),receipt)
        return receipt

    def retain_unknown_owner_until_terminal(self,summary):
        """Called before coupled CLI return; never discard uncertain Popen ownership."""
        key=id(self);_RETAINED_OWNERS[key]=self
        retained=False
        while True:
            try:observation=self.observe_owned_terminal_once()
            except BaseException as exc:
                observation={'status':'UNKNOWN_RETAINED_OWNER_OBSERVE_ONLY','error':str(exc)}
            if observation['status']=='TERMINAL_REAPED_AND_ORIGINAL_GROUPS_ABSENT':
                summary['coupled_owner_final_observation']=observation
                if retained or (self.deadline_epoch is not None and time.time()>=self.deadline_epoch):
                    summary['status']='FAIL_OR_INCOMPLETE'
                    summary['coupled_unit_budget_exceeded']=bool(self.deadline_epoch is not None and time.time()>=self.deadline_epoch)
                del _RETAINED_OWNERS[key]
                return
            retained=True;self.stop_reason=self.stop_reason or 'owned_handle_wait_or_group_absence_unverified'
            summary['status']='UNKNOWN_RETAINED_OWNER_OBSERVE_ONLY'
            summary['coupled_owner_last_observation']=observation
            summary['scientific_acceptance']='NOT_ACCEPTED'
            summary['safe_retry']=False
            from tools.run_native_w24_cure_preflight import write_json
            write_json(self.evidence/'summary.json',summary)
            # Budget expiry ends admission, not ownership; keep yielding this same CLI/tool.
            time.sleep(0.25)

    def dispatch(self,daemon,request):
        timeout=self.before_effect('managed_'+str(request.get('operation')),600.0)
        routed={**request,'execution':dict(request.get('execution',{}))}
        for key in ('rpc_timeout_s','queue_timeout_s','execution_timeout_s'):
            value=routed['execution'].get(key)
            if value is None:value=timeout
            if type(value) not in (int,float) or not math.isfinite(value) or value<=0:
                raise ValueError('managed admitted timeout must be finite positive')
            routed['execution'][key]=min(float(value),timeout)
        self._route_count+=1
        from tools.run_native_w24_cure_preflight import _write_new_mesh_artifact
        stem=self.evidence/('coupled_route_%03d'%self._route_count)
        _write_new_mesh_artifact(stem.with_suffix('.intent.json'),routed)
        try:
            response=daemon.dispatch(routed)
            _write_new_mesh_artifact(stem.with_suffix('.result.json'),response)
            if (not isinstance(response,dict) or response.get('success') is not True
                    or response.get('execution_state_unknown') is True
                    or response.get('data',{}).get('execution_state_unknown') is True):
                raise RuntimeError('managed firstFAIL/UNKNOWN stops coupled unit; original Store state retained')
            self.wait_cap(1.01)
            return response
        except BaseException as exc:self.fail('managed_dispatch_fail_or_unknown_no_replay',exc);raise

class _AdmissionBoundDaemon:
    def __init__(self,daemon,owner):self._daemon=daemon;self.owner=owner
    def __getattr__(self,name):return getattr(self._daemon,name)
    def dispatch(self,request):return self.owner.dispatch(self._daemon,request)

def _new_coupled_admission(server,evidence):return _CoupledAdmission(_TOKEN,server,evidence)
def require_admission_owner(owner,server=None):
    if type(owner) is not _CoupledAdmission or owner._token is not _TOKEN or (server is not None and owner.server is not server):
        raise ValueError('actual same internal Server admission owner required; JSON refused')
    return owner
