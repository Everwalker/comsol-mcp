"""Backend-owned observation provenance, using the existing durable stores.

The context is installed by ManagedBackend, never taken from request JSON.
Only successful W17 engine output can mint a reference.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import uuid

from ._artifact_store import ArtifactStore, trusted_project_root
from ._execution_contract import PreWriteRefusal

_context = ContextVar('observation_context', default=None)

@contextmanager
def observation_context(store, model_ref, revision, producer):
    token = _context.set(dict(store=store, model_ref=model_ref, revision=revision, producer=producer))
    try:
        yield
    finally:
        _context.reset(token)

def current_context():
    ctx = _context.get()
    if ctx is None:
        raise PreWriteRefusal('PROVENANCE_CONTEXT_REQUIRED', 'Managed execution context is required')
    return ctx

def solution_identity(worker, model_tag, solution):
    from ._g3_common import bound_model
    from ._g2_engine import _call
    model = bound_model(worker, model_tag)
    sol = _call(model, 'sol', solution)
    study = _call(sol, 'study')
    node = _call(model, 'study', study)
    return {'solution': solution, 'study': study,
            'computation_date': _call(node, 'getLastComputationDate'),
            'computation_version': _call(node, 'getLastComputationVersion'),
            'times': _call(sol, 'getPVals')}


def verify_producer(ctx, producer):
    operation = ctx['store'].get_operation(producer)
    allowed = {'SUCCEEDED'}
    if producer == ctx['producer']:
        allowed.add('RUNNING')
    if not operation or operation.get('status') not in allowed:
        raise PreWriteRefusal('INVALID_PRODUCER', 'Observation producer is missing or not successfully executed')


def register_observation(worker, model_tag, data):
    ctx = current_context()
    status = data.get('status', {})
    if not isinstance(status, dict) or status.get('ok') is not True:
        raise PreWriteRefusal('OBSERVATION_FAILED', 'Only successful engine samples may be registered')
    if not data.get('solution') or not data.get('dataset') or not data.get('field_array'):
        raise PreWriteRefusal('OBSERVATION_INCOMPLETE', 'Stored solution and FieldArray are required')
    verify_producer(ctx, ctx['producer'])
    source_identity = solution_identity(worker, model_tag, data['solution'])
    identity = 'obs_' + uuid.uuid4().hex
    artifact = ArtifactStore(trusted_project_root(worker)).export_field_data(
        'observations/' + identity + '.json', data)
    record = dict(kind='w17_observation', observation_id=identity, model_tag=model_tag,
                  model_ref=ctx['model_ref'], revision=ctx['revision'], producer=ctx['producer'],
                  dataset=data['dataset'], solution=data['solution'], source_identity=source_identity, artifact=artifact)
    record['sha256'] = artifact['sha256']
    ctx['store'].persist_artifact(identity, record)
    return dict(observation_id=identity, sha256=record['sha256'])

def resolve_observation(worker, model_tag, ref, *, allow_historical=False):
    ctx = current_context()
    if not isinstance(ref, dict) or set(ref) != {'observation_id', 'sha256'}:
        raise PreWriteRefusal('UNREGISTERED_OBSERVATION', 'Reference must contain only a registered observation_id and sha256')
    record = ctx['store'].get_metadata('artifacts', ref['observation_id'])
    if not record or record.get('kind') != 'w17_observation':
        raise PreWriteRefusal('UNREGISTERED_OBSERVATION', 'Observation was not registered by W17')
    verify_producer(ctx, record['producer'])
    if record['model_tag'] != model_tag or record['model_ref'] != ctx['model_ref']:
        raise PreWriteRefusal('CROSS_MODEL_REFUSED', 'Observation belongs to another model or worker epoch')
    if not allow_historical and record['revision'] != ctx['revision']:
        raise PreWriteRefusal('STALE_OBSERVATION', 'Model revision has changed since sampling')
    if not allow_historical and solution_identity(worker, model_tag, record['solution']) != record.get('source_identity'):
        raise PreWriteRefusal('STALE_SOLUTION', 'Stored solution has been replaced since sampling')
    if record['sha256'] != ref['sha256']:
        raise PreWriteRefusal('INTEGRITY_COMPROMISED', 'Observation hash differs from registered hash')
    artifact = record['artifact']
    path = ArtifactStore(trusted_project_root(worker)).resolve_safe_path(artifact['file_path'], allow_overwrite=True)
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != record['sha256']:
        raise PreWriteRefusal('INTEGRITY_COMPROMISED', 'Stored observation artifact changed')
    payload = json.loads(raw)
    return record, dict(payload['metadata'], values=payload['values'])

def numeric_values(value):
    if isinstance(value, list):
        return [v for item in value for v in numeric_values(item)]
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return [value]
    raise PreWriteRefusal('INVALID_OBSERVATION', 'Expected real numeric field values')
