"""Fresh fake-store evidence for same-worker project-attribution continuity.

Two actual ManagedBackend methods are compiled from their current source into a
minimal class; the rest of that module and its side effects are not imported.
Inputs are newly synthesized; no old fixture, model, Worker, SSH, database,
UNKNOWN, non-identity/ref hash, bool-generation or rebind outcome is exercised.
"""
from __future__ import annotations

import ast
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest  # pyright: ignore[reportMissingImports]

from comsol_mcp._runtime_state import restore_runtime_state, save_runtime_state


ROOT = Path('/Volumes/SSD/Comsol-MCP/workspaces/universal-completion-20261107')
BACKEND = ROOT / 'comsol_mcp/_managed_backend.py'
SESSION = 'synthetic-project-binding-session'
SERVER = 'synthetic-project-binding-server'
MODEL = 'synthetic-project-binding-model'
PROJECT = 'synthetic-project-binding-project'


def backend_slice():
    module = ast.parse(BACKEND.read_text(encoding='utf-8'), filename=str(BACKEND))
    classes = [node for node in module.body if isinstance(node, ast.ClassDef) and node.name == 'ManagedBackend']
    assert len(classes) == 1
    wanted = {'_model_project_key', 'persist'}
    methods = [node for node in classes[0].body if isinstance(node, ast.FunctionDef) and node.name in wanted]
    assert {node.name for node in methods} == wanted and len(methods) == len(wanted)
    statements: list[ast.stmt] = [*methods]
    cls = ast.ClassDef(name='ExtractedBackend', bases=[], keywords=[], body=statements,
                       decorator_list=[], type_params=[])
    code = compile(ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])), str(BACKEND), 'exec')
    namespace = {'__name__': 'comsol_mcp._binding_test_slice', '__package__': 'comsol_mcp',
                 'json': json, 'save_runtime_state': save_runtime_state}
    exec(code, namespace)
    return namespace['ExtractedBackend']


class MemoryMetadata:
    def __init__(self, session_state, old_key):
        self.data = {
            ('sessions', SESSION): deepcopy(session_state),
            ('revisions', old_key): {'project_id': PROJECT, 'attribution': 'PROJECT_BOUND'},
        }

    def get_metadata(self, table, key):
        assert table in {'sessions', 'runtimes', 'revisions'}
        return self.data.get((table, key))

    def put_metadata(self, table, key, value):
        assert table in {'sessions', 'runtimes', 'revisions'}
        self.data[(table, key)] = deepcopy(value)


def make_state(schema):
    identity = {'worker_instance_id': 'synthetic-project-binding-worker',
                'connection_epoch': 5, 'server_instance_id': SERVER}
    ref = {'session_id': SESSION, 'server_instance_id': SERVER, 'model_tag': MODEL,
           'generation': 4, 'schema_version': schema}
    state = {'schema_version': 1, 'worker_identity': dict(identity), 'session_id': SESSION,
             'server_instance_id': SERVER, 'server_ownership': 'shared',
             'permissions': ['inspect'], 'generations': {MODEL: 4},
             'models': {MODEL: {'ref': ref, 'revision': 8, 'fingerprint': 'synthetic-fingerprint',
                                 'external_event_counter': 6, 'observed_external_event_counter': 6,
                                 'dirty': False, 'retired': False, 'active_operation_id': None,
                                 'ownership': 'user_owned'}}, 'active_tickets': []}
    return identity, state


@pytest.mark.parametrize('schema', [1, True], ids=['int1', 'legacy-true'])
def test_restore_then_persist_preserves_project_attribution(schema):
    identity, state = make_state(schema)
    source_ref = state['models'][MODEL]['ref']
    old_key = json.dumps(source_ref, sort_keys=True, separators=(',', ':'))
    store = MemoryMetadata(state, old_key)
    ledger, unknown = restore_runtime_state(store, SESSION, identity)
    assert unknown == []
    assert ledger._models[MODEL].ref.generation == 4
    assert ledger._models[MODEL].revision == 8
    assert store.data[('revisions', old_key)]['project_id'] == PROJECT

    backend = backend_slice()()
    backend.store = store
    backend.service = SimpleNamespace(ledger=ledger)
    backend.worker_identity = identity
    backend._model_project_bindings = {}
    new_key = backend._model_project_key(ledger._models[MODEL].ref.as_dict())
    if schema is True:
        assert new_key != old_key  # proves the re-key branch is reached
    else:
        assert new_key == old_key
    backend.persist()

    published = store.data[('revisions', new_key)]
    assert published.get('project_id') == PROJECT
    assert published.get('attribution') == 'PROJECT_BOUND'
