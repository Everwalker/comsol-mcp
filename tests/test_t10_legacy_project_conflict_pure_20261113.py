"""Synthetic conflicting-project regression for T10 restore and persistence.

Only real runtime-state functions and two AST-extracted backend methods run;
all records and project IDs are fresh in-memory fakes. No real model, Worker,
SSH, UNKNOWN, database, old fixture, fenced identity/hash/rebind outcome, or
T20 live-guard comparison is involved. The fake store cannot establish real
transaction isolation, principal authorization or historical reachability.
"""
from __future__ import annotations

import ast
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest  # pyright: ignore[reportMissingImports]

from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._runtime_state import RuntimeStateError, restore_runtime_state, save_runtime_state

ROOT = Path('/Volumes/SSD/Comsol-MCP/workspaces/universal-completion-20261107')
BACKEND = ROOT / 'comsol_mcp/_managed_backend.py'
SESSION = 'synthetic-conflict-session'
SERVER = 'synthetic-conflict-server'
MODEL = 'synthetic-conflict-model'
ORIGINAL = 'synthetic-project-original'
FOREIGN = 'synthetic-project-conflicting'


def backend_slice():
    module = ast.parse(BACKEND.read_text(encoding='utf-8'), filename=str(BACKEND))
    classes = [node for node in module.body if isinstance(node, ast.ClassDef) and node.name == 'ManagedBackend']
    assert len(classes) == 1
    wanted = {'_model_project_key', 'persist'}
    methods = [node for node in classes[0].body if isinstance(node, ast.FunctionDef) and node.name in wanted]
    assert len(methods) == len(wanted) and {node.name for node in methods} == wanted
    statements: list[ast.stmt] = [*methods]
    cls = ast.ClassDef(name='ExtractedBackend', bases=[], keywords=[], body=statements,
                       decorator_list=[], type_params=[])
    code = compile(ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])), str(BACKEND), 'exec')
    namespace = {'__name__': 'comsol_mcp._project_conflict_slice', '__package__': 'comsol_mcp',
                 'json': json, 'save_runtime_state': save_runtime_state}
    exec(code, namespace)
    return namespace['ExtractedBackend']


class MemoryMetadata:
    def __init__(self, session_state, revisions):
        self.data = {('sessions', SESSION): deepcopy(session_state)}
        self.data.update({('revisions', key): deepcopy(value) for key, value in revisions.items()})
        self.writes = []

    def get_metadata(self, table, key):
        assert table in {'sessions', 'runtimes', 'revisions'}
        return self.data.get((table, key))

    def put_metadata(self, table, key, value):
        assert table in {'sessions', 'runtimes', 'revisions'}
        self.writes.append((table, key))
        self.data[(table, key)] = deepcopy(value)


def make_state(schema):
    identity = {'worker_instance_id': 'synthetic-conflict-worker',
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


def key(ref):
    return json.dumps(ref, sort_keys=True, separators=(',', ':'))


def restore_and_persist(store, identity):
    ledger, unknown = restore_runtime_state(store, SESSION, identity)
    assert unknown == []
    assert ledger._models[MODEL].ref.generation == 4
    assert ledger._models[MODEL].revision == 8
    backend = backend_slice()()
    backend.store = store
    backend.service = SimpleNamespace(ledger=ledger)
    backend.worker_identity = identity
    backend._model_project_bindings = {}
    current_key = backend._model_project_key(ledger._models[MODEL].ref.as_dict())
    backend.persist()
    return current_key


def test_existing_integer_project_binding_stays_usable():
    identity, state = make_state(1)
    ref_key = key(state['models'][MODEL]['ref'])
    store = MemoryMetadata(state, {ref_key: {'project_id': ORIGINAL, 'attribution': 'PROJECT_BOUND'}})
    current_key = restore_and_persist(store, identity)
    assert current_key == ref_key
    assert store.data[('revisions', current_key)]['project_id'] == ORIGINAL
    assert store.data[('revisions', current_key)]['attribution'] == 'PROJECT_BOUND'
    assert store.writes


def test_legacy_conflicting_project_bindings_refuse_without_persisted_changes():
    identity, state = make_state(True)
    original_ref = state['models'][MODEL]['ref']
    old_key = key(original_ref)
    int_key = key({**original_ref, 'schema_version': 1})
    assert old_key != int_key
    assert ORIGINAL != FOREIGN
    store = MemoryMetadata(state, {
        old_key: {'project_id': ORIGINAL, 'attribution': 'PROJECT_BOUND'},
        int_key: {'project_id': FOREIGN, 'attribution': 'PROJECT_BOUND'},
    })
    before = {item: key(value) for item, value in store.data.items()}
    try:
        actual_key = restore_and_persist(store, identity)
    except (ExecutionContractError, RuntimeStateError):
        assert {item: key(value) for item, value in store.data.items()} == before
        return
    published = store.data[('revisions', actual_key)]
    pytest.fail('conflicting bindings were not refused; '
                f'published_project={published.get("project_id")!r}; '
                f'write_tables={[table for table, _ in store.writes]!r}')
