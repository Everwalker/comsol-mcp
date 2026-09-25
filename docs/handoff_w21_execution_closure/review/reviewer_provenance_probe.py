"""Independent control reproductions; not native evidence. No production edits."""
import json, tempfile
from pathlib import Path
from types import SimpleNamespace
from comsol_mcp._operation_store import OperationStore
from comsol_mcp._observation_store import observation_context,register_observation
from comsol_mcp._g3_w20_validation import validate_solution

def sample(value):
 return {'status':{'ok':True},'solution':'sol1','dataset':'dset1','values':[[[[value]]]],'field_array':{'axes':['expression','outer','inner','point']},'expressions':['T']}

with tempfile.TemporaryDirectory() as tmp:
 worker=SimpleNamespace(project_root=Path(tmp).resolve()); store=OperationStore(Path(tmp).resolve()/'ops.sqlite')
 model={'model_tag':'m1','generation':1,'session_id':'s','server_instance_id':'w'}
 with observation_context(store,model,7,'reviewer-no-such-producer'):
  ref=register_observation(worker,'m1',sample(303))
  assert store.get_operation('reviewer-no-such-producer') is None
  missing=validate_solution(worker,'m1',{'observation_ref':ref,'criteria':{'range':[300,310]}})
  assert missing['model_validated'] and missing['numerical_verification_status']=='PASS'
 # Exact revision reassignment performed by ManagedBackend after a multicase operation.
 record=store.get_metadata('artifacts',ref['observation_id'])
 record['sample_revision']=record['revision'];record['revision']=8
 store.persist_artifact(ref['observation_id'],record)
 with observation_context(store,model,8,'later-validation'):
  rebound=validate_solution(worker,'m1',{'solution':{'dataset':'dset1'},'observation_ref':ref,'criteria':{'range':[300,310]}})
  assert rebound['model_validated'] and rebound['numerical_verification_status']=='PASS'
 result={'scope':'CONTROL_ONLY_NOT_NATIVE','missing_producer':{'producer_exists':False,'verdict':missing},'old_sample_rebound_final_revision':{'old_sample_revision':7,'final_revision':8,'verdict':rebound}}
 print(json.dumps(result,indent=2,allow_nan=False))
 store.close()
