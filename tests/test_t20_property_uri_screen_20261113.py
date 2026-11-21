"""T19/T20 hierarchical resource URI screen: synthetic values only.

COMSOL's documented dbfile URI can reference a Model Manager input or output;
other hierarchical schemes exercise the generic syntax, not a claim that COMSOL
accepts every scheme. The managed screen is real, but authority/host and the
subsequent setter are substitutes. No engine, database, socket, file, model,
Worker, SSH, historical job or UNKNOWN is accessed.
"""
from __future__ import annotations

from contextvars import ContextVar
from types import SimpleNamespace
from typing import cast

import pytest  # pyright: ignore[reportMissingImports]

from comsol_mcp import _artifact_store
from comsol_mcp._execution_contract import PreWriteRefusal
from comsol_mcp._g2_property_path_guard import SHAPE_WINDOWS_DRIVE, classify_string_leaf
from comsol_mcp._managed_backend import ManagedBackend


def _screen(monkeypatch):
    monkeypatch.setattr(_artifact_store, 'local_engine_host_identity', lambda endpoint: 'synthetic-host')
    receiver = SimpleNamespace(
        project_authorizer=lambda project, permission: {'workspace': '/synthetic/project'},
        _project_root_context=ContextVar('synthetic_property_uri_root', default=None),
        endpoint_key='127.0.0.1:2036',
    )
    return ManagedBackend._g2_property_path_screen(cast(ManagedBackend, receiver), 'synthetic-project')


def _typed(value):
    return {'kind': 'string', 'shape': [], 'data': value}


@pytest.mark.parametrize('uri', [
    pytest.param('dbfile:///synthetic/data.txt?version=synthetic', id='dbfile'),
    pytest.param('user:///synthetic/data.txt', id='user'),
    pytest.param('upload:///synthetic/data.txt', id='upload'),
    pytest.param('example+custom://synthetic/data', id='generic-scheme'),
])
def test_unbound_hierarchical_resource_uri_is_refused_before_setter(monkeypatch, uri):
    screen = _screen(monkeypatch)
    reached = []
    try:
        screen(_typed(uri), where='synthetic property')
    except PreWriteRefusal as exc:
        assert exc.code == 'ACCESS_VIOLATION'
    else:
        reached.append('synthetic setter')
    assert not reached, 'the screen passed an unbound URI to the following synthetic setter'


def test_plain_identifier_still_passes_synthetic_screen(monkeypatch):
    assert _screen(monkeypatch)(_typed('f1'), where='synthetic identifier') is None


def test_windows_drive_prefix_keeps_existing_classification():
    assert classify_string_leaf('C:/synthetic/file.txt') == SHAPE_WINDOWS_DRIVE
