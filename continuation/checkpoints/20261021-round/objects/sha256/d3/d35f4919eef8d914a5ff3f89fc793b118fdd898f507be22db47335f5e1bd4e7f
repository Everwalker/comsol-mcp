"""Fixed original T050 cost/restore cycle, using the existing public client call."""
from pathlib import Path
import time,math

GEOMETRY={'segments':[{'collection':'component','tag':'comp1'},{'collection':'geom','tag':'geom1'}]}
PARAMETERS=4096
RECTANGLES=128
BATCHES=4
BATCH_SIZE=128
MAX_WORKSPACE_BYTES=256*1024*1024
ACTION_SECONDS=600
PUBLIC_CALLS=33

def sample_disk(workspace):
    total=0;mph={}
    for path in workspace.rglob('*'):
        if path.is_symlink():raise RuntimeError('T050 workspace symlink refused')
        if path.is_file():
            total+=path.stat().st_size
            if path.suffix.lower()=='.mph':mph[str(path)]=path.stat().st_size
    if total>MAX_WORKSPACE_BYTES:raise RuntimeError('T050 sampled workspace byte cap exceeded')
    return {'sampled_bytes':total,'mph_files':mph}

def parameters_changed():
    return [{'name':f't050_p{i:04d}','expression':f'{i+11}[mm]','description':f'T050 grouped update {i}'} for i in range(BATCHES*BATCH_SIZE)]

def verify_parameters(data,expected=None):
    rows=data.get('parameters',[]);expected=parameters_changed() if expected is None else expected
    if len(rows)!=len(expected):raise RuntimeError('T050 parameter readback count differs')
    for row,wanted in zip(rows,expected):
        value=row.get('evaluated',{})
        if (row.get('name')!=wanted['name'] or row.get('expression')!=wanted['expression'] or row.get('description')!=wanted['description']
            or row.get('unit')!='m' or row.get('unit_error') is not None or row.get('evaluate_errors') is not None or value.get('kind')!='float64' or value.get('shape')!=[]
            or not isinstance(value.get('data'),(float,int)) or isinstance(value.get('data'),bool)
            or not math.isclose(value['data'],int(wanted['expression'].split('[')[0])/1000,rel_tol=1e-12,abs_tol=1e-12)):
            raise RuntimeError('T050 exact grouped parameter value/unit readback differs')
    return rows

def verify_geometry(data,extra=False):
    tags=[v.get('tag') for v in data.get('features',[])]
    expected={f't050_r{i:03d}' for i in range(RECTANGLES)}
    if not expected.issubset(tags) or (('t050_extra' in tags) is not extra) or data.get('geometry_state',{}).get('dimension')!=2:
        raise RuntimeError('T050 geometry baseline/structural/restore readback differs')
    return {'baseline_rectangles':len(expected),'extra_present':extra,'features_observed':len(tags)}

async def run_cycle(call,*,workspace,project_id,session_id,model_ref,revision,fixture,summary,verify_artifact,digest,runtime_id,attached):
    common={'project_id':project_id,'session_id':session_id,'model_ref':model_ref}
    samples=[];start=time.monotonic()
    async def action(operation,arguments,ref=model_ref,rev=None):
        value=await call(operation,arguments,ref=ref,revision=rev)
        samples.append({'operation':operation,'elapsed_seconds':time.monotonic()-start,**sample_disk(workspace)})
        return value
    def model_body(body):return {**common,**body}
    fixture_path=workspace/'T050CostModelFixture.java';fixture_path.write_bytes(fixture)
    registered=await action('artifact.register',{'project_id':project_id,'path':fixture_path.name,'role':'java-source','classification':'project_private','idempotency_key':'new'},ref=None)
    verify_artifact(workspace,registered['data'])
    if registered['data']['sha256']!=digest(fixture_path):raise RuntimeError('T050 fixture source SHA registration differs')
    built=await action('code.execute_java',model_body({'source_artifact':registered['data']['path'],'entrypoint':'T050CostModelFixture','arguments':{},'mode':'trusted','timeout_s':60,'expected_revision':revision,'idempotency_key':'new'}),rev=revision)
    observation=built['execution'].get('runtime_observation',{})
    expected_identity={'runtime_id':runtime_id,'worker_instance_id':attached['worker_instance_id'],'connection_epoch':attached['worker_epoch'],'server_instance_id':attached['server_instance_id'],'endpoint':attached['endpoint']['host']+':'+str(attached['endpoint']['port'])}
    if (observation.get('status')!='OBSERVED' or observation.get('runtime_identity')!=expected_identity or observation.get('installation_runtime_id')!=runtime_id or built['execution'].get('model_ref')!=model_ref or built['data'].get('source_sha256')!=registered['data']['sha256']):raise RuntimeError('T050 fixedcode actual runtime/model/source producer identity differs')
    summary['t050_runtime_observation']=observation
    revision=built['execution']['revision'];witness=built['data']['readback']['readback']
    if witness!={'model_tag':model_ref['model_tag'],'parameters':PARAMETERS,'rectangles':RECTANGLES,'dimension':2,'semantics':'TASK_OWNED_UNSOLVED_NO_SAVE'}:raise RuntimeError('T050 fixed model build readback differs')
    summary['t050_model_build']=witness
    geometry=await action('geometry.inspect',model_body({'path':GEOMETRY}));verify_geometry(geometry['data'])
    baseline_parameters=await action('parameter.get',model_body({'names':['t050_p0000','t050_p4095'],'evaluate':True}))
    verify_parameters(baseline_parameters['data'],[{'name':f't050_p{i:04d}','expression':f'{i+1}[mm]','description':f'T050 baseline {i}'} for i in (0,4095)])
    baseline=await action('model.save',model_body({'destination':'t050-baseline.mph','overwrite':False,'include_solution':False,'expected_revision':revision,'idempotency_key':'new'}),rev=revision)
    revision=baseline['execution']['revision']
    baseline_path=workspace/baseline['data']['destination'];baseline_sha=digest(baseline_path)
    if baseline_sha!=baseline['data']['sha256']:raise RuntimeError('T050 baseline save SHA differs')
    before=await action('checkpoint.list',{'project_id':project_id,'filter':{}},ref=None)
    disk_before=sample_disk(workspace)['mph_files']
    rows=parameters_changed()
    batch_operations=[]
    for i in range(BATCHES):
        updated=await action('parameter.set',model_body({'parameters':rows[i*BATCH_SIZE:(i+1)*BATCH_SIZE],'expected_revision':revision,'idempotency_key':'new'}),rev=revision)
        revision=updated['execution']['revision'];batch_operations.append(updated['execution']['operation_id'])
    observed=await action('parameter.get',model_body({'names':[v['name'] for v in rows],'evaluate':True}));verify_parameters(observed['data'])
    after=await action('checkpoint.list',{'project_id':project_id,'filter':{}},ref=None)
    if before['data']!=after['data'] or disk_before!=sample_disk(workspace)['mph_files'] or digest(baseline_path)!=baseline_sha:
        raise RuntimeError('T050 grouped scalar updates changed checkpoint/full-save artifacts')
    checkpoint=await action('checkpoint.create',model_body({'label':'t050-before-structure','include_solution':False,'expected_revision':revision,'idempotency_key':'new'}),rev=revision)
    revision=checkpoint['execution']['revision'];cp=checkpoint['data']
    if cp.get('save_count')!=1 or not isinstance(cp.get('save_elapsed_ms'),(float,int)) or cp['save_elapsed_ms']<0 or digest(Path(cp['path']))!=cp['sha256']:
        raise RuntimeError('T050 structural-before checkpoint cost/hash unavailable')
    created=await action('geometry.feature_create',model_body({'parent':GEOMETRY,'tag':'t050_extra','type_id':'Rectangle','properties':[{'name':'size','value':{'kind':'float64','shape':[2],'data':[0.002,0.001]}}],'expected_revision':revision,'idempotency_key':'new'}),rev=revision)
    revision=created['execution']['revision'];rebuilt=await action('geometry.build',model_body({'geometry':GEOMETRY,'idempotency_key':'new'}),rev=revision);revision=rebuilt['execution']['revision']
    changed=await action('geometry.inspect',model_body({'path':GEOMETRY}));verify_geometry(changed['data'],extra=True)
    restored=await action('checkpoint.restore',model_body({'checkpoint_id':cp['checkpoint_id'],'expected_revision':revision,'authorization_ref':'T050 explicit taskowned checkpoint restore','idempotency_key':'new'}),rev=revision)
    ref=restored['execution']['model_ref'];revision=restored['execution']['revision']
    if ref==model_ref or restored['data'].get('old_model_ref')!=model_ref or not restored['data'].get('generation_advanced'):raise RuntimeError('T050 restore did not retire old ModelRef and bind new one')
    common={'project_id':project_id,'session_id':session_id,'model_ref':ref}
    geo=await action('geometry.inspect',model_body({'path':GEOMETRY}),ref=ref);verify_geometry(geo['data'])
    params=await action('parameter.get',model_body({'names':[v['name'] for v in rows],'evaluate':True}),ref=ref);verify_parameters(params['data'])
    await action('checkpoint.list',{'project_id':project_id,'filter':{}},ref=None)
    summary['t050_cost_cycle']={'status':'PASS_GROUPED_PARAMETERS_PRESTRUCTURE_CHECKPOINT_RESTORE','bounded_parameters':PARAMETERS,'baseline_rectangles':RECTANGLES,'updated_parameters':len(rows),'batch_operation_ids':batch_operations,'grouped_parameter_MPH_artifacts_unchanged':True,'baseline_save':baseline['data'],'structural_checkpoint':cp,'restore':restored['data'],'model_restore_scope':restored['data']['restore_scope'],'disk_samples':samples,'sampled_peak_workspace_bytes':max(v['sampled_bytes'] for v in samples),'peak_kind':'per-public-call samples, not continuous or OSquota','explicit_full_save_plan':3,'actual_native_save_trace':'audit original WorkerRequest submitted/observed metadata.method after terminal; baseline/final saveModelPackage and checkpoint.save, no eachsetter full-save','unsupported_acceptance':['solution','GUI','external dependencies','multiGBperformance','systemrestore']}
    return ref,revision


def audit_native_save_events(events,batch_operation_ids):
    """Consume original production worker_request receipts; never mint evidence."""
    submitted={};observed={}
    for event in events:
        if not isinstance(event,dict) or event.get('kind')!='call':continue
        method=event.get('metadata',{}).get('method')
        if method not in {'save','saveModelPackage'}:continue
        key=event.get('request_id');sha=event.get('request_hash')
        if not isinstance(key,str) or not key or not isinstance(sha,str) or len(sha)!=64:
            raise RuntimeError('T050 save request receipt identity unavailable')
        if event.get('operation_id') in batch_operation_ids:
            raise RuntimeError('T050 scalar batch issued a full-model save')
        target=submitted if event.get('phase')=='submitted' else observed if event.get('phase')=='observed' else None
        if target is None or key in target:raise RuntimeError('T050 ambiguous save receipt phase/duplicate')
        target[key]=event
    if set(submitted)!=set(observed) or len(submitted)!=3:
        raise RuntimeError('T050 exact three full-model saves not terminally observed')
    methods=[]
    for key,event in submitted.items():
        done=observed[key]
        if (done.get('request_hash')!=event['request_hash'] or done.get('metadata')!=event.get('metadata')
                or done.get('operation_id')!=event.get('operation_id') or done.get('status')!='SUCCEEDED'
                or done.get('reply',{}).get('ok') is not True):
            raise RuntimeError('T050 full-save receipt immutable terminal pair differs')
        methods.append(event['metadata']['method'])
    if methods.count('save')!=1 or methods.count('saveModelPackage')!=2:
        raise RuntimeError('T050 full-save methods differ from checkpoint/baseline/final plan')
    return {'observed_full_model_save_count':len(methods),'grouped_parameter_full_save_count':0,'methods':methods,
            'source':'original production worker_request submitted+observed sameUUID/hash/metadata/operation pair'}


def audit_terminal_database(home,evidence,batch_operation_ids):
    import shutil,json,sqlite3,hashlib
    root=evidence/'t050-raw-sqlite';root.mkdir(mode=0o700)
    readback=evidence/'t050-sqlite-readback';readback.mkdir(mode=0o700)
    hashes={}
    for path in (home/'control-private').glob('operations.sqlite3*'):
        before=hashlib.sha256(path.read_bytes()).hexdigest();copy=root/path.name;shutil.copyfile(path,copy);copy.chmod(0o400)
        if hashlib.sha256(copy.read_bytes()).hexdigest()!=before or hashlib.sha256(path.read_bytes()).hexdigest()!=before:
            raise RuntimeError('T050 terminal original SQLite snapshot changed')
        hashes[path.name]=before;shutil.copyfile(copy,readback/path.name)
    db=sqlite3.connect(readback/'operations.sqlite3')
    try:events=[json.loads(row[0]) for row in db.execute("SELECT metadata FROM job_events WHERE event='worker_request' ORDER BY id")]
    finally:db.close()
    return {**audit_native_save_events(events,batch_operation_ids),'original_SQLite_WAL_SHM_SHA':hashes}
