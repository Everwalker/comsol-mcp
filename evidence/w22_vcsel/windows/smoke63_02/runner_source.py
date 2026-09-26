"""Windows W22 public wheel/stdio runner; every engine action is managed MCP."""
import argparse, asyncio, hashlib, itertools, json, os, shutil, sys, time, traceback
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from run_w21_native import LiveComsolServerInstance
from comsol_mcp._vcsel_source import prepare_basis, impossible_power_bound

FIELDS={'T':'K','roi_mean':'K','roi_std':'K','roi_cv_guarded':'1','roi_max':'K','roi_min':'K','roi_area':'m^2',
        'Pinc':'W','Pabs':'W','Proi':'W','Pout':'W','Pstore':'W','U':'J'}

def definition(Ls,inputs):
    expressions=list(FIELDS)
    return {'parameters':{'L':Ls,'p0':[.8],'p1':[.75]},'units':{'L':'m','p0':'W','p1':'W'},
        'sample':{'spec':{'expressions':expressions,'solution':{'dataset':'dset1'}},
                  'points':[[0,0,.001]],'coordinate_unit':'m'},
        'metrics':{name:{'expression':name,'unit':unit,'indices':[0,4,0]} for name,unit in FIELDS.items()},
        'times':[0,1,5,20,60],'validation':{'range':[-1e6,1e8]},'input_artifacts':inputs}

async def run(args):
    import comsol_mcp
    root=Path(args.work).resolve(); root.mkdir(parents=True,exist_ok=False)
    project=root/'project'; project.mkdir()
    root.joinpath('runner_source.py').write_bytes(Path(__file__).read_bytes())
    fixture=Path(args.fixture).resolve(); source=project/fixture.name; shutil.copyfile(fixture,source)
    outputs=project/'W22Outputs.java'; shutil.copyfile(fixture.with_name('W22Outputs.java'),outputs)
    spec=json.loads(Path(args.spec).read_text()); manifest=prepare_basis(spec['fixture'],project/'source')
    (project/'benchmark_spec.json').write_bytes(Path(args.spec).read_bytes())
    origin={'package':comsol_mcp.__file__,'python':sys.executable,'runner_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'wheel_sha256':hashlib.sha256(fixture.with_name('comsol_mcp-0.1.9-py3-none-any.whl').read_bytes()).hexdigest(),
            'benchmark_sha256':hashlib.sha256(Path(args.spec).read_bytes()).hexdigest()}
    (root/'package_origin.json').write_text(json.dumps(origin,indent=2))
    server=LiveComsolServerInstance(args.version,Path(args.comsol),Path(args.jdk),root/'runtime')
    summary={'status':'RUNNING','physical_validation':'UNVERIFIED','version_requested':args.version,'cases':[]}
    def log(row):
        with (root/'transcript.jsonl').open('a',encoding='utf-8') as out:out.write(json.dumps(row,default=str,allow_nan=False)+'\n')
    try:
        port=server.start(); summary['engine']=server.worker.client().getComsolVersion();server.stop_worker()
        env=dict(os.environ);env.pop('PYTHONPATH',None)
        env.update(COMSOL_SERVER_MCP_HOME=str(root/'control'),COMSOL_ROOT=args.comsol,COMSOL_PREFS_DIR=str(server.prefs_dir),
            COMSOL_PROJECT_ROOT=str(project),COMSOL_MCP_ISOLATION_RECEIPT=str(server.receipt_file),COMSOL_MCP_TRUSTED_CODE='1',
            COMSOL_JAVA_HOME=args.jdk,JAVA_HOME=args.jdk,COMSOL_SERVER_VERSION=args.version)
        params=StdioServerParameters(command=str(Path(sys.executable).parent/'comsol-mcp.exe'),args=[],env=env,cwd=str(root))
        async with stdio_client(params) as (read,write):
            async with ClientSession(read,write) as session:
                await session.initialize();binding={}
                async def call(name,body,bound=True,allow_error=False):
                    if name in {'result.at_points','study.run'}:
                        if name=='result.at_points':body=dict(body,frame='spatial',points=[dict(zip(('x','y','z'),p)) if isinstance(p,list) else p for p in body['points']])
                        body={'operation_id':name,'arguments':body};name='operation_call'
                    execution=dict(binding) if bound else {}; execution.update(project_id='w22vcsel',rpc_timeout_s=300,execution_timeout_s=None)
                    request=dict(body,execution=execution);log({'direction':'request','tool':name,'arguments':request})
                    result=await session.call_tool(name,arguments=request);log({'direction':'response','tool':name,'result':result.model_dump(mode='json',by_alias=True)})
                    data=result.structuredContent or json.loads(result.content[0].text); ex=data.get('execution',{})
                    if ex.get('model_ref'):binding.update(model_ref=ex['model_ref'],expected_revision=ex.get('revision'),session_id=ex['model_ref']['session_id'])
                    print(name,data.get('success'),str(data.get('error',''))[:250],flush=True)
                    if not allow_error and (result.isError or not data.get('success')):raise RuntimeError(json.dumps(data,default=str)[:3000])
                    return data
                await call('server_connect',{'host':'127.0.0.1','port':port},False)
                await call('model_create',{'name':'W22Owned'},False)
                summary['build']=await call('operation_call',{'operation_id':'code.execute_java','arguments':{'source_artifact':str(source),'entrypoint':'W22Fixture','mode':'trusted','arguments':{'sources':manifest['sources'],'source_root':str(project/'source')}}})
                inputs=['source/manifest.json','benchmark_spec.json']+['source/'+s['file'] for s in manifest['sources']]
                d=definition([.02],inputs)
                candidates=list(itertools.product(spec['search']['L_m'],spec['search']['p_center_W'],spec['search']['p_ring1_W'])) if args.full else [(.02,.8,.75)]
                if args.candidate: candidates=[tuple(json.loads(args.candidate)[k] for k in ['L','p0','p1'])]
                started=time.monotonic();summary['sweeps']=[]
                for ordinal,(L,p0,p1) in enumerate(candidates,1):
                    assert time.monotonic()-started<10800, 'search wall budget exhausted'
                    d=definition([L],inputs);d['parameters'].update(p0=[p0],p1=[p1])
                    result=await call('study.sweep_manage',{'study':'std1','definition':d,'max_cases':1,'max_wall_time_s':3600})
                    summary['sweeps'].append(result);case=result['data']['cases'][0]
                    assert case['status']=='COMPLETED', 'case failure: '+str(case)[:2000]
                    case['roi_cv']=case['roi_std']/case['roi_mean'] if case['roi_mean']>1e-9 else None
                    case['roi_cv_status']='DEFINED' if case['roi_cv'] is not None else 'UNDEFINED_NEAR_ZERO_MEAN'
                    case['search_ordinal']=ordinal;summary['cases'].append(case)
                    (root/'progress.json').write_text(json.dumps(summary,indent=2,default=str,allow_nan=False))
                    print('CASE_COMPLETED',ordinal,case['parameters'],case['roi_cv'],flush=True)
                for c in summary['cases']:
                    assert abs(c['roi_area']-3.141592653589793*.015**2)<1e-7,'ROI area mismatch'
                    assert abs(c['Pabs']-c['Pout']-c['Pstore'])<=.02*c['Pabs'],'energy balance'
                feasible=[c for c in summary['cases'] if c['roi_mean']>=spec['search']['mean_deltaT_min_K'] and 0<=c['parameters']['p0']<=1 and 0<=c['parameters']['p1']<=1 and 0<=(12.45-c['parameters']['p0']-6*c['parameters']['p1'])/11<=1]
                summary['best_candidate']=min(feasible,key=lambda c:c['roi_cv']) if feasible else None
                summary['optimality']='BEST_VERIFIED_FEASIBLE_SO_FAR' if feasible else 'NO_FEASIBLE_FOUND'
                summary['impossible_control']=impossible_power_bound(spec['fixture'],1,20)
                best=summary['best_candidate'] or summary['cases'][-1]
                # A cached record never restores a previous native solution. Re-solve selected best.
                if args.full:
                    await call('operation_call',{'operation_id':'parameter.set','arguments':{'parameters':[{'name':k,'expression':str(v)+'['+d['units'][k]+']','unit':d['units'][k]} for k,v in best['parameters'].items()]}})
                    summary['best_fresh_solve']=await call('study.run',{'study':{'segments':[{'collection':'study','tag':'std1'}]}})
                summary['final_sample']=await call('result.at_points',d['sample'])
                summary['final_parameters']=best['parameters']
                probe={'spec':{'expressions':['incident','qabs'],'solution':{'dataset':'dset1'}},'points':[[x,y,.001] for x,y in spec['source_probes_m']],'coordinate_unit':'m'}
                summary['source_probes']=await call('result.at_points',probe)
                probe['points']=[[1000*v for v in row] for row in probe['points']];probe['coordinate_unit']='mm'
                summary['source_probes_mm']=await call('result.at_points',probe)
                outside=[src['function']+'(0.026[m],0[m])' for src in manifest['sources']]
                summary['outside_probes']=await call('result.at_points',{'spec':{'expressions':outside,'solution':{'dataset':'dset1'}},'points':[[0,0,.001]],'coordinate_unit':'m'})
                summary['plot_setup']=await call('operation_call',{'operation_id':'code.execute_java','arguments':{'source_artifact':str(outputs),'entrypoint':'W22Outputs','mode':'trusted','arguments':{'plots':True}}})
                summary['plots']=[]
                for tag in ['sourceplot','temperatureplot']:
                    summary['plots'].append(await call('plot_render',{'path':tag,'options':{'destination':str(project/(tag+'.png')),'width':1000,'height':800,'format':'png'}}))
                summary['save']=await call('save_model',{'path':str(project/'w22_final.mph')})
                summary['saved_sha256']=hashlib.sha256((project/'w22_final.mph').read_bytes()).hexdigest()
                summary['status']='NATIVE_SMOKE_COMPLETED' if not args.full else 'SEARCH_COMPLETED'
    except BaseException:
        summary['status']='FAILED';summary['error']=traceback.format_exc();print(summary['error'],flush=True)
    finally:
        (root/'summary.json').write_text(json.dumps(summary,indent=2,default=str,allow_nan=False));server.stop()
    return 0 if summary['status']!='FAILED' else 1

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--work',required=True);p.add_argument('--version',required=True);p.add_argument('--comsol',required=True);p.add_argument('--jdk',required=True);p.add_argument('--fixture',required=True);p.add_argument('--spec',required=True);p.add_argument('--full',action='store_true');p.add_argument('--candidate')
    raise SystemExit(asyncio.run(run(p.parse_args())))
