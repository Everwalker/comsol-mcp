"""Local attach-only control selection: software fixtures, no live daemon."""
import builtins
import json
import stat
from types import SimpleNamespace

import pytest
from comsol_mcp import _control_client as client
from comsol_mcp import _server


class FakePath:
    def __init__(self, state, part):
        self.state, self.part = state, part

    def __truediv__(self, name):
        return FakePath(self.state, {'control-private': 'home', 'control.json': 'endpoint', 'control.log': 'log'}[name])

    def resolve(self):
        return self

    def is_symlink(self):
        return self.state.get(self.part + '_link', False)

    def lstat(self):
        if not self.state['home_exists']:
            raise FileNotFoundError('SECRET_PATH')
        kind = stat.S_IFLNK if self.state.get('home_link') else stat.S_IFDIR
        return SimpleNamespace(st_mode=kind | self.state['mode'], st_uid=self.state['uid'])

    def stat(self):
        return self.lstat()

    def exists(self):
        return self.state['endpoint_exists'] if self.part == 'endpoint' else self.state['home_exists']

    def mkdir(self, **kwargs):
        self.state['mutations'].append('mkdir')

    def read_text(self, **kwargs):
        return self.state['endpoint_json']

    def open(self, *args, **kwargs):
        self.state['mutations'].append('log-open')
        return FakeStream()


class FakeStream:
    def __enter__(self): return self
    def __exit__(self, *args): return False


@pytest.fixture
def attach(monkeypatch):
    state = {'env': {'COMSOL_SERVER_MCP_CONTROL_MODE': 'attach-only'}, 'uid': 501,
             'mode': 0o700, 'home_exists': True, 'endpoint_exists': True,
             'endpoint_json': json.dumps({'port': 1777, 'token': 'TEST_SECRET_TOKEN'}),
             'health': {'success': True}, 'rpc': {'success': True},
             'calls': [], 'mutations': [], 'spawn': 0, 'alive': 0, 'scm': []}
    class OS:
        name = 'posix'
        environ = state['env']
        @staticmethod
        def getuid(): return 501
        @staticmethod
        def chmod(*args): state['mutations'].append('chmod')
    monkeypatch.setattr(client, 'os', OS)
    monkeypatch.setattr(_server, 'COMSOL_SERVER_MCP_HOME', FakePath(state, 'root'))
    def spawn(*args): state['spawn'] += 1
    def alive(*args): state['alive'] += 1; return True
    def request(endpoint, payload, timeout):
        state['calls'].append(payload['operation'])
        if type(endpoint.get('port')) is not int or not 1 <= endpoint['port'] <= 65535 or not isinstance(endpoint.get('token'), str):
            raise ValueError('SENSITIVE_EXCEPTION SECRET_PATH TEST_SECRET_TOKEN')
        value = state['health'] if payload['operation'] == 'session_health' else state['rpc']
        if isinstance(value, BaseException): raise value
        return value
    def verify(locator):
        state['scm'].append(locator)
        if locator != 'valid': raise ValueError('SECRET_PATH TEST_SECRET_TOKEN')
        return {'port': 1777, 'token': 'TEST_SECRET_TOKEN'}
    monkeypatch.setattr(client, '_spawn_control_daemon', spawn)
    monkeypatch.setattr(client, '_alive', alive)
    monkeypatch.setattr(client, '_request', request)
    original_import = builtins.__import__
    def import_module(name, globals=None, locals=None, fromlist=(), level=0):
        if name == '_windows_scm_endpoint' and level == 1:
            return SimpleNamespace(verify_endpoint=verify)
        return original_import(name, globals, locals, fromlist, level)
    monkeypatch.setattr(builtins, '__import__', import_module)
    return state


def dispatch():
    return client.dispatch('registry_list', {'limit': 1}, {'request_id': 'r', 'idempotency_key': 'k'})


def no_effect(state):
    assert state['spawn'] == state['alive'] == 0
    assert state['mutations'] == []


def assert_refusal(state):
    result = dispatch()
    assert result['success'] is False
    assert result['error']['code'] == 'CONTROL_REQUEST_NOT_SUBMITTED'
    assert result['error']['safe_retry'] is False
    assert result['data']['status'] == 'NOT_SUBMITTED'
    assert result['data']['control_startup_outcome'] == 'NOT_ATTEMPTED'
    for secret in ('TEST_SECRET_TOKEN', 'SECRET_PATH', 'SENSITIVE_EXCEPTION'):
        assert secret not in json.dumps(result)
    no_effect(state)


def test_attach_success_health_and_real_dispatch_path(attach):
    assert client.ensure_control() == {'port': 1777, 'token': 'TEST_SECRET_TOKEN'}
    attach['calls'].clear()
    assert dispatch()['success'] is True
    assert attach['calls'] == ['session_health', 'registry_list']
    no_effect(attach)


@pytest.mark.parametrize('field,value', [
    ('home_exists', False), ('root_link', True), ('home_link', True),
    ('uid', 777), ('mode', 0o755), ('endpoint_exists', False), ('endpoint_link', True),
    ('endpoint_json', 'SECRET_PATH {'), ('endpoint_json', '[]'),
    ('endpoint_json', '{"port":1777}'), ('endpoint_json', '{"port":0,"token":"TEST_SECRET_TOKEN"}'),
    ('health', {'success': False, 'secret': 'TEST_SECRET_TOKEN'}),
    ('health', ['TEST_SECRET_TOKEN']), ('health', {'success': 1}),
    ('health', TimeoutError('SENSITIVE_EXCEPTION SECRET_PATH TEST_SECRET_TOKEN')),
])
def test_attach_refuses_unsafe_endpoint_and_health_without_effects(attach, field, value):
    attach[field] = value
    assert_refusal(attach)


@pytest.mark.parametrize('value', ['', 'AUTO', 'invalid'])
def test_invalid_mode_fails_closed(attach, value):
    attach['env']['COMSOL_SERVER_MCP_CONTROL_MODE'] = value
    assert_refusal(attach)
    assert attach['calls'] == []


def test_scm_takes_priority_even_blank_or_invalid_mode(attach):
    attach['env']['COMSOL_SERVER_MCP_SCM_ENDPOINT_CONFIG'] = ''
    assert_refusal(attach)
    assert attach['scm'] == [''] and attach['calls'] == []
    attach['env']['COMSOL_SERVER_MCP_SCM_ENDPOINT_CONFIG'] = 'valid'
    attach['env']['COMSOL_SERVER_MCP_CONTROL_MODE'] = 'invalid'
    assert client.ensure_control()['port'] == 1777
    assert attach['scm'] == ['', 'valid'] and attach['calls'] == ['session_health']
    no_effect(attach)


@pytest.mark.parametrize('mode', [None, 'auto'])
def test_auto_existing_preserves_creation_and_health(attach, mode):
    if mode is None: del attach['env']['COMSOL_SERVER_MCP_CONTROL_MODE']
    else: attach['env']['COMSOL_SERVER_MCP_CONTROL_MODE'] = mode
    assert dispatch()['success'] is True
    assert attach['calls'] == ['session_health', 'registry_list']
    assert attach['mutations'] == ['mkdir', 'chmod']
    assert attach['spawn'] == attach['alive'] == 0


def test_auto_missing_endpoint_preserves_spawn_path(attach, monkeypatch):
    del attach['env']['COMSOL_SERVER_MCP_CONTROL_MODE']
    reads = iter([None, {'port': 1777, 'token': 'TEST_SECRET_TOKEN'}])
    monkeypatch.setattr(client, '_read_endpoint', lambda home: next(reads))
    assert client.ensure_control()['port'] == 1777
    assert attach['spawn'] == 1 and attach['calls'] == ['session_health']
    assert attach['mutations'] == ['mkdir', 'chmod', 'log-open']


def test_postdispatch_unknown_never_replays_or_leaks(attach):
    attach['rpc'] = TimeoutError('SENSITIVE_EXCEPTION SECRET_PATH TEST_SECRET_TOKEN')
    result = dispatch()
    assert result['error']['code'] == 'EXECUTION_STATE_UNKNOWN'
    assert result['error']['safe_retry'] is False and result['data']['status'] == 'UNKNOWN'
    assert attach['calls'] == ['session_health', 'registry_list']
    assert all(secret not in json.dumps(result) for secret in ('SENSITIVE_EXCEPTION', 'SECRET_PATH', 'TEST_SECRET_TOKEN'))
    no_effect(attach)


def test_real_request_auth_with_fake_urlopen(attach, monkeypatch):
    # Restore the canonical function saved before the fixture patches transport.
    monkeypatch.setattr(client, '_request', ORIGINAL_REQUEST)
    calls = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self): return b'{"success":true}'
    def urlopen(request, timeout):
        calls.append((request.full_url, request.get_header('Authorization'), json.loads(request.data)['operation'], timeout))
        return Response()
    monkeypatch.setattr(client, 'urlopen', urlopen)
    assert dispatch()['success'] is True
    assert [call[2] for call in calls] == ['session_health', 'registry_list']
    assert all(call[0] == 'http://127.0.0.1:1777/rpc' and call[1] == 'Bearer TEST_SECRET_TOKEN' for call in calls)
    assert calls[0][3] == 1.0
    no_effect(attach)


ORIGINAL_REQUEST = client._request
