"""Internal native epoch evidence; public JSON is never an epoch capability."""
from copy import deepcopy
from collections.abc import Mapping
import hashlib
_TOKEN = object()

class _NativeEpochCapability:
    def __init__(self, token, *, daemon, project_id, model_ref, epoch, origin, original_receiver):
        if token is not _TOKEN:
            raise ValueError('native epoch capabilities require an actual managed origin')
        if type(original_receiver) is not _ReceiverAnchor or original_receiver._token is not _TOKEN:
            raise ValueError('initial mint requires the original non-JSON pre-Java receiver anchor')
        original_receiver.verify(daemon,origin)
        self._origin=deepcopy(origin)
        self._backend=_resolve_backend(daemon,origin)
        self._binding=deepcopy(origin['metadata']['runtime_binding'])
        self._worker=getattr(self._backend,'worker',None)
        self._identity=deepcopy(getattr(self._backend,'worker_identity',None))
        if self._worker is None or not isinstance(self._identity,dict) or not self._identity:
            raise ValueError('native epoch capability lacks the actual live session Worker')
        self._daemon=daemon;self._project_id=project_id;self._session_id=model_ref['session_id']
        if (self._identity.get('worker_instance_id')!=epoch['instance_id']
                or type(self._identity.get('connection_epoch')) is not int
                or self._identity['connection_epoch']!=epoch['generation']
                or self._identity.get('endpoint')!=epoch['server']
                or model_ref.get('server_instance_id')!=self._identity.get('server_instance_id')):
            raise ValueError('initial actual backend/Worker identity differs from native Java/ModelRef epoch')
        if self._binding['kind']=='default_backend':
            expected_server=hashlib.sha256(f"{self._identity['endpoint']}:{epoch['instance_id']}:{epoch['generation']}".encode()).hexdigest()
        else:
            from comsol_mcp._session_context import CanonicalSocket
            peer=self._identity.get('observed_peer',{})
            expected_server=self._backend._session_server_instance_id(
                CanonicalSocket(peer['address'],peer['port']),
                {'instance_id':epoch['instance_id'],'generation':epoch['generation'],
                 'engine_version':self._identity['remote_engine_version']})
        if expected_server!=self._identity['server_instance_id']:
            raise ValueError('actual backend compatibility server identity differs from original implementation inputs')
        self._epoch=deepcopy(epoch)
        self._token=token

    def _check_live(self):
        backend=_resolve_backend(self._daemon,self._origin)
        if (backend is not self._backend or getattr(self._backend,'worker',None) is not self._worker
                or getattr(self._backend,'worker_identity',None)!=self._identity):
            raise ValueError('actual session Worker identity changed; no next route')

    def verify(self,response,*,model_ref,revision):
        from tools.run_native_w24_cure_preflight import _native_mesh_response_worker_epoch
        self._check_live()
        if model_ref.get('session_id')!=self._session_id:
            raise ValueError('native epoch response crossed the registered session')
        origin=_verify_origin(self._daemon,response,project_id=self._project_id)
        actual=_native_mesh_response_worker_epoch(response,project_id=self._project_id,
                                                 model_ref=model_ref,revision=revision)
        if _resolve_backend(self._daemon,origin) is not self._backend:
            raise ValueError('actual Java origin names a different backend receiver')
        runtime=origin['metadata'].get('runtime_binding',{})
        if runtime.get('kind')!=self._binding.get('kind'):
            raise ValueError('actual Java origin changed authoritative receiver kind')
        if (runtime.get('project_id')!=self._project_id or runtime.get('session_id')!=self._session_id
                or runtime.get('worker_instance_id')!=actual['instance_id']
                or type(runtime.get('worker_epoch')) is not int or runtime['worker_epoch']!=actual['generation']):
            raise ValueError('actual OperationStore runtime binding differs from Java Worker audit')
        if actual!=self._epoch:
            raise ValueError('actual Java pre/post Worker epoch differs from the fresh cure epoch')
        self._check_live()
        return deepcopy(actual)

    def backend_for_binding(self, binding):
        self._check_live()
        if (binding.project_id!=self._project_id or binding.session_id!=self._session_id
                or binding.model_ref.get('server_instance_id')!=self._identity.get('server_instance_id')):
            raise ValueError('model binding differs from the actual project/session/server epoch')
        return self._backend

    def as_evidence(self):
        self._check_live()
        return deepcopy(self._epoch)

def _verify_origin(daemon,response,*,project_id):
    if not isinstance(response,dict) or response.get('success') is not True:
        raise ValueError('native epoch requires a terminal successful managed response')
    execution=response.get('execution')
    if not isinstance(execution,dict) or execution.get('project_id')!=project_id:
        raise ValueError('native epoch managed project origin differs')
    op_id=execution.get('operation_id')
    if not isinstance(op_id,str) or not op_id:
        raise ValueError('native epoch response omitted its actual OperationStore origin')
    op=daemon.store.get_operation(op_id)
    if (not isinstance(op,dict) or op.get('status')!='SUCCEEDED' or op.get('metadata',{}).get('execution',{}).get('project_id')!=project_id
            or op.get('operation_id')!=op_id or op.get('request_id')!=execution.get('request_id')
            or op.get('result')!=response):
        raise ValueError('native epoch response differs from actual terminal OperationStore result')
    metadata=op.get('metadata',{})
    if (op.get('operation')!='operation_call' or metadata.get('arguments',{}).get('operation_id')!='code.execute_java'
            or metadata.get('execution',{}).get('model_ref')!=execution.get('model_ref')):
        raise ValueError('native epoch origin is not the exact managed Java ModelRef request')
    return op

def _mint_from_actual_cure_response(daemon,response,*,project_id,model_ref,revision,original_receiver):
    from tools.run_native_w24_cure_preflight import _native_mesh_response_worker_epoch
    origin=_verify_origin(daemon,response,project_id=project_id)
    java=origin['metadata']['arguments'].get('arguments',{})
    if (java.get('source_artifact')!='W24CureCouponFixture.java' or java.get('entrypoint')!='W24CureCouponFixture#run'
            or java.get('arguments')!={'phase':'build_v2','mu_scale':1.0}):
        raise ValueError('native epoch capability requires this fresh v2 cure build origin, not historical v1')
    epoch=_native_mesh_response_worker_epoch(response,project_id=project_id,
                                            model_ref=model_ref,revision=revision)
    capability=_NativeEpochCapability(_TOKEN,daemon=daemon,project_id=project_id,
                                      model_ref=model_ref,epoch=epoch,origin=origin,original_receiver=original_receiver)
    capability.verify(response,model_ref=model_ref,revision=revision)
    return capability

def require_native_epoch_capability(value):
    if type(value) is not _NativeEpochCapability or value._token is not _TOKEN:
        raise ValueError('coupled setup requires an internal actual cure epoch capability; JSON is refused')
    value._check_live()
    return value


def _resolve_backend(daemon,origin):
    """Use the Controller's durable receiver resolver; never register a session."""
    metadata=origin.get('metadata',{})
    binding=metadata.get('runtime_binding',{})
    kind=binding.get('kind')
    if kind not in {'registered_session','default_backend'}:
        raise ValueError('actual Store runtime binding kind is missing or unsupported')
    context=daemon._resolve_job_runtime_context(origin,metadata.get('execution',{}))
    if kind=='registered_session':
        if context is None:raise ValueError('registered origin has no authoritative runtime context')
        return context.backend
    if context is not None:raise ValueError('default origin resolved to a registered context')
    return daemon._default_backend


class _ReceiverAnchor:
    def __init__(self,token,daemon,execution):
        if token is not _TOKEN:raise ValueError('receiver anchor is internal, not JSON')
        context=daemon._execution_session_context(execution)
        self._runtime=deepcopy(daemon._runtime_binding_for_request(context,execution))
        self._backend=daemon._default_backend if context is None else context.backend
        self._worker=self._backend.worker
        self._identity=deepcopy(self._backend.worker_identity)
        if self._worker is None or not isinstance(self._identity,dict) or not isinstance(self._runtime,dict):
            raise ValueError('original pre-Java receiver is not established')
        self._token=token
    def verify(self,daemon,origin):
        backend=_resolve_backend(daemon,origin)
        if (backend is not self._backend or backend.worker is not self._worker
                or backend.worker_identity!=self._identity
                or origin['metadata'].get('runtime_binding')!=self._runtime):
            raise ValueError('original pre-Java backend/Worker/Store binding changed before mint')

def _capture_original_cure_receiver(daemon,*,project_id,model_ref):
    return _ReceiverAnchor(_TOKEN,daemon,{'project_id':project_id,'session_id':model_ref['session_id'],
                                        'model_ref':dict(model_ref)})
