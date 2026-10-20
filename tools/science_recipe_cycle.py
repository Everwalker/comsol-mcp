"""Fixed host-side zero-solve scientific recipe mode; uses the public P0 owner.

Preparation is source/data hashing only. No recipe IDs/test witnesses are made
up. Native run_cycle consumes actual durable public code.execute_java replies.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import sys

SCHEMA = "SCIENCE_PUBLIC_RECIPE_ZERO_SOLVE_PLAN_V1"
PARAMETERS = "comsol_mcp/data/science_recipes/parameters.json"
RESOURCE_ROOT = "comsol_mcp/data/science_recipes"
BASE_OPERATIONS = ["runtime.discover", "runtime.inspect", "project.create", "session.start",
                   "session.connect", "session.health", "model.create"]
CLEANUP_OPERATIONS = ["session.disconnect", "session.stop"]
BUDGET = {"birth_including_cleanup_seconds":2400,"cleanup_reserve_seconds":240,
          "servers":1,"workers":1,"task_models_max":8,"study_submissions":0,"solver_calls":0,
          "single_output_bytes_max":64*1024*1024,"total_project_bytes_max":256*1024*1024,
          "rpc_timeout_seconds":135,"queue_timeout_seconds":15,"execution_timeout_seconds":120,
          "transport_read_timeout_seconds":150}


def digest(path):
    path=Path(path)
    if any(parent.is_symlink() for parent in path.parents):raise ValueError("input parent symlink forbidden")
    a=path.lstat()
    if path.is_symlink() or not path.is_file():raise ValueError("source/input must be a regular no-follow file")
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        b=os.fstat(fd);signature=lambda s:(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
        if signature(a)!=signature(b):raise ValueError("input changed before descriptor open")
        sha=hashlib.sha256()
        while data:=os.read(fd,1048576):sha.update(data)
        if signature(b)!=signature(os.fstat(fd)) or signature(b)!=signature(path.lstat()):raise ValueError("input changed during pinned read")
    finally:os.close(fd)
    return sha.hexdigest()


def strict_json(path):
    def pairs(rows):
        result={}
        for k,v in rows:
            if k in result:raise ValueError("duplicate input key: "+k)
            result[k]=v
        return result
    return json.loads(Path(path).read_text(),object_pairs_hook=pairs,
                      parse_constant=lambda _:(_ for _ in ()).throw(ValueError("nonfinite JSON")))


def runtime_identity(installation,java_home):
    installation=Path(installation).resolve(strict=True);java_home=Path(java_home).resolve(strict=True)
    readme=installation/'readme.txt'
    raw=readme.read_bytes()
    match=re.search(rb'COMSOL (6\.4\.0\.293) README',raw)
    if match is None:raise ValueError('actual installation readme must identify 6.4.0.293')
    jars={str(p):digest(p) for p in sorted((installation/'plugins').glob('*.jar'))}
    if not jars:raise ValueError('actual SDK classpath missing')
    return {"python_executable":sys.executable,"python_resolved":str(Path(sys.executable).resolve()),
            "python_prefix":sys.prefix,"python_version":sys.version.split()[0],
            "comsol_root":str(Path(installation).resolve(strict=True)),
            "java_home":str(Path(java_home).resolve(strict=True)),"engine_expected":"6.4.0.293",
            "target_python_release_acceptance":"NOT_CLAIMED",
            "python_binary_sha256":digest(Path(sys.executable).resolve()),
            "installation_readme":str(readme),"installation_readme_sha256":digest(readme),
            "sdk_jars":jars,"jdk_files":{str(java_home/p):digest(java_home/p) for p in ["release","bin/java","bin/javac","bin/javap"]}}


def plan(source:Path,runtime:Path,installation:Path,java_home:Path):
    source=source.resolve(strict=True);runtime=runtime.absolute()
    if runtime.parent.resolve()!=Path('/private/tmp') or runtime.exists() or runtime.is_symlink():raise ValueError("fresh direct private namespace required")
    parameters=strict_json(source/PARAMETERS)
    if set(parameters)!={'schema','recipes','not_science_acceptance'} or parameters['schema']!='SCIENCE_RECIPE_LITERAL_PARAMETERS_V1':raise ValueError("closed literal parameter schema")
    cases=parameters['recipes']
    if [r['id'] for r in cases]!=['W22','W23','W24_SHAPE','W24_CURE'] or len(cases)*2!=BUDGET['task_models_max']:raise ValueError("exact four scientific builders/eight models required")
    resource_paths=sorted(p.relative_to(source).as_posix() for p in (source/RESOURCE_ROOT).rglob('*') if p.is_file())
    origins=strict_json(source/RESOURCE_ROOT/'SOURCE_ORIGINS.json')
    if origins.get('schema')!='SCIENCE_RECIPE_RESOURCE_ORIGINS_V1' or set(origins) != {'schema','files'} or len({r['resource'] for r in origins['files']}) != len(origins['files']):raise ValueError('closed unique resource origins required')
    for row in origins['files']:
        if digest(source/row['origin'])!=row['sha256'] or digest(source/row['resource'])!=row['sha256']:raise ValueError("original/resource scientific bytes differ")
    for row in cases:
        if set(row)!={'id','source','entrypoint','dimension','witness','rebuild'}:raise ValueError("closed recipe case schema")
    files={p:digest(source/p) for p in resource_paths}
    closure=["tools/science_recipe_cycle.py","tools/run_v3_public_mcp_smoke.py","tools/p0_owned_control_entry.py","tools/p0_original_controller_hold.py","comsol_mcp/_g3_managed_models.py","comsol_mcp/_g2_code.py","comsol_mcp/data/g2/02_ACTION_CATALOG.json","pyproject.toml"]
    closure += [r['origin'] for r in origins['files']]
    closure += [p.relative_to(source).as_posix() for p in (source/"comsol_mcp").rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    files.update({p:digest(source/p) for p in closure})
    stage=[p for p in resource_paths if Path(p).name not in ['parameters.json','SOURCE_ORIGINS.json']]
    operations=list(BASE_OPERATIONS)+['artifact.register']*len(stage)
    for index,row in enumerate(cases):
        if index:operations.append('model.create')
        operations+=['model.inspect','code.execute_java','code.execute_java','model.inspect','code.recipe_register',
                     'model.create','model.inspect','code.recipe_run','code.execute_java','model.inspect','model.save','model.list']
    operations+=CLEANUP_OPERATIONS
    return {'schema':SCHEMA,'runtime_namespace':str(runtime),'runtime_identity':runtime_identity(installation,java_home),
            'files':files,'staged_resources':stage,'cases':cases,'ordered_operations':operations,
            'literal_cycle_calls':literal_cycle_calls(stage,cases),
            'literal_base_calls':literal_base_calls(str(installation.resolve()),runtime.name),
            'literal_cleanup_calls':[{'operation':'session.disconnect','arguments':{'project_id':'$PROJECT_ID','session_id':'$SESSION_ID','retire_worker':True,'idempotency_key':'new'}},{'operation':'session.stop','arguments':{'project_id':'$PROJECT_ID','session_id':'$SESSION_ID','authorization_ref':'MASTER_GOAL_V3_FRESH_TASK_RUNTIME','idempotency_key':'new'}}],
            'dynamic_bindings':['actual runtime_id/project_id/session_id/workspace','new original ModelRefs/revisions','actual registered content IDs','actual durable builder operation_id','actual accepted recipe_id'],
            'budget':{**BUDGET,'public_calls':len(operations)},'scope':'UNSOLVED_REAL_BUILDS_ONLY_NO_NUMERICAL_PHYSICAL_EXPERIMENTAL_PASS',
            'permission':'RoothostCLI exactsource/runtime/input/argv/externalonce approval required; no callerJSON grant'}


def literal_base_calls(installation,label):
    common={'project_id':'$PROJECT_ID','session_id':'$SESSION_ID'}
    return [
      {'operation':'runtime.discover','arguments':{'roots':[installation]}},
      {'operation':'runtime.inspect','arguments':{'runtime_id':'$RUNTIME_ID'}},
      {'operation':'project.create','arguments':{'label':label,'workspace':'native-smoke','policy':{'permissions':['inspect','project_write','compute','host_control','trusted_code']},'idempotency_key':'new'}},
      {'operation':'session.start','arguments':{'project_id':'$PROJECT_ID','runtime_id':'$RUNTIME_ID','idempotency_key':'new'}},
      {'operation':'session.connect','arguments':{**common,'runtime_id':'$RUNTIME_ID','endpoint':'$ENDPOINT','idempotency_key':'new'}},
      {'operation':'session.health','arguments':common},
      {'operation':'model.create','arguments':{**common,'label':'science-W22-witness','idempotency_key':'new'}}]


def check_literal_body(slot,body,bindings):
    def resolve(value):
        if isinstance(value,str) and value in bindings:return bindings[value]
        if isinstance(value,dict):return {k:resolve(v) for k,v in value.items()}
        if isinstance(value,list):return [resolve(v) for v in value]
        return value
    if body!=resolve(slot['arguments']):raise RuntimeError('literal base/cleanup body differs')


def literal_cycle_calls(stage,cases):
    slots=[]
    def add(op,body):slots.append({'operation':op,'arguments':body})
    for rel in stage:
        path='science/'+Path(rel).relative_to(RESOURCE_ROOT).as_posix()
        add('artifact.register',{'path':path,'role':'java-source' if path.endswith('.java') else 'input_data','classification':'project_private','idempotency_key':'new'})
    for index,case in enumerate(cases):
        if index:add('model.create',{'label':'science-'+case['id']+'-witness','idempotency_key':'new'})
        add('model.inspect',{'detail':'summary'})
        add('code.execute_java',{'source_artifact':'$SOURCE_PATH','entrypoint':case['entrypoint'],'arguments':case['witness'],'mode':'trusted','timeout_s':120,'expected_revision':'$REVISION','idempotency_key':'new'})
        getter={'source_artifact':'science/java/ScientificBuildReadback.java','entrypoint':'ScientificBuildReadback','arguments':{'domain':case['id'],'expected_model_tag':'$MODEL_TAG'},'mode':'trusted','timeout_s':120,'expected_revision':'$REVISION','idempotency_key':'new'}
        add('code.execute_java',getter);add('model.inspect',{'detail':'summary'})
        add('code.recipe_register',{'source_artifact':'$SOURCE_ID','entrypoint':case['entrypoint'],'input_schema':'$EXACT_INPUT_SCHEMA','test_refs':['$WITNESS_OPERATION_ID'],'expected_revision':'$REVISION','idempotency_key':'new'})
        add('model.create',{'label':'science-'+case['id']+'-rebuilt','idempotency_key':'new'});add('model.inspect',{'detail':'summary'})
        add('code.recipe_run',{'recipe_id':'$RECIPE_ID','arguments':case['rebuild'],'expected_revision':'$REVISION','idempotency_key':'new'})
        add('code.execute_java',getter);add('model.inspect',{'detail':'summary'})
        add('model.save',{'destination':'science-'+case['id']+'-rebuilt.mph','include_solution':False,'overwrite':False,'expected_revision':'$REVISION','idempotency_key':'new'})
        add('model.list',{})
    return slots


def check_closure(source,plan_value):
    if any(digest(Path(source)/rel)!=sha for rel,sha in plan_value['files'].items()):
        raise RuntimeError('source/resource closure changed; no next effect allowed')


def execution_timeouts(remaining):
    if remaining <= BUDGET['transport_read_timeout_seconds']:
        raise TimeoutError('science requires the complete bounded call window plus cleanup reserve')
    return {'rpc_timeout_s':BUDGET['rpc_timeout_seconds'],'queue_timeout_s':BUDGET['queue_timeout_seconds'],
            'execution_timeout_s':BUDGET['execution_timeout_seconds']}


def check_call_order(plan_value,index,operation,*,cleanup,cleanup_history,connected):
    if cleanup:
        expected=CLEANUP_OPERATIONS if connected else ['session.stop']
        if len(cleanup_history)>=len(expected) or operation!=expected[len(cleanup_history)]:
            raise RuntimeError('exact cleanup suffix/order differs')
    elif cleanup_history or index>=len(plan_value['ordered_operations'])-2 or operation!=plan_value['ordered_operations'][index]:
        raise RuntimeError('science literal operation order/cap differs')


def safe_text(exc):
    try:return str(exc)
    except BaseException:return '<unprintable '+type(exc).__name__+'>'


def validate_plan(path,expected_sha256,*,source,runtime,installation,java_home):
    p=Path(path);s=p.lstat()
    if p.is_symlink() or s.st_uid!=os.getuid() or s.st_mode&0o077 or s.st_nlink!=1:raise ValueError("plan must be single-link owner-private regular file")
    if digest(p)!=expected_sha256:raise ValueError("science plan byte SHA mismatch")
    value=strict_json(p)
    if value!=plan(Path(source),Path(runtime),Path(installation),Path(java_home)):raise ValueError("science plan/literal/source/runtime/input drift")
    return value


def expand(value,workspace,ref):
    if isinstance(value,str):return value.replace('$WORKSPACE',str(workspace)).replace('$MODEL_TAG',ref['model_tag'])
    if isinstance(value,dict):return {k:expand(v,workspace,ref) for k,v in value.items()}
    if isinstance(value,list):return [expand(v,workspace,ref) for v in value]
    return value


def input_schema(case,workspace):
    props={}
    for key,value in case['witness'].items():
        if key=='expected_model_tag':props[key]={'type':'string','minLength':1,'maxLength':256}
        elif key=='case_id':props[key]={'enum':['flat','step']}
        else:props[key]={'const':expand(value,workspace,{'model_tag':''})}
    return {'type':'object','required':list(props),'properties':props,'additionalProperties':False}


def payload(value):
    if value.get('success') is not True:raise RuntimeError('public source execution was not successful')
    data=value.get('data',{})
    recipe_run = data.get('status') == 'EXECUTED'
    if recipe_run:
        if data.get('execution_state_unknown') is not False:raise RuntimeError('recipe execution is unknown')
        data=data.get('worker_result',{})
        if data.get('worker',{}).get('ok') is not True:raise RuntimeError('actual recipe Worker execution missing')
    elif data.get('execution_success') is not True:raise RuntimeError('actual source execution witness missing')
    raw=data.get('readback',{}).get('readback')
    if not isinstance(raw,dict):raise RuntimeError('actual domain Java readback unavailable')
    return raw


def verify_builder(value,case,ref):
    raw=payload(value)
    if value.get('execution',{}).get('model_ref')!=ref:raise RuntimeError('source execution ModelRef drift')
    if case['id']=='W23':
        if raw.get('status')!='BUILT_CONFIGURED_NOT_SOLVED' or raw.get('study_or_solver_invoked') is not False or raw.get('geometry',{}).get('dimension')!=2 or len(raw.get('ports',[]))!=2:raise RuntimeError('real TE port builder/readback missing')
    elif raw.get('status')!='BUILT_NOT_SOLVED':raise RuntimeError('actual scientific builder did not build')
    if case['id']=='W22' and not all(raw.get(k) for k in ['top','bottom','roi','incident_expression']):raise RuntimeError('actual W22 thermal/source geometry missing')
    if case['id']=='W24_SHAPE' and (raw.get('model_tag')!=ref['model_tag'] or raw.get('geometry_domain_count')!=2 or raw.get('geometry_axisymmetric') is not True or raw.get('study_run_calls')!=0):raise RuntimeError('in-place actual phase-field builder missing')
    if case['id']=='W24_CURE' and (raw.get('geometry_domain_count')!=4 or raw.get('geometry_axisymmetric') is not True or raw.get('native_study_run_calls')!=0 or not raw.get('domain_readbacks') or not raw.get('solver_readbacks')):raise RuntimeError('actual cure law/solver readback missing')
    return raw


def verify_inspect(value,ref,*,empty):
    data=value.get('data',{})
    if value.get('success') is not True or data.get('model_identity')!=ref:raise RuntimeError('public model inspect identity differs')
    structure=data.get('structure')
    if not isinstance(structure,dict):raise RuntimeError('actual model structure unavailable')
    required=['components','parameters','studies','solutions','datasets','results']
    if any(not isinstance(structure.get(k),list) for k in required):raise RuntimeError('incomplete actual model structure')
    if empty and any(structure[k] for k in required):raise RuntimeError('science recipe target is not empty')
    if not empty and (not structure['components'] or not structure['studies']):raise RuntimeError('actual domain physics/study structure not built')


async def run_cycle(call,*,source,plan_value,workspace,project_id,session_id,model_ref,revision,summary,evidence,verify_artifact,runtime_id,attached):
    identity={'runtime_id':runtime_id,'worker_instance_id':attached['worker_instance_id'],'connection_epoch':attached['worker_epoch'],'server_instance_id':attached['server_instance_id'],'endpoint':attached['endpoint']['host']+':'+str(attached['endpoint']['port'])}
    known=[model_ref];registered={};cases=plan_value['cases'];staged={}
    slots=plan_value['literal_cycle_calls'];slot_index=0;bindings={}
    async def action(op,body,*,ref=None,rev=None):
        nonlocal slot_index
        check_closure(source,plan_value)
        if any(digest(p)!=sha for p,sha in staged.items()):raise RuntimeError('registered literal data changed before next effect')
        def resolve(value):
            if isinstance(value,str) and value in bindings:return bindings[value]
            if value == '$REVISION':return rev
            if isinstance(value,str):return expand(value,workspace,ref or model_ref)
            if isinstance(value,dict):return {k:resolve(v) for k,v in value.items()}
            if isinstance(value,list):return [resolve(v) for v in value]
            return value
        slot=slots[slot_index] if slot_index<len(slots) else None
        if slot is None or op!=slot['operation'] or body!=resolve(slot['arguments']):raise RuntimeError('exact literal recipe call/body differs')
        slot_index+=1
        wire_body=body if op=='model.inspect' else {'project_id':project_id,'session_id':session_id,**body}
        value=await call(op,wire_body,ref=ref,revision=rev)
        files=[p for p in workspace.rglob('*') if p.is_file()]
        if any(p.is_symlink() or p.stat().st_size>BUDGET['single_output_bytes_max'] for p in files) or sum(p.stat().st_size for p in files)>BUDGET['total_project_bytes_max']:raise RuntimeError('science project byte cap exceeded')
        return value
    # Host materialization is the exact prebirth-bound whitelisted resource set.
    for rel in plan_value['staged_resources']:
        raw=(source/rel).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=plan_value['files'][rel]:raise RuntimeError('literal input resource changed')
        destination=workspace/'science'/Path(rel).relative_to(RESOURCE_ROOT)
        destination.parent.mkdir(parents=True,mode=0o700,exist_ok=True)
        with destination.open('xb') as out:out.write(raw)
        os.chmod(destination,0o400)
        staged[destination]=plan_value['files'][rel]
        role='java-source' if destination.suffix=='.java' else 'input_data'
        value=await action('artifact.register',{'path':destination.relative_to(workspace).as_posix(),'role':role,'classification':'project_private','idempotency_key':'new'})
        record=value['data'];verify_artifact(workspace,record)
        if record['sha256']!=plan_value['files'][rel]:raise RuntimeError('registered science input SHA differs')
        registered[destination.name]=record
    async def create(case,kind):
        value=await action('model.create',{'label':'science-'+case['id']+'-'+kind,'idempotency_key':'new'})
        ref=value['data']['model_ref']
        if ref in known or len(known)>=8:raise RuntimeError('new public ModelRef/model cap violation')
        known.append(ref);return ref,value['execution']['revision']
    async def readback(case,ref,rev):
        getter=registered['ScientificBuildReadback.java']
        value=await action('code.execute_java',{'source_artifact':getter['path'],'entrypoint':'ScientificBuildReadback','arguments':{'domain':case['id'],'expected_model_tag':ref['model_tag']},'mode':'trusted','timeout_s':120,'expected_revision':rev,'idempotency_key':'new'},ref=ref,rev=rev)
        raw=payload(value)
        if raw.get('schema')!='COMSOL_SCIENTIFIC_UNSOLVED_BUILD_READBACK_V1' or raw.get('model_tag')!=ref['model_tag'] or raw.get('domain')!=case['id'] or not raw.get('parameters') or not raw.get('material_tags') or not raw.get('physics_tags') or raw.get('study_or_solver_invoked') is not False:raise RuntimeError('domain-specific native unsolved metadata missing')
        return raw,value['execution']['revision']
    results=[]
    for index,case in enumerate(cases):
        if index:model_ref,revision=await create(case,'witness')
        verify_inspect(await action('model.inspect',{'detail':'summary'},ref=model_ref),model_ref,empty=True)
        src=registered[case['source']];bindings.update({'$SOURCE_PATH':src['path'],'$SOURCE_ID':src['artifact_id'],'$EXACT_INPUT_SCHEMA':input_schema(case,workspace)});summary['science_effect_started']=True
        witness=await action('code.execute_java',{'source_artifact':src['path'],'entrypoint':case['entrypoint'],'arguments':expand(case['witness'],workspace,model_ref),'mode':'trusted','timeout_s':120,'expected_revision':revision,'idempotency_key':'new'},ref=model_ref,rev=revision)
        build=verify_builder(witness,case,model_ref);revision=witness['execution']['revision']
        observed=witness.get('execution',{}).get('runtime_observation',{})
        witness_id=witness['execution'].get('operation_id')
        if not witness_id or witness['data'].get('source_sha256')!=src['sha256'] or observed.get('status')!='OBSERVED' or observed.get('comsol_version')!='6.4.0.293' or observed.get('runtime_identity')!=identity:raise RuntimeError('actual durable source/runtime execution witness missing')
        bindings['$WITNESS_OPERATION_ID']=witness_id
        witness_read,revision=await readback(case,model_ref,revision)
        verify_inspect(await action('model.inspect',{'detail':'summary'},ref=model_ref),model_ref,empty=False)
        recipe=await action('code.recipe_register',{'source_artifact':src['artifact_id'],'entrypoint':case['entrypoint'],'input_schema':input_schema(case,workspace),'test_refs':[witness_id],'expected_revision':revision,'idempotency_key':'new'},ref=model_ref,rev=revision)
        accepted=recipe['data'].get('test_refs',[])
        if len(accepted)!=1 or accepted[0].get('test_ref')!=witness_id or accepted[0].get('source_sha256')!=src['sha256'] or recipe['data'].get('source_artifact',{}).get('entrypoint')!=case['entrypoint']:raise RuntimeError('strict consumer did not accept exact real builder witness')
        bindings['$RECIPE_ID']=recipe['data']['recipe_id']
        target,rev=await create(case,'rebuilt')
        verify_inspect(await action('model.inspect',{'detail':'summary'},ref=target),target,empty=True)
        rebuilt=await action('code.recipe_run',{'recipe_id':recipe['data']['recipe_id'],'arguments':expand(case['rebuild'],workspace,target),'expected_revision':rev,'idempotency_key':'new'},ref=target,rev=rev)
        rebuilt_data=verify_builder(rebuilt,case,target)
        if rebuilt['data'].get('source_sha256')!=src['sha256'] or rebuilt['data'].get('entrypoint')!=case['entrypoint'] or rebuilt['data'].get('runtime')!='6.4.0.293':raise RuntimeError('accepted recipe run source/entrypoint/engine drift')
        rev=rebuilt['execution']['revision']
        rebuilt_read,rev=await readback(case,target,rev)
        verify_inspect(await action('model.inspect',{'detail':'summary'},ref=target),target,empty=False)
        saved=await action('model.save',{'destination':'science-'+case['id']+'-rebuilt.mph','include_solution':False,'overwrite':False,'expected_revision':rev,'idempotency_key':'new'},ref=target,rev=rev)
        save=saved['data'];path=workspace/save['destination']
        if path.is_symlink() or digest(path)!=save['sha256'] or path.stat().st_size!=save['size_bytes']:raise RuntimeError('saved actual science MPH hash/size drift')
        listing=await action('model.list',{})
        rows=listing['data'].get('models',[])
        if len(rows)!=len(known) or {r['model_ref']['model_tag'] for r in rows if isinstance(r,dict) and isinstance(r.get('model_ref'),dict)}!={r['model_tag'] for r in known}:raise RuntimeError('unexpected/unbound model creation; no adoption allowed')
        results.append({'case':case['id'],'witness_operation_id':witness_id,'recipe':recipe['data'],'witness_build':build,'witness_readback':witness_read,'rebuilt_ref':target,'rebuilt_build':rebuilt_data,'rebuilt_readback':rebuilt_read,'saved':save})
        model_ref,revision=target,saved['execution']['revision']
    if slot_index!=len(slots):raise RuntimeError('literal recipe plan incomplete')
    check_closure(source,plan_value)
    if any(digest(p)!=sha for p,sha in staged.items()):raise RuntimeError('registered literal input final drift')
    summary['science_recipes']={'status':'PUBLIC_REAL_BUILDER_RECIPE_REBUILD_READ_SAVE_ONLY','cases':results,'models':8,'study_submissions':0,'solver_calls':0,'numerical_physical_experimental_acceptance':'NOT_RUN'}
    return model_ref,revision


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--source',type=Path,required=True);parser.add_argument('--runtime',type=Path,required=True);parser.add_argument('--installation',type=Path,required=True);parser.add_argument('--java-home',type=Path,required=True);parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();value=plan(args.source,args.runtime,args.installation,args.java_home)
    with args.out.open('x') as handle:os.chmod(args.out,0o600);json.dump(value,handle,indent=2,sort_keys=True,allow_nan=False)
    print(json.dumps({'status':'PREPARE_ONLY_REFERENCE_NOT_APPROVED','plan':str(args.out),'sha256':digest(args.out),'calls':value['budget']['public_calls']}))

if __name__=='__main__':main()
