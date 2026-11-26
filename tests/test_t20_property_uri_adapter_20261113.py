"""Candidate-only adapter check: real property_set, synthetic ports.

No production receiver, model, Worker, project, disk or network is used.
Model lookup, schema and node calls are substituted explicitly. The actual
validation, managed value screen and property_set ordering remain in use.
"""
from __future__ import annotations

import pytest  # pyright: ignore[reportMissingImports]

from comsol_mcp import _g2_engine
from comsol_mcp._execution_contract import PreWriteRefusal
from tests.test_t20_property_uri_screen_20261113 import _screen, _typed


def _setup(monkeypatch):
    node = {}
    calls = []
    checks = []
    monkeypatch.setattr(_g2_engine, '_model', lambda worker, tag: object())
    monkeypatch.setattr(_g2_engine, 'resolve_node_path', lambda model, path: node)
    monkeypatch.setattr(_g2_engine, '_schemas_for', lambda node, props, **kwargs: {
        item['name']: {'metadata_status': 'KNOWN', 'kind': 'string', 'shape_rank': 0,
                       'getter': 'getString', 'java_signature': 'java.lang.String'}
        for item in props
    })

    def fake_call(target, method, name=None, *args):
        calls.append((method, name))
        if method == 'getType':
            # The resolved fake node's structural type for the T19.a
            # known-file gate; read-only, never a mutation.
            return 'Interpolation'
        if method == 'set':
            node[name] = args[0]['data']
            return None
        if method == 'getString':
            return node[name]
        raise AssertionError('unexpected fake node method')

    monkeypatch.setattr(_g2_engine, '_call', fake_call)
    real_screen = _screen(monkeypatch)

    def observed_screen(value, *, where):
        checks.append(where)
        return real_screen(value, where=where)

    return node, calls, checks, observed_screen


def test_uri_is_refused_before_any_setter_even_after_a_valid_value(monkeypatch):
    node, calls, checks, screen = _setup(monkeypatch)
    props = [
        {'name': 'funcname', 'value': _typed('f1')},
        {'name': 'filename', 'value': _typed('dbfile:///synthetic/file.dat?version=synthetic')},
    ]
    with pytest.raises(PreWriteRefusal) as failure:
        _g2_engine.property_set(object(), 'synthetic-model', {'segments': []}, props, path_guard=screen)
    assert failure.value.code == 'ACCESS_VIOLATION'
    # Current contract: the adapter first reads the resolved node's real type
    # via getType() and the T19.a known-file gate refuses an Interpolation
    # filename whose shape is not an absolute path (pinned by
    # tests/test_t19a_import_filename_semantics_20261123.py), so the refusal
    # happens before the path-shape screen runs; the URI screen refusal itself
    # stays pinned in tests/test_t20_property_uri_screen_20261113.py. The
    # adapter ordering invariant is unchanged: no set() dispatch ever happened
    # even though a legal value led the list.
    assert failure.value.details['mitigation'] == 't19a-known-file-semantics'
    assert checks == []
    assert all(method != 'set' for method, _ in calls) and node == {}


def test_plain_value_reaches_fake_setter_and_is_read_back(monkeypatch):
    node, calls, checks, screen = _setup(monkeypatch)
    props = [{'name': 'funcname', 'value': _typed('f1')}]
    result = _g2_engine.property_set(object(), 'synthetic-model', {'segments': []}, props, path_guard=screen)
    assert result['success'] is True and node['funcname'] == 'f1'
    assert len(checks) == 1
    assert calls == [('set', 'funcname'), ('getString', 'funcname')]
