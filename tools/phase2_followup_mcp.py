#!/usr/bin/env python3
"""Bounded supplemental Phase-2 evidence: T011 API mutation, fresh Worker reopen, T026 queue.

No mode starts/stops COMSOL.  ``fresh-worker-reopen`` delegates the verified
owned-worker replacement to phase2_recovery_mcp.py, then uses production MCP
stdio to reopen a saved MPH.  ``external-api`` uses a separate fixed official
Java client only on a newly loaded evidence copy and scalar test parameter.
"""
from __future__ import annotations
import argparse, asyncio, hashlib, json, os, subprocess, sys, time, traceback
from datetime import timedelta
from pathlib import Path
from typing import Any
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import phase2_run_mcp
from phase2_run_mcp import ROOT, JDK11_DEFAULT, _execution, _server_identity

ACTIVE={"QUEUED","STARTING","RUNNING"}
def redact(v):
    if isinstance(v,dict): return {k:("REDACTED" if any(x in k.lower() for x in ("token","password","authorization","prefs")) else redact(xv)) for k,xv in v.items()}
    if isinstance(v,list): return [redact(x) for x in v]
    return v
def write(path:Path,v:Any): path.write_text(json.dumps(redact(v),ensure_ascii=False,indent=2,default=str)+"\n")
def digest(path:Path):
    h=hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda:f.read(1024*1024),b""): h.update(b)
    return h.hexdigest()
def extract(p):
    e=p.get("execution",{}) if isinstance(p,dict) else {}; return e.get("model_ref"),e.get("revision")
def metric_ok(p):
    rows=(p.get("data") or {}).get("results",[]); value=rows[0].get("value") if len(rows)==1 else None
    while isinstance(value,list) and value: value=value[-1]
    return bool(p.get("success")) and len(rows)==1 and rows[0].get("ok") is True and abs(float(value)) < 1e-8
def env_for(a): return dict(os.environ,COMSOL_ROOT=a.comsol_root,JAVA_HOME=a.jdk11,COMSOL_PREFS_DIR=a.prefs,COMSOL_SERVER_MCP_HOME=str(a.private_home.resolve()),PYTHONPATH=str(ROOT))
def javac_cp(root:Path):
    names=[x.strip() for x in (root/"bin/comsolclientpath.txt").read_text().splitlines() if x.strip()]
    return os.pathsep.join(str(root/"apiplugins"/x) for x in names)
def compile_external(a, run:Path):
    out=run/"external-client-classes"; out.mkdir()
    cp=javac_cp(Path(a.comsol_root)); source=ROOT/"tools/java/Phase2ExternalMutation.java"
    cmd=[str(Path(a.jdk11)/"bin/javac"),"-cp",cp,"-d",str(out),str(source)]
    done=subprocess.run(cmd,text=True,capture_output=True,check=False); write(run/"external_compile.json",{"command":["javac","-cp","OFFICIAL_COMSOL_CLASSPATH","-d",str(out),str(source)],"returncode":done.returncode,"stdout":done.stdout,"stderr":done.stderr})
    if done.returncode: raise RuntimeError("external mutation Java client did not compile")
    return out,cp
class Host:
 def __init__(self,a,run,transcript,label): self.a,self.run,self.transcript,self.label=a,run,transcript,label
 async def __aenter__(self):
  self.log=(self.run/f"{self.label}.log").open("w"); self.t=stdio_client(StdioServerParameters(command=self.a.python,args=["-m","comsol_mcp.mcp_server"],env=env_for(self.a),cwd=str(ROOT)),errlog=self.log); r,w=await self.t.__aenter__(); self.s=ClientSession(r,w,read_timeout_seconds=timedelta(minutes=10)); await self.s.__aenter__(); await self.s.initialize(); return self
 async def __aexit__(self,*x): await self.s.__aexit__(*x); await self.t.__aexit__(*x); self.log.close()
 async def call(self,name,args=None):
  start=time.monotonic(); reply=await self.s.call_tool(name,args or {}); elapsed=time.monotonic()-start
  try: payload=json.loads(reply.content[0].text)
  except Exception: payload={"success":False,"error":"non-json"}
  self.transcript.append({"host":self.label,"operation":name,"arguments":args or {},"elapsed_s":elapsed,"outer_isError":bool(reply.isError),"payload":payload}); return {**payload,"_outer_isError":bool(reply.isError)}
async def bind_saved(host,a, key):
 key=a.key_prefix+key
 c=await host.call("server_connect",{"host":"127.0.0.1","port":a.port,"execution":{"idempotency_key":key+"-connect","request_id":key+"-connect"}})
 l=await host.call("model_load",{"path":str(a.saved_mph.resolve()),"execution":{"idempotency_key":key+"-load","request_id":key+"-load"}})
 return c,l,*extract(l)
async def external_api(a,run,t,assertions):
 async with Host(a,run,t,"external-api") as h:
  c,l,ref,rev=await bind_saved(h,a,"followup-external")
  assertions["loaded_fresh_test_copy"]=bool(c.get("success") and l.get("success") and ref and isinstance(rev,int))
  if not assertions["loaded_fresh_test_copy"]: return "BLOCKED"
  ordinary_error=await h.call("evaluate_expressions", {"expressions_json": '[{"name":"invalid","expression":"phase2_followup_missing_symbol"}]', "execution": _execution(ref,rev,key="followup-ordinary-expression",request="followup-ordinary-expression")})
  ordinary_rows=(ordinary_error.get("data") or {}).get("results", [])
  assertions["ordinary_expression_outer_error"]=bool(
      not ordinary_error.get("success") and ordinary_error.get("_outer_isError")
      and len(ordinary_rows)==1 and ordinary_rows[0].get("ok") is False
      and "phase2_followup_missing_symbol" in json.dumps(ordinary_error)
  )
  before=await h.call("set_parameters",{"parameters_json":'[{"name":"phase2_external_guard","expression":"1"}]',"execution":_execution(ref,rev,key="followup-external-before",request="followup-external-before")})
  ref,rev=extract(before); assertions["managed_baseline_write"]=bool(before.get("success") and ref and isinstance(rev,int))
  if not assertions["managed_baseline_write"]: return "BLOCKED"
  health_before=await h.call("session_health")
  out,cp=compile_external(a,run)
  cmd=[str(Path(a.jdk11)/"bin/java"),"-Dcs.prefsdir="+str(Path(a.prefs).resolve()),"-cp",str(out)+os.pathsep+cp,"Phase2ExternalMutation","127.0.0.1",str(a.port),ref["model_tag"],"phase2_external_guard","2"]
  done=subprocess.run(cmd,text=True,capture_output=True,check=False); write(run/"external_mutation.json",{"command":["java","-Dcs.prefsdir=REDACTED_PREFS","-cp","OFFICIAL_COMSOL_CLASSPATH","Phase2ExternalMutation","127.0.0.1",a.port,ref["model_tag"],"phase2_external_guard","2"],"returncode":done.returncode,"stdout":done.stdout,"stderr":done.stderr})
  assertions["external_client_mutated_only_test_parameter"]=done.returncode==0
  inspect=await h.call("model_inspect",{"refresh":False,"execution":{"model_ref":ref,"session_id":ref["session_id"],"idempotency_key":a.key_prefix+"followup-external-inspect","request_id":a.key_prefix+"followup-external-inspect"}})
  params=await h.call("get_parameters",{"execution":{"model_ref":ref,"session_id":ref["session_id"],"idempotency_key":a.key_prefix+"followup-external-read","request_id":a.key_prefix+"followup-external-read"}})
  stale=await h.call("set_parameters",{"parameters_json":'[{"name":"phase2_external_guard","expression":"3"}]',"execution":_execution(ref,rev,key="followup-external-stale",request="followup-external-stale")})
  health_after=await h.call("session_health")
  rows=(params.get("data") or {}).get("parameters",[]); got=next((x.get("expression") for x in rows if x.get("name")=="phase2_external_guard"),None)
  hb=((health_before.get("data") or {}).get("worker") or {}).get("changed_count",0); ha=((health_after.get("data") or {}).get("worker") or {}).get("changed_count",0)
  text=json.dumps(stale).lower(); assertions.update(handler_event_counter_advanced=isinstance(hb,int) and isinstance(ha,int) and ha>hb,inspection_marks_external_change=bool(inspect.get("success") and (inspect.get("execution") or {}).get("dirty") is True),readback_external_value_is_2=str(got)=="2",stale_write_denied=not stale.get("success") and stale.get("_outer_isError") and "revision_conflict" in text)
 return "PASS" if all(assertions.values()) else "FAIL"
async def fresh_worker_reopen(a,run,t,assertions):
 if not a.previous_job_id or not a.previous_ref_json: raise RuntimeError("fresh-worker-reopen requires prior terminal job/ref")
 recovery=run/"worker-recovery"; cmd=[a.python,str(ROOT/"tools/phase2_recovery_mcp.py"),"--mode","worker-replace","--private-home",str(a.private_home),"--prefs",a.prefs,"--job-id",a.previous_job_id,"--model-ref-json",str(a.previous_ref_json),"--comsol-pid",str(a.pid),"--port",str(a.port),"--python",a.python,"--run-dir",str(recovery)]
 done=subprocess.run(cmd,text=True,capture_output=True,check=False,env=env_for(a)); write(run/"worker_recovery_invocation.json",{"returncode":done.returncode,"stdout":done.stdout,"stderr":done.stderr,"recovery_dir":str(recovery)})
 rr=json.loads((recovery/"result.json").read_text()) if (recovery/"result.json").is_file() else {}; assertions["verified_worker_replacement"]=done.returncode==0 and rr.get("status")=="PASS"
 if not assertions["verified_worker_replacement"]: return "BLOCKED"
 async with Host(a,run,t,"fresh-worker-reopen") as h:
  c,l,ref,rev=await bind_saved(h,a,"followup-fresh-worker")
  m=await h.call("get_core_metrics",{"metrics_json":'[{"name":"fresh_worker_max_abs_error","expression":"abs(u-2)","aggregate":"max","domains":[1]}]',"execution":_execution(ref,rev,key="followup-fresh-worker-metric",request="followup-fresh-worker-metric")}) if ref else {"success":False}
  assertions["fresh_worker_reopen_metric"]=bool(c.get("success") and l.get("success") and ref and metric_ok(m))
 return "PASS" if all(assertions.values()) else "FAIL"
async def same_model_queue(a,run,t,assertions):
 async with Host(a,run,t,"same-model-queue") as h:
  c,l,ref,rev=await bind_saved(h,a,"followup-queue")
  if not (c.get("success") and l.get("success") and ref): assertions["bound"]=False; return "BLOCKED"
  solve=await h.call("run_study",{"study_tag":"std1","execution":_execution(ref,rev,key="followup-same-solve",request="followup-same-solve",rpc_timeout_s=0)})
  job=((solve.get("data") or {}).get("job_id") or (solve.get("execution") or {}).get("job_id")); state=None
  if isinstance(job,str):
   for _ in range(100):
    st=await h.call("job_status",{"job_id":job}); state=(st.get("data") or {}).get("status")
    if state=="RUNNING": break
    if state not in ACTIVE: break
    await asyncio.sleep(.05)
  if state!="RUNNING": assertions["real_running_window"]=False; return "NOT_RUN"
  q=await h.call("set_parameters",{"parameters_json":'[{"name":"phase2_same_model_queued","expression":"1"}]',"execution":_execution(ref,rev,key="followup-same-queued",request="followup-same-queued",queue_timeout_s=0)})
  qjob=((q.get("data") or {}).get("job_id") or (q.get("execution") or {}).get("job_id")); js=await h.call("job_status",{"job_id":qjob}) if qjob else {"data":{}}
  terminal=None
  for _ in range(600):
   terminal=await h.call("job_status",{"job_id":job}); state=(terminal.get("data") or {}).get("status")
   if state not in ACTIVE: break
   await asyncio.sleep(.1)
  original=(terminal.get("data") or {}).get("result") if terminal else {}
  assertions.update(real_running_window=True,same_model_write_not_executed=(js.get("data") or {}).get("status") in {"EXPIRED","CANCELLED"},queue_timeout_reported=not q.get("success") and q.get("_outer_isError"),original_solve_terminal_success=state=="SUCCEEDED" and isinstance(original,dict) and original.get("success") is True,no_hidden_running_after_case=state not in ACTIVE)
 return "PASS" if all(assertions.values()) else "FAIL"
async def run(a):
 stamp=time.strftime("%Y%m%dT%H%M%SZ",time.gmtime()); phase2_run_mcp._RUN_IDEMPOTENCY_PREFIX=stamp+"-"; a.key_prefix=stamp+"-"; run=Path(a.run_dir).resolve() if a.run_dir else ROOT/"evidence/phase2/followup"/stamp; run.mkdir(parents=True,exist_ok=False); t=[]; assertions={}; result={"case":a.mode,"status":"NOT_RUN","gui_t011":"BLOCKED_NO_GUI_EVIDENCE" if a.mode=="external-api" else "NOT_APPLICABLE"}
 try:
  if not a.saved_mph.is_file(): raise FileNotFoundError(a.saved_mph)
  write(run/"request.json",{"mode":a.mode,"saved_mph":str(a.saved_mph),"production_stdio":True,"no_comsol_lifecycle":True,"server_instance_id_semantics":"worker-connection epoch; server PID/endpoint evidence identifies the persistent server"}); write(run/"environment.json",{"server_identity":_server_identity(a.pid,a.port),"saved_sha256":digest(a.saved_mph)})
  result["status"]=await {"external-api":external_api,"fresh-worker-reopen":fresh_worker_reopen,"same-model-queue":same_model_queue}[a.mode](a,run,t,assertions)
 except Exception as e: result.update(status="FAIL",error=f"{type(e).__name__}: {e}",traceback=traceback.format_exc())
 finally:
  write(run/"transcript.json",t); write(run/"assertions.json",assertions); write(run/"result.json",result); write(run/"SHA256SUMS.json",{p.name:digest(p) for p in run.iterdir() if p.is_file()})
 print(json.dumps({"run_dir":str(run),"status":result["status"]})); return 0 if result["status"]=="PASS" else 1
if __name__=="__main__":
 p=argparse.ArgumentParser(); p.add_argument("--mode",choices=("external-api","fresh-worker-reopen","same-model-queue"),required=True); p.add_argument("--saved-mph",type=Path,required=True); p.add_argument("--private-home",type=Path,required=True); p.add_argument("--prefs",required=True); p.add_argument("--pid",type=int,default=84749); p.add_argument("--port",type=int,default=56388); p.add_argument("--comsol-root",default="/Applications/COMSOL64/Multiphysics"); p.add_argument("--jdk11",default=JDK11_DEFAULT); p.add_argument("--python",default=sys.executable); p.add_argument("--previous-job-id"); p.add_argument("--previous-ref-json",type=Path); p.add_argument("--run-dir"); raise SystemExit(asyncio.run(run(p.parse_args())))
