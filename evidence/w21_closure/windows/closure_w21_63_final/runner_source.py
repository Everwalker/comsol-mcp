"""Native Windows W21 runner. Real wheel stdio entry, no fake service."""
import argparse, asyncio, hashlib, json, math, os, shutil, subprocess, sys, time, traceback
from pathlib import Path
from datetime import datetime, timezone
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from comsol_mcp._java_worker import JavaWorkerPaths, PersistentJavaWorker
class LiveComsolServerInstance:
    """Manages an authentic COMSOL Multiphysics server process and persistent Java worker."""

    def __init__(self, version: str, comsol_root: Path, jdk_home: Path, work_dir: Path) -> None:
        self.version = version
        self.comsol_root = comsol_root
        self.jdk_home = jdk_home
        self.work_dir = work_dir
        self.prefs_dir = work_dir / "prefs"
        self.tmp_dir = work_dir / "tmp"
        self.recovery_dir = work_dir / "recovery"
        self.worker_dir = work_dir / "worker"
        for d in (self.prefs_dir, self.tmp_dir, self.recovery_dir, self.worker_dir):
            d.mkdir(parents=True, exist_ok=True)

        self.proc: subprocess.Popen | None = None
        self.port: int | None = None
        self.worker: PersistentJavaWorker | None = None

    def private_launcher(self):
        """New task-owned tree: shared install remains read-only.

        Directory junctions reference installed binaries. Only the small
        server configuration tree is copied and restricted to loopback.
        """
        private_root=self.work_dir/'engine'
        private_root.mkdir()
        def junction(source,target):
            subprocess.run(['cmd','/c','mklink','/J',str(target),str(source)],check=True,stdout=subprocess.DEVNULL)
        for entry in self.comsol_root.iterdir():
            if entry.name=='bin': continue
            if entry.is_dir(): junction(entry,private_root/entry.name)
            else: shutil.copyfile(entry,private_root/entry.name)
        private_bin=private_root/'bin'; private_bin.mkdir()
        for entry in (self.comsol_root/'bin').iterdir():
            if entry.name=='servers':
                shutil.copytree(entry,private_bin/'servers')
            elif entry.is_dir(): junction(entry,private_bin/entry.name)
            else: shutil.copyfile(entry,private_bin/entry.name)
        import xml.etree.ElementTree as ET
        xml=private_bin/'servers'/'webbridge'/'conf'/'server.xml'
        source_xml=self.comsol_root/'bin'/'servers'/'webbridge'/'conf'/'server.xml'
        original=hashlib.sha256(source_xml.read_bytes()).hexdigest()
        tree=ET.parse(xml)
        connectors=tree.findall('.//Connector')
        assert len(connectors)==1, 'Unexpected server connectors'
        connectors[0].set('address','127.0.0.1')
        tree.write(xml,encoding='utf-8',xml_declaration=True)
        assert hashlib.sha256(source_xml.read_bytes()).hexdigest()==original
        (self.work_dir/'private_runtime.json').write_text(json.dumps({'installed_xml_sha256':original,'private_xml_sha256':hashlib.sha256(xml.read_bytes()).hexdigest(),'installed_root':str(self.comsol_root),'private_root':str(private_root)}))
        return private_bin/'win64'/'comsolmphserver.exe'

    def start(self) -> int:
        portfile = self.work_dir / "server.port"
        if portfile.exists():
            portfile.unlink()

        server_exe = self.comsol_root / "bin" / "win64" / "comsolmphserver.exe" if sys.platform == "win32" else self.comsol_root / "bin" / "comsol"
        if sys.platform == "win32":
            cmd = [
                str(self.private_launcher()),
                "-port", "0",
                "-portfile", str(portfile),
                "-prefsdir", str(self.prefs_dir),
                "-tmpdir", str(self.tmp_dir),
                "-recoverydir", str(self.recovery_dir),
                "-login", "auto",
                "-silent",
                "-multi", "on",
            ]
        else:
            cmd = [
                str(server_exe),
                "mphserver",
                "-port", "0",
                "-portfile", str(portfile),
                "-prefsdir", str(self.prefs_dir),
                "-tmpdir", str(self.tmp_dir),
                "-recoverydir", str(self.recovery_dir),
                "-login", "auto",
                "-silent",
                "-multi", "on",
            ]

        self.proc = subprocess.Popen(cmd)
        deadline = time.time() + 45
        while time.time() < deadline:
            if portfile.exists():
                try:
                    p = int(portfile.read_text().strip())
                    if p > 0:
                        self.port = p
                        break
                except Exception:
                    pass
            time.sleep(0.5)

        if not self.port:
            self.stop()
            raise TimeoutError(f"COMSOL {self.version} mphserver failed to bind port within 45s")

        paths = JavaWorkerPaths(self.comsol_root, self.jdk_home, private_prefs=self.prefs_dir, project_root=self.work_dir)
        self.worker = PersistentJavaWorker(paths, state_dir=self.worker_dir)
        self.worker.start()
        self.worker.client().connect(self.port, "127.0.0.1")

        try:
            from comsol_mcp._g2_isolation import _process_snapshot
            if self.proc is not None:
                snap = _process_snapshot(self.proc.pid)
                if snap is not None:
                    snap["port"] = self.port
                    receipt = {
                        "schema_version": 2,
                        "status": "RUNNING",
                        "process": snap,
                    }
                    receipt_file = self.work_dir / "isolation_receipt.json"
                    receipt_file.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
                    self.receipt_file = receipt_file
        except Exception as exc:
            print(f"Warning: could not write isolation receipt: {exc}")

        return self.port

    def stop_worker(self) -> None:
        if self.worker is not None:
            try:
                self.worker.client().disconnect()
            except Exception:
                pass
            try:
                self.worker.close()
            except Exception:
                pass
            self.worker = None

    def stop(self) -> None:
        self.stop_worker()

        if self.proc is not None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=5)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
            self.proc = None



async def run(args):
    import comsol_mcp
    root = Path(args.work).resolve()
    root.mkdir(parents=True, exist_ok=False)
    (root/'runner_source.py').write_bytes(Path(__file__).read_bytes())
    (root/'package_origin.json').write_text(json.dumps({'package':comsol_mcp.__file__, 'python':sys.executable, 'runner_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), 'wheel_sha256':hashlib.sha256(Path(args.fixture).with_name('comsol_mcp-0.1.9-py3-none-any.whl').read_bytes()).hexdigest()}))
    server = LiveComsolServerInstance(args.version, Path(args.comsol), Path(args.jdk), root/'runtime')
    transcript = root/'transcript.jsonl'
    summary = {'status':'RUNNING', 'version_requested':args.version}
    def log(row):
        with transcript.open('a',encoding='utf-8') as f: f.write(json.dumps(row,default=str,allow_nan=False)+'\n')
    try:
        port = server.start()
        summary['engine'] = server.worker.client().getComsolVersion()
        server.stop_worker()
        env = dict(os.environ)
        env.pop('PYTHONPATH',None)
        env.update(COMSOL_SERVER_MCP_HOME=str(root/'control'), COMSOL_ROOT=args.comsol,
                   COMSOL_PREFS_DIR=str(server.prefs_dir), COMSOL_PROJECT_ROOT=str(root/'project'),
                   COMSOL_MCP_ISOLATION_RECEIPT=str(server.receipt_file), COMSOL_MCP_TRUSTED_CODE='1',
                   COMSOL_JAVA_HOME=args.jdk, JAVA_HOME=args.jdk, COMSOL_SERVER_VERSION=args.version)
        project = root/'project'; project.mkdir()
        source = project/'W21Fixture.java'; source.write_bytes(Path(args.fixture).read_bytes())
        params = StdioServerParameters(command=str(Path(sys.executable).parent/'comsol-mcp.exe'), args=[], env=env, cwd=str(root))
        async with stdio_client(params) as (read,write):
            async with ClientSession(read,write) as session:
                await session.initialize()
                binding = {}
                async def call(name, body, *, bound=True, allow_error=False):
                    if name=='result.at_points':
                        body=dict(body,frame='spatial',points=[dict(zip(('x','y','z'),p)) if isinstance(p,list) else p for p in body['points']])
                    if name in {'result.at_points','study.run'}:
                        body={'operation_id':name,'arguments':body};name='operation_call'
                    execution = dict(binding) if bound else {}
                    execution.update(project_id='w21closure',rpc_timeout_s=300,execution_timeout_s=None)
                    request = dict(body,execution=execution)
                    log({'direction':'request','tool':name,'arguments':request})
                    result = await session.call_tool(name,arguments=request)
                    raw=result.model_dump(mode='json',by_alias=True)
                    log({'direction':'response','tool':name,'result':raw})
                    data=result.structuredContent
                    if data is None:
                        data=json.loads(result.content[0].text)
                    ex=data.get('execution',{})
                    if ex.get('model_ref'):
                        binding.update(model_ref=ex['model_ref'],expected_revision=ex.get('revision'),session_id=ex['model_ref']['session_id'])
                    print(name, data.get('success'), str(data.get('error',''))[:250],flush=True)
                    if not allow_error and (result.isError or not data.get('success')):
                        raise RuntimeError(name+': '+json.dumps(data,default=str)[:1600])
                    return data
                await call('server_connect',{'host':'127.0.0.1','port':port},bound=False)
                if args.reopen:
                    prior=json.loads((Path(args.reopen)/'summary.json').read_text())
                    saved=Path(args.reopen)/'project'/'w21_final.mph'
                    copy=project/'reopened.mph';shutil.copyfile(saved,copy)
                    summary['saved_sha256']=hashlib.sha256(copy.read_bytes()).hexdigest()
                    assert summary['saved_sha256']==prior['saved_sha256']
                    summary['load']=await call('model_load',{'path':str(copy)},bound=False)
                    assert summary['load']['data']['load_mode']=='loaded'
                    summary['samples']=[]
                    for previous in prior['reopen_samples']:
                        current=await call('result.at_points',{'spec':{'expressions':previous['expressions'],'solution':{'dataset':previous['dataset']}},'points':previous['points'],'coordinate_unit':previous['coordinate_unit']})
                        assert current['data']['values']==previous['values'], 'Saved field changed in fresh Worker'
                        validation=await call('validate.solution',{'observation_ref':current['data']['observation_ref'],'criteria':prior.get('reopen_criteria',{'range':[299.9,310.1]}),'observation_selectors':prior.get('reopen_selectors',{})})
                        assert validation['data']['numerical_verification_status']=='PASS'
                        summary['samples'].append({'sample':current,'validation':validation})
                    summary['wrong_file_negative']=await call('model_load',{'path':str(project/'does_not_exist.mph')},bound=False,allow_error=True)
                    assert not summary['wrong_file_negative']['success']
                    await call('model_create',{'name':'EmptyReopenNegative'},bound=False)
                    summary['empty_solution_negative']=await call('result.at_points',{'spec':{'expressions':['T'],'solution':{'dataset':'dset1'}},'points':[[.025,.005,.005]],'coordinate_unit':'m'},allow_error=True)
                    assert not summary['empty_solution_negative']['success']
                    summary['cross_model_negative']=await call('validate.solution',{'observation_ref':summary['samples'][0]['sample']['data']['observation_ref'],'criteria':{'range':[299.9,310.1]}},allow_error=True)
                    assert summary['cross_model_negative']['data']['scope']=='CROSS_MODEL_REFUSED'
                    summary['status']='SMOKE_COMPLETED'
                elif args.budget_only:
                    await call('model_create',{'name':'W21BudgetOwned'},bound=False)
                    await call('operation_call',{'operation_id':'code.execute_java','arguments':{'source_artifact':str(source),'entrypoint':'W21Fixture','mode':'trusted','arguments':{}}})
                    sample={'spec':{'expressions':['T'],'solution':{'dataset':'dset1'}},'points':[[.0125,.005,.005],[.025,.005,.005],[.0375,.005,.005]],'coordinate_unit':'m'}
                    definition={'parameters':{'k':[380,400],'rhoCp':[3400000,3800000]},'units':{'k':'W/(m*K)','rhoCp':'J/(m^3*K)'},'sample':sample,
                        'metrics':{'T_mid':{'expression':'T','unit':'K','indices':[0,4,1]},'objective':{'expression':'T','unit':'K','indices':[0,4,1],'target':304}},
                        'times':[0,.5,1,1.5,2],'validation':{'range':[299.9,310.1]},'input_artifacts':['cache-input.txt']}
                    summary['budget_runs']=[]
                    for payload,expected_hits in [('input-A',0),('input-A',1),('input-B',0)]:
                        (project/'cache-input.txt').write_text(payload)
                        response=await call('study.sweep_manage',{'study':'std1','definition':definition,'max_cases':1})
                        b=response['data'];summary['budget_runs'].append(response)
                        assert b['completion_status']=='BUDGET_EXHAUSTED'
                        assert b['budget']['cases_evaluated']==1 and b['cache_hits']==expected_hits
                        assert b['cases'][-1]['status']=='NOT_RUN' and 'solve' not in b['cases'][-1]
                    failed=json.loads(json.dumps(definition));failed['sample']['spec']['expressions']=['W21_missing_variable']
                    response=await call('optimization.bounded_run',{'objective_name':'objective','parameter_bounds':{'k':[380,400],'rhoCp':[3400000,3800000]},
                        'constraints':[{'expression':'T_mid','min_value':300,'max_value':310}],'max_cases':1,'study':'std1','definition':failed})
                    summary['failed_candidate']=response
                    assert response['data']['best_candidate'] is None and response['data']['all_candidates'][0]['raw_results']['status']=='FAILED'
                    summary['status']='SMOKE_COMPLETED'
                elif args.refinement:
                    summary['refinement']={}
                    fixture=project/'W20RefinementFixture.java'
                    fixture.write_bytes(Path(args.fixture).with_name('W20RefinementFixture.java').read_bytes())
                    stats=project/'W20SolutionStats.java'
                    stats.write_bytes(Path(args.fixture).with_name('W20SolutionStats.java').read_bytes())
                    sample={'spec':{'expressions':['T'],'solution':{'dataset':'dset1'}},
                            'points':[[.25,.05,.05],[.5,.05,.05],[.75,.05,.05]],'coordinate_unit':'m'}
                    for mode,levels in [('mesh',[(8,'1e-8'),(16,'1e-8'),(32,'1e-8')]),('time',[(32,'1e-3'),(32,'1e-5'),(32,'1e-7')])]:
                        cases=[];summary['refinement'][mode]=cases
                        for level,(n,rtol) in enumerate(levels,1):
                            await call('model_create',{'name':f'W20_{mode}_{level}'},bound=False)
                            build=await call('operation_call',{'operation_id':'code.execute_java','arguments':{'source_artifact':str(fixture),'entrypoint':'W20RefinementFixture','mode':'trusted','arguments':{'n':n,'rtol':rtol}}})
                            solve=await call('study.run',{'study':{'segments':[{'collection':'study','tag':'std1'}]}})
                            current=await call('result.at_points',sample)
                            selectors={f'T_{x}_{t}':[0,0,i+1,j] for i,t in enumerate([.01,.03,.1]) for j,x in enumerate([.25,.5,.75])}
                            validation=await call('validate.solution',{'observation_ref':current['data']['observation_ref'],
                                'solution':{'dataset':'dset1'},'observation_selectors':selectors,'criteria':{'oracle':'transient_sine_diffusion'}},allow_error=True)
                            values=current['data']['values'][0][0]
                            error=max(abs(values[i+1][j]-(300+10*math.sin(math.pi*x)*math.exp(-math.pi**2*t)))
                                      for i,t in enumerate([.01,.03,.1]) for j,x in enumerate([.25,.5,.75]))
                            measured=await call('operation_call',{'operation_id':'code.execute_java','arguments':{'source_artifact':str(stats),'entrypoint':'W20SolutionStats','mode':'trusted','arguments':{}}})
                            case={'level':level,'axial_elements':n,'mesh_size_metric':1/n,'rtol':float(rtol),'error':error,'build':build,'solve':solve,'sample':current,'validation':validation,'native_stats':measured}
                            cases.append(case)
                            assert validation['data']['numerical_verification_status']=='PASS' and error<=.1
                        refs=[c['validation']['data']['convergence_ref'] for c in cases]
                        convergence=await call('validate.convergence',{'cases':refs,
                                                'criteria':{'monotonic':True,'target_error':.1}},allow_error=True)
                        summary['refinement'][mode+'_convergence']=convergence
                        assert convergence['data']['scope']=='REGISTERED_NATIVE_REFINEMENT'
                        assert len(convergence['data']['registered_cases'])==3
                        for measured_case,registered_case in zip(cases,convergence['data']['registered_cases']):
                            assert abs(measured_case['error']-registered_case['error'])<1e-12
                        negatives={
                            'forged_arrays':[{'level':i+1,'error':e,'mesh_size_metric':1/(i+1)} for i,e in enumerate([.065,.028,.009])],
                            'repeated_ref':[refs[0]]*3,
                            'error_override':[dict(ref,error=0.0) for ref in refs],
                        }
                        # A second valid observation of the same grid is not refinement.
                        current=await call('result.at_points',sample)
                        repeated_validation=await call('validate.solution',{'observation_ref':current['data']['observation_ref'],
                            'solution':{'dataset':'dset1'},'observation_selectors':selectors,'criteria':{'oracle':'transient_sine_diffusion'}})
                        negatives['repeated_settings']=[refs[0],refs[-1],repeated_validation['data']['convergence_ref']]
                        for name,negative_cases in negatives.items():
                            negative=await call('validate.convergence',{'cases':negative_cases,'criteria':{'target_error':.1}},allow_error=True)
                            summary['refinement'][mode+'_'+name]=negative
                            assert negative['data']['numerical_verification_status']!='PASS'
                        summary['refinement'][mode+'_threshold_status']='PASS' if max(c['error'] for c in cases)<=.1 else 'FAIL'
                        # Frozen B3 separates trend from absolute target acceptance.
                        assert summary['refinement'][mode+'_threshold_status']=='PASS'
                    summary['reopen_samples']=[current['data']]
                    summary['reopen_criteria']={'oracle':'transient_sine_diffusion'}
                    summary['reopen_selectors']=selectors
                    summary['save']=await call('save_model',{'path':str(project/'w21_final.mph')})
                    summary['saved_sha256']=hashlib.sha256((project/'w21_final.mph').read_bytes()).hexdigest()
                    summary['status']='SMOKE_COMPLETED'
                else:
                    await call('model_create',{'name':'W21Owned'},bound=False)
                    await call('operation_call',{'operation_id':'code.execute_java','arguments':{'source_artifact':str(source),'entrypoint':'W21Fixture','mode':'trusted','arguments':{}}})
                    sample={'spec':{'expressions':['T'],'solution':{'dataset':'dset1'}},
                            'points':[[.0125,.005,.005],[.025,.005,.005],[.0375,.005,.005]],'coordinate_unit':'m'}
                    definition={'parameters':{'k':[380.0,400.0],'rhoCp':[3400000.0,3800000.0]},
                                'units':{'k':'W/(m*K)','rhoCp':'J/(m^3*K)'}, 'sample':sample,
                                'metrics':{'T_mid':{'expression':'T','unit':'K','indices':[0,4,1]},
                                           'objective':{'expression':'T','unit':'K','indices':[0,4,1],'target':304}},
                                'times':[0,.5,1,1.5,2], 'validation':{'range':[299.9,310.1]}}
                    summary['parameter_case']={}
                    for action in ['create','inspect','list','apply']:
                        summary['parameter_case'][action]=await call('parameter.case_manage',{'action':action,'group':'native','case_tag':'baseline',
                            'values':{'k':400,'rhoCp':3800000},'units':definition['units']})
                    sweep = await call('study.sweep_manage',{'study':'std1','definition':definition,'max_cases':4})
                    summary['sweep']=sweep
                    cases=sweep['data']['cases']
                    assert sweep['data']['status']=='COMPLETE' and len(cases)==4
                    def oracle(case, times=None, sample_data=None):
                        times=times or case['solution_indices']['time_values']
                        values=(sample_data or case['sample'])['values'][0][0]
                        k,rho=case['parameters']['k'],case['parameters']['rhoCp']
                        errors=[abs(values[i][j]-(300+10*math.sin(math.pi*x/.05)*math.exp(-k/rho*(math.pi/.05)**2*t)))
                                for i,t in enumerate(times) for j,x in enumerate([.0125,.025,.0375])]
                        assert max(errors)<=.03, errors
                        return max(errors)
                    summary['scan_errors_K']=[oracle(c) for c in cases]
                    summary['provenance_checks']=[]
                    valid=await call('validate.solution',{'observation_ref':cases[-1]['observation_ref'],'criteria':{'range':[299.9,310.1]}})
                    assert valid['data']['physical_validation_status']=='UNVERIFIED'
                    assert valid['execution']['revision']==sweep['execution']['revision'], 'Read-only validation must not stale its own input'
                    summary['provenance_checks'].append(valid)
                    for request,scope in [
                        ({'observation_ref':{'observation_id':'not_registered','sha256':'0'*64},'criteria':{'range':[299.9,310.1]}},'UNREGISTERED_OBSERVATION'),
                        ({'observation_ref':dict(cases[-1]['observation_ref'],sha256='0'*64),'criteria':{'range':[299.9,310.1]}},'INTEGRITY_COMPROMISED'),
                        ({'observation_ref':cases[-1]['observation_ref'],'criteria':{'values':[305]}},'OBSERVATION_OVERRIDE_REFUSED'),
                        ({'observation_ref':cases[-1]['observation_ref'],'solution':{'dataset':'wrong_dataset'},'criteria':{'range':[299.9,310.1]}},'DATASET_MISMATCH')]:
                        negative=await call('validate.solution',request,allow_error=True)
                        assert negative['data']['scope']==scope
                        assert negative['data']['physical_validation_status']=='UNVERIFIED'
                        summary['provenance_checks'].append(negative)
                    summary['old_case_negative']=await call('validate.solution',{'observation_ref':cases[0]['observation_ref'],'solution':{'dataset':'dset1'},'criteria':{'range':[299.9,310.1]}},allow_error=True)
                    assert summary['old_case_negative']['data']['scope']=='STALE_SOLUTION'
                    repeat=await call('study.sweep_manage',{'study':'std1','definition':definition,'max_cases':4})
                    summary['repeat']=repeat
                    assert repeat['data']['cache_hits']==4, 'Expected four exact cached cases'
                    assert repeat['data']['budget']['cases_evaluated']==0
                    # Fresh actual W17 sample for the currently stored terminal state.
                    checkpoint=await call('stage.checkpoint_create',{'stage_id':'source','timestamp_s':2,
                        'units':{'T':'K'},'arguments':{'sample':sample}})
                    target_sample=json.loads(json.dumps(sample));target_sample['spec']['solution']['dataset']='dset2'
                    transfer=await call('stage.state_transfer',{'checkpoint_id':checkpoint['data']['checkpoint_id'],
                        'target_stage_id':'std2','variable_mapping':{'T':'T'},
                        'arguments':{'target_sample':target_sample,'initial_tolerance':.001}})
                    summary['stage']=transfer
                    assert transfer['data']['status']=='COMPLETE'
                    summary['stage_validation']=await call('validate.solution',{'observation_ref':transfer['data']['observation_ref'],'criteria':{'range':[299.9,310.1]}})
                    assert summary['stage_validation']['data']['numerical_verification_status']=='PASS'
                    summary['stage_oracle_error_K']=oracle(cases[-1],transfer['data']['solution_indices']['time_values'],transfer['data']['target_sample'])
                    optimization=await call('optimization.bounded_run',{'objective_name':'objective',
                        'parameter_bounds':{'k':[380,400],'rhoCp':[3400000,3800000]},
                        'constraints':[{'expression':'T_mid','min_value':300,'max_value':310}], 'max_cases':9,
                        'arguments':{'study':'std1','definition':definition,'grid_points_per_dim':3}})
                    summary['optimization']=optimization
                    best=optimization['data']['best_candidate']
                    assert best and best['is_feasible'] and optimization['data']['compute_budget']['cases_evaluated']<=9
                    for c in optimization['data']['all_candidates']:
                        if c['raw_results']['status'] in ('COMPLETED','CACHED'): oracle(c['raw_results'])
                    summary['reopen_samples']=[(await call('result.at_points',sample))['data'],(await call('result.at_points',target_sample))['data']]
                    summary['save']=await call('save_model',{'path':str(project/'w21_final.mph')})
                    summary['saved_sha256']=hashlib.sha256((project/'w21_final.mph').read_bytes()).hexdigest()
                    budget_definition=json.loads(json.dumps(definition))
                    budget_definition['input_artifacts']=['cache-input.txt']
                    summary['budget_input_changes']=[]
                    for payload in ['input-A','input-B']:
                        (project/'cache-input.txt').write_text(payload)
                        bounded=await call('study.sweep_manage',{'study':'std1','definition':budget_definition,'max_cases':1},allow_error=True)
                        summary['budget_input_changes'].append(bounded)
                        b=bounded['data']
                        assert b['budget']['cases_evaluated']==1 and b['cache_hits']==0
                        assert b['cases'][0]['status']=='COMPLETED' and b['cases'][1]['status']=='NOT_RUN'
                        assert 'solve' not in b['cases'][1]
                    failed_definition=json.loads(json.dumps(definition))
                    failed_definition['sample']['spec']['expressions']=['W21_missing_variable']
                    failure=await call('optimization.bounded_run',{'objective_name':'objective',
                        'parameter_bounds':{'k':[380,400],'rhoCp':[3400000,3800000]},
                        'constraints':[{'expression':'T_mid','min_value':300,'max_value':310}],'max_cases':1,
                        'study':'std1','definition':failed_definition})
                    summary['failed_candidate']=failure
                    assert failure['data']['best_candidate'] is None
                    assert failure['data']['all_candidates'][0]['raw_results']['status']=='FAILED'
                    summary['status']='SMOKE_COMPLETED'
    except Exception as exc:
        summary.update(status='FAILED',error=str(exc),traceback=traceback.format_exc())
        print(summary['traceback'],flush=True)
    finally:
        from comsol_mcp._platform_process import process_identity, terminate_process_tree
        endpoint_path=root/'control'/'control-private'/'control.json'
        if endpoint_path.exists():
            endpoint=json.loads(endpoint_path.read_text())
            identity=process_identity(endpoint['pid'])
            if identity['alive'] and identity['start_epoch_ms']==endpoint.get('process_start_epoch_ms'):
                summary['owned_daemon_stopped']=terminate_process_tree(endpoint['pid'])
        server.stop()
        (root/'summary.json').write_text(json.dumps(summary,indent=2,default=str,allow_nan=False))
    return 0 if summary['status']=='SMOKE_COMPLETED' else 1

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--work',required=True);p.add_argument('--version',required=True)
    p.add_argument('--comsol',required=True);p.add_argument('--jdk',required=True);p.add_argument('--fixture',required=True);p.add_argument('--reopen');p.add_argument('--refinement',action='store_true');p.add_argument('--budget-only',action='store_true')
    raise SystemExit(asyncio.run(run(p.parse_args())))
