"""Independent reviewer public-MCP probe. PREPARED, run only on frozen candidate.
Reuses launcher/fixture only; assertions/call sequence selected by reviewer.
Do not install a wheel or use any other run's server/control directory.
"""
import argparse, asyncio, copy, hashlib, importlib.util, json, math, os, shutil, sys, traceback
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def oracle(values, times, k, rho):
    errors=[abs(values[0][0][i][j]-(300+10*math.sin(math.pi*x/.05)*math.exp(-k/rho*(math.pi/.05)**2*t)))
            for i,t in enumerate(times) for j,x in enumerate((.0125,.025,.0375))]
    assert max(errors)<=.03, errors
    return max(errors)

async def run(a):
    assert sha(a.wheel)==a.wheel_sha256, 'Candidate wheel changed'
    spec=importlib.util.spec_from_file_location('review_launcher',a.launcher)
    helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
    root=Path(a.work).resolve();root.mkdir(parents=True,exist_ok=False)
    summary={'status':'RUNNING','reviewer':'/root/reviewer','wheel_sha256':a.wheel_sha256,
             'script_sha256':sha(__file__),'launcher_sha256':sha(a.launcher),'version_requested':a.version}
    server=helper.LiveComsolServerInstance(a.version,Path(a.comsol),Path(a.jdk),root/'runtime')
    def log(row):
        with (root/'transcript.jsonl').open('a',encoding='utf8') as f:f.write(json.dumps(row,allow_nan=False,default=str)+'\n')
    try:
        port=server.start();summary['actual_engine']=server.worker.client().getComsolVersion();server.stop_worker()
        project=root/'project';project.mkdir()
        fixture=project/'W21Fixture.java';shutil.copyfile(a.fixture,fixture)
        env=dict(os.environ);env.pop('PYTHONPATH',None)
        env.update(COMSOL_SERVER_MCP_HOME=str(root/'control'),COMSOL_ROOT=a.comsol,COMSOL_PREFS_DIR=str(server.prefs_dir),
                   COMSOL_PROJECT_ROOT=str(project),COMSOL_MCP_ISOLATION_RECEIPT=str(server.receipt_file),COMSOL_MCP_TRUSTED_CODE='1',
                   COMSOL_JAVA_HOME=a.jdk,JAVA_HOME=a.jdk,COMSOL_SERVER_VERSION=a.version)
        params=StdioServerParameters(command=str(Path(sys.executable).parent/'comsol-mcp.exe'),args=[],env=env,cwd=str(root))
        async with stdio_client(params) as (read,write):
            async with ClientSession(read,write) as session:
                await session.initialize();binding={}
                async def call(tool,body,bound=True,negative=False):
                    request=copy.deepcopy(body)
                    execution=dict(binding) if bound else {}
                    execution.update(project_id='independent-review',rpc_timeout_s=300,execution_timeout_s=None)
                    request['execution']=execution
                    log({'direction':'request','tool':tool,'arguments':request})
                    r=await session.call_tool(tool,arguments=request)
                    log({'direction':'response','tool':tool,'result':r.model_dump(mode='json',by_alias=True)})
                    data=r.structuredContent or json.loads(r.content[0].text)
                    ex=data.get('execution',{})
                    if ex.get('model_ref'):binding.update(model_ref=ex['model_ref'],expected_revision=ex.get('revision'),session_id=ex['model_ref']['session_id'])
                    if not negative:assert not r.isError and data.get('success'),data
                    print(tool,data.get('success'),flush=True)
                    return data
                async def sample(dataset):
                    return await call('operation_call',{'operation_id':'result.at_points','arguments':{
                        'spec':{'expressions':['T'],'solution':{'dataset':dataset}},'frame':'spatial',
                        'points':[{'x':x,'y':.005,'z':.005} for x in (.0125,.025,.0375)],'coordinate_unit':'m'}})
                await call('server_connect',{'host':'127.0.0.1','port':port},bound=False)
                if a.reopen:
                    prior=json.loads((Path(a.reopen)/'summary.json').read_text())
                    saved=Path(a.reopen)/'project'/'review.mph';assert sha(saved)==prior['saved_sha256']
                    copied=project/'review_reopen.mph';shutil.copyfile(saved,copied);assert sha(copied)==prior['saved_sha256']
                    loaded=await call('model_load',{'path':str(copied)},bound=False)
                    assert loaded['data']['load_mode']=='loaded'
                    summary['new_binding']=dict(binding);summary['saved_sha256']=sha(copied)
                    summary['fresh_samples']=[]
                    for old in prior['saved_samples']:
                        fresh=(await sample(old['dataset']))['data']
                        assert fresh['values']==old['values']
                        assert fresh['observation_ref']!=old['observation_ref']
                        validation=await call('validate.solution',{'observation_ref':fresh['observation_ref'],'criteria':{'range':[299.9,310.1]}})
                        assert validation['data']['numerical_verification_status']=='PASS'
                        assert validation['data']['physical_validation_status']=='UNVERIFIED'
                        summary['fresh_samples'].append(fresh)
                    bad=await call('model_load',{'path':str(project/'missing.mph')},bound=False,negative=True)
                    assert not bad.get('success');summary['wrong_file_rejected']=True
                    await call('model_create',{'name':'ReviewerEmpty'},bound=False)
                    bad=await call('operation_call',{'operation_id':'result.at_points','arguments':{
                        'spec':{'expressions':['T'],'solution':{'dataset':'dset1'}},'frame':'spatial','points':[{'x':.025,'y':.005,'z':.005}],'coordinate_unit':'m'}},negative=True)
                    assert not bad.get('success');summary['empty_solution_rejected']=True
                else:
                    await call('model_create',{'name':'ReviewerIndependent'},bound=False)
                    await call('operation_call',{'operation_id':'code.execute_java','arguments':{'source_artifact':str(fixture),'entrypoint':'W21Fixture','mode':'trusted','arguments':{}}})
                    sampling={'spec':{'expressions':['T'],'solution':{'dataset':'dset1'}},'points':[[.0125,.005,.005],[.025,.005,.005],[.0375,.005,.005]],'coordinate_unit':'m'}
                    definition={'parameters':{'k':[381.,399.],'rhoCp':[3500000.]},'units':{'k':'W/(m*K)','rhoCp':'J/(m^3*K)'},
                                'sample':sampling,'metrics':{'T_mid':{'expression':'T','unit':'K','indices':[0,4,1]},'objective':{'expression':'T','unit':'K','indices':[0,4,1],'target':304}},
                                'times':[0,.5,1,1.5,2],'validation':{'range':[299.9,310.1]}}
                    first=await call('study.sweep_manage',{'study':'std1','definition':definition,'max_cases':2})
                    cases=first['data']['cases'];assert len(cases)==2 and all(c['status']=='COMPLETED' for c in cases)
                    summary['normal_errors']=[oracle(c['sample']['values'],c['solution_indices']['time_values'],c['parameters']['k'],c['parameters']['rhoCp']) for c in cases]
                    for ref,extra,expected in [({'observation_id':'reviewer_unregistered_9f37','sha256':'b'*64},{},'UNREGISTERED_OBSERVATION'),
                                              (cases[-1]['observation_ref'],{'criteria':{'values':[304]}},'OBSERVATION_OVERRIDE_REFUSED'),
                                              (cases[0]['observation_ref'],{},'STALE_SOLUTION')]:
                        bad=await call('validate.solution',dict({'observation_ref':ref,'criteria':{'range':[299.9,310.1]}},**extra),negative=True)
                        assert bad['data']['scope']==expected,bad
                    repeated=await call('study.sweep_manage',{'study':'std1','definition':definition,'max_cases':2})
                    assert repeated['data']['cache_hits']==2 and repeated['data']['budget']['cases_evaluated']==0
                    summary['repeat']=repeated['data']
                    checkpoint=await call('stage.checkpoint_create',{'stage_id':'review-source','timestamp_s':2,'units':{'T':'K'},'arguments':{'sample':sampling}})
                    target=copy.deepcopy(sampling);target['spec']['solution']['dataset']='dset2'
                    bad=await call('stage.state_transfer',{'checkpoint_id':checkpoint['data']['checkpoint_id'],'target_stage_id':'std2','variable_mapping':{'T':'wrong_variable'},'arguments':{'target_sample':target,'initial_tolerance':.001}},negative=True)
                    assert not bad.get('success')
                    transfer=await call('stage.state_transfer',{'checkpoint_id':checkpoint['data']['checkpoint_id'],'target_stage_id':'std2','variable_mapping':{'T':'T'},'arguments':{'target_sample':target,'initial_tolerance':.001}})
                    stage=transfer['data'];assert stage['history_preserved'] and stage['initial_max_abs_error']<=.001
                    summary['stage_error']=oracle(stage['target_sample']['values'],stage['solution_indices']['time_values'],399.,3500000.)
                    summary['stage']=stage
                    normal_opt=await call('optimization.bounded_run',{'objective_name':'objective','parameter_bounds':{'k':[381,399],'rhoCp':[3500000,3500000]},'constraints':[{'expression':'T_mid','min_value':300,'max_value':310}],
                                                                  'max_cases':2,'study':'std1','definition':definition,'grid_points_per_dim':2})
                    best=normal_opt['data']['best_candidate'];assert best and best['is_feasible'] and normal_opt['data']['compute_budget']['cases_evaluated']<=2
                    for candidate in normal_opt['data']['all_candidates']:
                        raw=candidate['raw_results'];assert raw['status'] in ('COMPLETED','CACHED')
                        oracle(raw['sample']['values'],raw['solution_indices']['time_values'],candidate['parameters']['k'],candidate['parameters']['rhoCp'])
                    summary['optimization']=normal_opt['data']
                    summary['saved_samples']=[(await sample('dset1'))['data'],(await sample('dset2'))['data']]
                    await call('save_model',{'path':str(project/'review.mph')})
                    summary['saved_sha256']=sha(project/'review.mph');summary['saved_binding']=dict(binding)
                    changed=copy.deepcopy(definition);changed['input_artifacts']=['review-input.txt']
                    summary['budget_runs']=[]
                    for content in ('review-A','review-B'):
                        (project/'review-input.txt').write_text(content)
                        b=(await call('study.sweep_manage',{'study':'std1','definition':changed,'max_cases':1},negative=True))['data']
                        assert b['budget']['cases_evaluated']==1 and b['cache_hits']==0
                        assert b['cases'][0]['status']=='COMPLETED' and b['cases'][1]['status']=='NOT_RUN' and 'solve' not in b['cases'][1]
                        summary['budget_runs'].append(b)
                    failed=copy.deepcopy(definition);failed['sample']['spec']['expressions']=['review_missing_expression']
                    opt=await call('optimization.bounded_run',{'objective_name':'objective','parameter_bounds':{'k':[381,399],'rhoCp':[3500000,3500000]},'constraints':[{'expression':'T_mid','min_value':300,'max_value':310}],
                                                           'max_cases':1,'study':'std1','definition':failed})
                    assert opt['data']['best_candidate'] is None and opt['data']['all_candidates'][0]['raw_results']['status']=='FAILED'
                    summary['failed_best_rejected']=True
                summary['status']='REVIEW_PROBES_PASS'
    except Exception as e:
        summary.update(status='FAILED',error=str(e),traceback=traceback.format_exc());print(summary['traceback'],flush=True)
    finally:
        from comsol_mcp._platform_process import process_identity,terminate_process_tree
        endpoint=root/'control'/'control-private'/'control.json'
        if endpoint.exists():
            e=json.loads(endpoint.read_text());actual=process_identity(e['pid'])
            if actual['alive'] and actual['start_epoch_ms']==e.get('process_start_epoch_ms'):
                summary['owned_daemon_stopped']=terminate_process_tree(e['pid'])
        server.stop();(root/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False,default=str))
    return 0 if summary['status']=='REVIEW_PROBES_PASS' else 1

if __name__=='__main__':
    p=argparse.ArgumentParser()
    for name in ('work','version','comsol','jdk','fixture','wheel','wheel-sha256','launcher'):p.add_argument('--'+name,required=True)
    p.add_argument('--reopen');raise SystemExit(asyncio.run(run(p.parse_args())))
