"""Transport identities with the real production SessionLedger; no JVM."""
from copy import deepcopy
from types import SimpleNamespace
from comsol_mcp._execution_contract import SessionLedger,model_ref_from_mapping

def configure_backend(backend,*,project_id,session_id,server_id,epoch=2,instance='synthetic-w24-worker',endpoint='127.0.0.1:56388'):
    backend.worker_identity={'server_instance_id':server_id,'worker_instance_id':instance,'connection_epoch':epoch,'endpoint':endpoint}
    runtime={'instance_id':instance,'generation':epoch,'server':endpoint,'connected':True}
    backend.worker=SimpleNamespace(runtime_metadata=lambda:deepcopy(runtime))
    backend.service=SimpleNamespace(ledger=SessionLedger(session_id,server_id))
    if not callable(getattr(backend,"model_project_binding",None)):
        backend.model_project_binding=lambda ref:{"attribution":"PROJECT_BOUND","project_id":project_id}
    return backend

def bind_ledger(backend,ref):
    ledger=backend.service.ledger
    if ref['model_tag'] not in ledger._models:
        actual=ledger.bind_model(ref['model_tag']).as_dict()
        assert actual==model_ref_from_mapping(dict(ref)).as_dict(),'fixture must use the actual SessionLedger incarnation'
