"""R19 actual Controller/Store/SDK ownership consumers; synthetic SDK only."""
import json
from pathlib import Path
import pytest
from tests.test_windows_appcontainer import environment, dispatch
from tests.test_windows_host_preflight_owner import Function
from comsol_mcp._windows_owned_resources import registry_for
from comsol_mcp._windows_service_sid import original_preflights_terminal


def unknown_row(env):
    row = json.loads(env[5].db.execute('SELECT result FROM operations').fetchone()[0])
    assert row['execution_state'] == 'UNKNOWN' and row['safe_retry'] is False
    return row


@pytest.mark.parametrize('case', ['raise_no_output', 'bool_no_output', 'failure_no_output', 'null', 'io', 'bool_hresult', 'overflow_hresult', 'failed_output', 'invalid_pointer', 'invalid_sid', 'invalid_length'])
def test_original_unclassified_create_reconcile_and_metadata_tamper_refused(tmp_path, monkeypatch, case):
    env, sdk = environment(tmp_path, monkeypatch)
    api, store, exe, daemon = env[4], env[5], env[8], env[9]
    if case.endswith('no_output'):
        def create(*args):
            row = store.db.execute("SELECT metadata FROM job_events WHERE event='HostControlMutationJournalV2' ORDER BY id DESC LIMIT 1").fetchone()
            assert json.loads(row[0])['event'] == 'APPLY_STEP_INTENT'
            sdk.calls.append('create')
            if case == 'raise_no_output': raise sdk.primary
            return False if case == 'bool_no_output' else -2147024891
        sdk.userenv.CreateAppContainerProfile.callback = create
    else: sdk.fault = case
    try:
        result = dispatch(env); assert 'error' in result
        before = unknown_row(env)
        call = api._appcontainer_original_call
        if case.endswith('no_output') or case == 'null': assert not registry_for(api).unresolved()
        call[3].update(outcome='COMPLETED', allocation_confirmed=True, result=0, address=123)
        assert not original_preflights_terminal(api)
        with pytest.raises(Exception) as rejected:
            daemon._reconcile_original_host_control_executor(result['request'], exe)
        assert rejected.value.code == 'HOST_CONTROL_OWNER_UNKNOWN'
        assert len(daemon._retained_host_control_executors) == 1
        assert api._appcontainer_original_call is call
        assert list(daemon._retained_host_control_executors.values())[0]['executor'] is exe
        if case == 'raise_no_output':
            assert api._first_mutation_cause is sdk.primary
            assert list(daemon._retained_host_control_executors.values())[0]['first_cause'] is sdk.primary
        assert unknown_row(env) == before
        sdk.fault = None
        assert 'error' in dispatch(env) and sdk.calls.count('create') == 1
        assert not hasattr(sdk.userenv, 'DeleteAppContainerProfile')
        if case == 'raise_no_output':
            (Path(__file__).resolve().parents[2] / 'evidence/ACTUAL_UNCLASSIFIED_CREATE_RECONCILE_R19.json').write_text(json.dumps({
                'status': 'PASS_SOURCE_SOFTWARE_ORIGINAL_OWNER_RETAINED',
                'original_SDK_call_entered_after_INTENT': True, 'original_error': str(sdk.primary),
                'original_cause_retained_on_API_and_Controller': True,
                'diagnostic_state_tampered_cannot_authorize_release': dict(call[3]),
                'original_retainer_before': 1, 'original_retainer_after': len(daemon._retained_host_control_executors),
                'registry_unresolved': len(registry_for(api).unresolved()),
                'actual_reconcile_code': rejected.value.code, 'Store_result': before,
                'Create_count': sdk.calls.count('create'), 'FreeSid_count': 0, 'Delete_exported': False,
                'Windows_COMSOL_native': 0}, indent=2) + '\n')
    finally: env[6].close(); store.close()


def test_normal_return_consumer_retains_original_unknown_call(tmp_path, monkeypatch):
    env, sdk = environment(tmp_path, monkeypatch)
    api, exe, daemon = env[4], env[8], env[9]
    def create(*args):
        sdk.calls.append('create'); raise sdk.primary
    sdk.userenv.CreateAppContainerProfile.callback = create
    original = daemon._execute_registered_host_control
    def returned(request, record):
        try: return original(request, record)
        except OSError as error:
            assert error is sdk.primary
            return 'synthetic outer normal return after actual missing SDK return'
    monkeypatch.setattr(daemon, '_execute_registered_host_control', returned)
    try:
        result = dispatch(env)
        assert result['result'].startswith('synthetic outer normal return')
        assert len(daemon._retained_host_control_executors) == 1
        assert not registry_for(api).unresolved() and not original_preflights_terminal(api)
        unknown_row(env)
        with pytest.raises(Exception): daemon._reconcile_original_host_control_executor(result['request'], exe)
        assert sdk.calls.count('create') == 1
    finally: env[6].close(); env[5].close()


@pytest.mark.parametrize('fault', ['free_pointer', 'free_bool', 'free_io', 'sid_mismatch'])
def test_validated_original_sid_closer_retry_reconciles_only_memory(tmp_path, monkeypatch, fault):
    env, sdk = environment(tmp_path, monkeypatch); sdk.fault = fault
    api, exe, daemon = env[4], env[8], env[9]
    try:
        result = dispatch(env); assert 'error' in result
        before = unknown_row(env)
        r = registry_for(api)
        entries = [e for e in r.unresolved() if e.domain == 'appcontainer_sid']
        if fault == 'sid_mismatch':
            assert not entries and original_preflights_terminal(api)
        else:
            assert len(entries) == 1 and not original_preflights_terminal(api)
            with pytest.raises(Exception): daemon._reconcile_original_host_control_executor(result['request'], exe)
            sdk.fault = None
            api._advapi.FreeSid = Function(lambda *args: pytest.fail('foreign FreeSid'))
            api._captured_mutation_sdk['FreeSid'] = api._advapi.FreeSid
            r.retry_owned(entries[0], api)
            assert not r.unresolved() and original_preflights_terminal(api)
            assert len([c for c in sdk.calls if isinstance(c, tuple) and c[0] == 'FreeSid']) == 2
        daemon._reconcile_original_host_control_executor(result['request'], exe)
        assert not daemon._retained_host_control_executors and unknown_row(env) == before
        assert sdk.calls.count('create') == 1 and not hasattr(sdk.userenv, 'DeleteAppContainerProfile')
    finally: env[6].close(); env[5].close()


@pytest.mark.parametrize('entered', [False, True])
def test_original_capability_closer_retry_required_for_reconciliation(tmp_path, monkeypatch, entered):
    env, sdk = environment(tmp_path, monkeypatch)
    api, exe, daemon = env[4], env[8], env[9]
    original = api._kernel32.LocalFree.callback; blocked = [False]; closes = []; armed = [False]
    def free(ptr):
        if armed[0]: closes.append(ptr.value)
        if blocked[0]: return ptr.value
        return original(ptr)
    api._kernel32.LocalFree.callback = free
    convert = sdk.convert
    def capability_convert(*args):
        armed[0] = True; blocked[0] = True
        answer = convert(*args)
        return answer if entered else 0
    api._advapi.ConvertStringSidToSidW.callback = capability_convert
    try:
        result = dispatch(env); assert 'error' in result
        before = unknown_row(env); r = registry_for(api)
        entries = [e for e in r.unresolved() if e.domain == 'mutation_local']
        assert entries and not original_preflights_terminal(api)
        with pytest.raises(Exception): daemon._reconcile_original_host_control_executor(result['request'], exe)
        blocked[0] = False
        api._kernel32.LocalFree = Function(lambda *args: pytest.fail('foreign LocalFree'))
        api._captured_mutation_sdk['LocalFree'] = api._kernel32.LocalFree
        for entry in entries: r.retry_owned(entry, api)
        assert not r.unresolved() and original_preflights_terminal(api)
        daemon._reconcile_original_host_control_executor(result['request'], exe)
        assert not daemon._retained_host_control_executors and unknown_row(env) == before
        assert sdk.calls.count('create') == int(entered)
        assert len(closes) == 2 * len(entries)
    finally: env[6].close(); env[5].close()
