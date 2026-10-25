"""Real completed C and source-loader ownership over inert SDK exports only."""
from contextlib import ExitStack
import copy
import json

import pytest

from comsol_mcp import _windows_t037_qualification_run as qualification
from comsol_mcp import _windows_t037_targets as targets
from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._windows_owned_resources import registry_for
from tests._prepared_launch_software_fixture import enter_test
from tests.test_session_server_control import _start
from tests.test_windows_native_child_lifecycle import server_case
from tests.test_windows_t037_c_full_lifecycle import effects


@pytest.mark.parametrize('close_result', ['false', 'lost_return'])
def test_original_live_c_source_close_unknown_fences_every_reentry(
        monkeypatch, tmp_path, record_property, close_result):
    with ExitStack() as stack:
        enter_test(stack)
        _, api, _, _, launcher, _, _ = server_case(
            monkeypatch, tmp_path/'fixture', controller=True)
        env = api._prepared_fixture
        # This executes the actual original APPLY and complete coordinator;
        # no C/loader/enrollment/issuer/readiness function is substituted.
        daemon, project, originals = env.public_controller(launcher)
        try:
            driver = env._full_c_software_driver
            live = qualification.require_live_c(
                daemon, runtime_id='fixture-runtime', live=True)
            op = live.operation
            original_apply = daemon.store.get_operation(op.admission.operation_id)
            original_job = daemon.store.job(op.admission.job_id)
            assert original_apply['status'] == original_job['status'] == 'SUCCEEDED'
            epochs = tuple((c, c._resume_epoch, c._resume_attempt)
                           for s in live.run.stages for c in [s.child])
            calls, close_entries = [], []
            fault = OSError('inert original source CloseHandle return lost')
            original_close = env.sdk.k.CloseHandle
            prior_readers = set(targets._LOADER_APIS)

            def close(handle):
                calls.append(('CloseHandle', getattr(handle, 'value', handle)))
                if not close_entries:
                    # Only this captured SDK export is faulty. Find its actual
                    # original reader entry after normal pre-entry acquisition.
                    matching = [(reader, entry)
                                for reader in targets._LOADER_APIS.values()
                                if id(reader) not in prior_readers
                                for entry in registry_for(reader).unresolved()
                                if entry.value == getattr(handle, 'value', handle)]
                    # The existing ACL verifier has separate original readers.
                    # Inject only the fresh profile loader's own pinned entry.
                    if not matching:
                        return original_close(handle)
                    assert len(matching) == 1
                    close_entries.append(matching[0])
                    if close_result == 'lost_return':
                        original_close(handle)
                        raise fault
                    return 0
                return original_close(handle)

            monkeypatch.setattr(env.sdk.k, 'CloseHandle', close)
            # Instrument only concrete inert exports. Each loader and all
            # acquisition/close/SD parsers keep their original production body.
            for label, exports in [('kernel', env.sdk.k), ('advapi', env.sdk.a),
                                   ('ntdll', env.sdk.nt)]:
                for name, function in list(vars(exports).items()):
                    if not callable(function) or name == 'CloseHandle':
                        continue
                    def observed(*args, _fn=function, _name=(label, name)):
                        calls.append(_name)
                        return _fn(*args)
                    monkeypatch.setattr(exports, name, observed)

            def sdk_state():
                return (tuple(calls), tuple(env.sdk.calls), tuple(env.sdk.free_calls),
                        tuple(driver.sdk_calls),
                        tuple((key, effects(env, state)) for key, state in driver.states.items()))

            before = sdk_state()
            for value, runtime in [(copy.copy(live), 'fixture-runtime'),
                                   (live, 'foreign-runtime')]:
                with pytest.raises(ExecutionContractError):
                    value.check(runtime_id=runtime, live=True)
            assert sdk_state() == before and live.validation_error is None
            assert live.check(runtime_id='fixture-runtime', live=False) is live
            assert sdk_state() == before

            with pytest.raises(ExecutionContractError) as first:
                qualification.require_live_c(daemon, runtime_id='fixture-runtime', live=True)
            assert len(close_entries) == 1, (first.value.code, str(first.value), repr(first.value.__cause__))
            reader, entry = close_entries[0]
            retained = registry_for(reader)
            assert targets._LOADER_APIS[id(reader)] is reader
            assert retained.owner is reader and entry in retained.unresolved()
            assert entry.state == 'UNKNOWN' and first.value.__cause__ is entry.error
            if close_result == 'lost_return':
                assert entry.error is fault
            original_entries = tuple((e, e.state, e.error, e.closer, e.handle, e.keepalive)
                                     for e in retained.unresolved())
            before = sdk_state()
            readers_before = tuple(targets._LOADER_APIS.items())
            policy_before = tuple(live.current_policy_reads)
            replay_errors = []
            for is_live in (False, True):
                try:
                    qualification.require_live_c(
                        daemon, runtime_id='fixture-runtime', live=is_live)
                except BaseException as error:
                    replay_errors.append(error)
                else:
                    replay_errors.append(None)
            # Exercise the public normal PROJECT route, not only the helper.
            response = _start(daemon, project, key='source-unknown-'+close_result)
            after = sdk_state()
            diagnostic = dict(
                scope='SOURCE_SOFTWARE_ONLY_INERT_EXPORTS_NO_NATIVE',
                injected_close=close_result, close_injected=len(close_entries),
                first_code=first.value.code, first_cause_same_entry=True,
                retained_entry_state=entry.state, original_apply=original_apply['status'],
                extra_source_exports=len(after[0])-len(before[0]),
                all_observed_sdk_streams_unchanged=after == before,
                same_error_on_pure_and_live=[e is first.value for e in replay_errors],
                public_project_success=response['success'],
                public_project_error=response.get('error'),
                C_first_error_same=live.validation_error is first.value,
                new_project_factories=len(originals))
            record_property('original_source_revalidation_oracle', json.dumps(diagnostic, sort_keys=True))
            (tmp_path/'original-source-revalidation-oracle.json').write_text(
                json.dumps(diagnostic, indent=1)+'\n')
            # On old51 this assertion must fail AFTER actual second SDK entry,
            # rather than stopping early at the missing C latch itself.
            assert after == before, diagnostic
            assert live.validation_error is first.value
            assert all(error is first.value for error in replay_errors)
            assert not response['success'] and response['error']['code'] == first.value.code
            assert tuple(targets._LOADER_APIS.items()) == readers_before
            assert tuple(live.current_policy_reads) == policy_before
            assert tuple((e, e.state, e.error, e.closer, e.handle, e.keepalive)
                         for e in retained.unresolved()) == original_entries
            assert not originals
            assert tuple((c, c._resume_epoch, c._resume_attempt)
                         for s in live.run.stages for c in [s.child]) == epochs
            assert daemon.store.get_operation(op.admission.operation_id) == original_apply
            assert daemon.store.job(op.admission.job_id) == original_job
            assert op.state == live.run.state == 'READY'
            assert op.first_error is None and op.enrollment.record['first_cause'] is None
        finally:
            daemon.close()
