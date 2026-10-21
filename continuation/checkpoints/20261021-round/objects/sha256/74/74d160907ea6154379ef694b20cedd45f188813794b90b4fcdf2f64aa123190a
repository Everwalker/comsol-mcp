"""Real daemon/ExecutionService/store public tickets; synthetic SDK boundary only."""
import copy,json
from pathlib import Path
import pytest
from tests.test_g3_managed_models import _Worker,_runtime_root
from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._execution_contract import SessionLedger
from comsol_mcp._execution_service import ExecutionService
from comsol_mcp._runtime_installation import runtime_id_for_root

@pytest.fixture
def environment(tmp_path,monkeypatch):
    monkeypatch.setenv('COMSOL_MCP_TRUSTED_CODE','1');monkeypatch.setenv('COMSOL_MCP_HOST_CONTROL','1')
    projects=tmp_path/'projects';projects.mkdir();install=_runtime_root(tmp_path/'install');endpoint='127.0.0.1:25432'
    worker=_Worker(install,endpoint);service=ExecutionService(SessionLedger('software-session','software-server',permissions={'inspect','project_write','compute','trusted_code','host_control'}),worker,project_root=projects)
    daemon=ControlDaemon(tmp_path/'control',service=service,worker=worker,project_root=projects)
    daemon.backend.endpoint_key=endpoint;daemon.backend.worker_identity={'runtime_id':runtime_id_for_root(install),'worker_instance_id':'worker-1','connection_epoch':1,'server_instance_id':'software-server','endpoint':endpoint}
    # Synthetic SDK attachment only; the actual producer mints the observation, never caller data.
    daemon.backend._bind_runtime_observation_context(installation_root=install,runtime_id=runtime_id_for_root(install),worker_instance_id='worker-1',connection_epoch=1,server_instance_id='software-server',endpoint=endpoint,legacy=False)
    daemon.backend.persist();monkeypatch.setattr(daemon.backend,'_require_g2_isolation',lambda:{'software_boundary_not_native':True})
    p=daemon.dispatch({'operation':'project.create','arguments':{'label':'public-recipe','workspace':'public-recipe','policy':{'permissions':['inspect','project_write','compute','trusted_code','host_control']}},'execution':{'request_id':'project','idempotency_key':'project'}})
    assert p['success'],p;project=p['data']['project'];created=daemon.dispatch({'operation':'model.create','arguments':{'label':'recipe'},'execution':{'project_id':project['project_id'],'session_id':service.ledger.session_id,'request_id':'model','idempotency_key':'model'}})
    assert created['success'],created;ref=created['execution']['model_ref'];workspace=Path(project['workspace']);(workspace/'Recipe.java').write_text('public class Recipe { public static Object run(){ return 1; } }')
    def public(op,body,key,wrapper='operation_call'):
        body={'project_id':project['project_id'],'session_id':ref['session_id'],'model_ref':ref,**body}
        execution={'project_id':project['project_id'],'session_id':ref['session_id'],'model_ref':ref,'expected_revision':service.ledger._models[ref['model_tag']].revision,'request_id':key,'idempotency_key':key}
        return daemon.dispatch({'operation':op if wrapper=='direct' else wrapper,'arguments':({k:v for k,v in body.items() if k not in {'project_id','session_id','model_ref'}} if wrapper=='direct' else {'operation_id':op,'arguments':body}),'execution':execution})
    registered=public('artifact.register',{'path':'Recipe.java','role':'java-source','classification':'project_private'},'source')
    assert registered['success'],registered
    yield daemon,worker,project,ref,registered['data'],public
    daemon.close()


def execute_recipe(environment,wrapper='operation_call'):
    daemon,worker,project,ref,source,public=environment
    executed=public('code.execute_java',{'source_artifact':source['path'],'entrypoint':'Recipe','mode':'trusted','arguments':{}},'execute',wrapper)
    assert executed['success'],executed
    opid=executed['execution']['operation_id'];record=daemon.store.get_operation(opid)
    expected='code.execute_java' if wrapper=='direct' else wrapper
    assert record['operation']==expected and record['metadata']['operation']==expected
    if wrapper!='direct':assert record['metadata']['arguments']['operation_id']=='code.execute_java'
    assert executed['execution']['runtime_observation']['status']=='OBSERVED'
    return executed,record

@pytest.mark.parametrize('wrapper',['operation_call','registry_call','direct'])
def test_real_public_durable_wrapper_is_consumed_without_rewriting_store(environment,wrapper):
    daemon,worker,project,ref,source,public=environment;executed,record=execute_recipe(environment,wrapper)
    before=json.dumps(record,sort_keys=True);result=public('code.recipe_register',{'source_artifact':source['artifact_id'],'entrypoint':'Recipe','input_schema':{'type':'object'},'test_refs':[record['operation_id']]},'recipe',wrapper)
    assert result['success'],result
    assert result['data']['test_refs'][0]['scope']=='EXECUTION_SMOKE'
    assert json.dumps(daemon.store.get_operation(record['operation_id']),sort_keys=True)==before
    assert len(worker.executions)==1

@pytest.mark.parametrize('damage',['recordwrapper','metawrapper','Noneoperation','innerop','recursive','extraouter','project','executionproject','session','model','modelbool','missingexecutionproject','failed','failedjob','source','runtime','worker-event'])
def test_actual_public_ticket_tampering_remains_refused(environment,damage):
    daemon,worker,project,ref,source,public=environment;executed,row=execute_recipe(environment)
    changed=copy.deepcopy(row);meta=changed['metadata'];inner=meta['arguments']['arguments']
    if damage=='recordwrapper':changed['operation']='registry_call'
    elif damage=='metawrapper':meta['operation']='registry_call'
    elif damage=='Noneoperation':meta['operation']=None
    elif damage=='innerop':meta['arguments']['operation_id']='code_execute_java'
    elif damage=='recursive':meta['arguments']={'operation_id':'operation_call','arguments':meta['arguments']}
    elif damage=='extraouter':meta['arguments']['extra']='forbidden'
    elif damage=='project':inner['project_id']='other-project'
    elif damage=='executionproject':meta['execution']['project_id']='other-project'
    elif damage=='session':inner['session_id']='other-session'
    elif damage=='model':inner['model_ref']['model_tag']='foreign'
    elif damage=='modelbool':inner['model_ref']['generation']=True
    elif damage=='missingexecutionproject':meta['execution'].pop('project_id')
    elif damage=='failed':changed['status']='FAILED'
    elif damage=='failedjob':
        job=daemon.store.operation_job(row['operation_id'])
        with daemon.store.lock:daemon.store.db.execute('UPDATE jobs SET status=? WHERE job_id=?',('FAILED',job['job_id']))
    elif damage=='source':inner['source_artifact']='else.java'
    elif damage=='runtime':changed['result']['execution']['runtime_observation']['runtime_identity']['worker_instance_id']='other-worker'
    else:
        job=daemon.store.operation_job(row['operation_id']);events=daemon.store.events(job['job_id']);event=next(x for x in events if x['event']=='worker_request' and x['metadata'].get('kind')=='code_execute')
        value=copy.deepcopy(event['metadata']);value['metadata']['entrypoint']='Other';daemon.store.add_event(job['job_id'],'worker_request',value)
    # Mutate only this adversarial software fixture's actual stored ticket after production dispatch.
    with daemon.store.lock:
        con=daemon.store.db
        con.execute('UPDATE operations SET operation=?,status=?,metadata=?,result=? WHERE operation_id=?',(changed['operation'],changed['status'],json.dumps(meta),json.dumps(changed['result']),row['operation_id']))
    result=public('code.recipe_register',{'source_artifact':source['artifact_id'],'entrypoint':'Recipe','input_schema':{'type':'object'},'test_refs':[row['operation_id']]},'recipe-negative')
    assert result['success'] is False,result
    assert result['error']['code']=='RECIPE_TEST_UNVERIFIED',result
    assert len(worker.executions)==1
