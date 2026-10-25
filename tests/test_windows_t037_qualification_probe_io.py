"""Raw-output/retirement consumers, NOT the C coordinator or issuer positive.

The normal original prepared factory, child storage, parser, file owners and
Gate close bodies run over the existing inert SDK/filesystem fixture. Its
explicit historical SOFTWARE_ONLY Resume continuation is used only to reach
post-return I/O consumers. No result here qualifies B/C, Resume or Windows.
"""
from contextlib import contextmanager
import copy
import hashlib
import ntpath
import threading

import pytest

from comsol_mcp import _windows_os_probe_protocol as wire
from comsol_mcp import _windows_t037 as win
from comsol_mcp import _windows_t037_launch_inputs as launch
from comsol_mcp import _windows_t037_qualification as q
from comsol_mcp import _windows_t037_acl as acl
from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._windows_owned_resources import registry_for
from tests.test_windows_t037_qualification_original_owner import original_owner, request_frame
from tests.test_windows_suspended_ownership import _launch_api
from tests.test_windows_t037_connected_b import _QuerySDK
from tests.test_windows_prepared_launch import WINROOT, SYSTEMROOT

REAL_ACL = acl.check_suspended_child_dacl


def response_frame(owner, request):
    row = wire.empty_observation(0, 'READ_HASH')
    row.update(entered=True, phase='DONE', body_returned=True, read_count=0,
               read_sha256=hashlib.sha256(b'').hexdigest(), eof_observed=True)
    row['input'].update(acquisition='RETURNED', close='RETURNED')
    return wire.encode_response(dict(version=1, nonce=owner.nonce,
        recipe_sha256=owner.operation.recipe.sha256, frame_status='PARSED', exception_class='',
        stdin=dict(acquisition='RETURNED', close='RETURNED', close_exception_class=''),
        output_observation='UNVERIFIED_SELF_OUTPUT', records=[row]))


@contextmanager
def returned_probe(monkeypatch, tmp_path, *, close_fault=None):
    # Full import/source recording makes this new multi-stage consumer longer
    # than the old ten-second fixture. Its original default and assertions stay
    # unchanged; only this fixture's original callback hold is bounded at40s.
    with original_owner(monkeypatch, tmp_path, fixed_probe=True, callback_hold_seconds=40) as value:
        env = value.env
        factory = value.operation.new_factory(q.Purpose.QUALIFY_OS)
        descriptor = bytes.fromhex(next(t.descriptor_hex for t in env.profile.finite_targets.files
            if t.path == env.profile.workspace_root))
        # The outer PreparedEnvironment has its own SDK adapter wrapper. A new
        # original factory needs the real gate with its own explicit SDK API,
        # rather than recursively capturing that other fixture's wrapper.
        monkeypatch.setattr(acl, 'check_suspended_child_dacl', REAL_ACL)
        _, api, events, model = _launch_api(monkeypatch, profile_override=env.profile,
                                           acl_descriptor=descriptor)
        query = _QuerySDK(monkeypatch, api, env.profile, env.sdk)
        api.hash_image = lambda path: hashlib.sha256(env.Path(path).read_bytes()).hexdigest()
        open_image = api._open_image
        pinned = []
        def image(path):
            pinned.append(path)
            return open_image(path)
        api._open_image = image
        api._hash_image_handle = lambda *_: hashlib.sha256(env.Path(pinned[-1]).read_bytes()).hexdigest()
        api._environment_buffer = win._Win32LaunchApi._environment_buffer.__get__(api)
        api.k.GetSystemWindowsDirectoryW = lambda b, n: env._root(b, WINROOT)
        api.k.GetSystemDirectoryW = lambda b, n: env._root(b, SYSTEMROOT)
        made = []
        def construct(cls):
            assert cls is win._Win32LaunchApi and not made
            assert factory._api is None and factory._job is None
            made.append(api)
            return api
        monkeypatch.setattr(win._Win32LaunchApi, '__new__', staticmethod(construct))
        monkeypatch.setattr(win._Win32LaunchApi, '__init__', lambda self: None)  # SDK constructor leaf only
        assert factory.read_launch_os_roots() == (WINROOT, SYSTEMROOT)
        # Reuse the historical prepared consumer's explicit continuation hooks,
        # not a replacement coordinator/assessment/C or a new native issuer.
        env.factory, env.api, env.events, env.model, env.query = factory, api, events, model, query
        _, worker, _, _, _, _, _ = env.worker()
        api.poll_process = lambda handle: None if model['alive'] else 0
        api.wait_process = lambda handle, timeout, args: 0 if not model['alive'] else (_ for _ in ()).throw(TimeoutError())
        frame = request_frame(value, factory)
        close_calls = []
        if close_fault is not None:
            import tempfile
            creator = tempfile.TemporaryFile; acquisitions = []
            def create(**arguments):
                file = creator(**arguments); acquisitions.append(file)
                if len(acquisitions) == 3:
                    original_close = file.close
                    def close():
                        close_calls.append(file)
                        if close_fault == 'after': original_close()
                        raise OSError('inert original submitted file close return lost')
                    file.close = close  # Captured by the real owner before registration or close entry.
                return file
            monkeypatch.setattr(tempfile, 'TemporaryFile', create)
        io = value.operation.prepare_probe_io(factory, frame)
        value.probe_close_calls = close_calls
        inputs = launch.build_os_probe_inputs(factory, worker.paths, frame=frame, stdio=io.files)
        options = dict(executable=inputs.executable, cwd=inputs.cwd, env=dict(inputs.environment),
            stdin=io.files[0], stdout=io.files[1], stderr=io.files[2], text=False,
            start_new_session=True, creationflags=0)
        with launch.activate_launch_inputs(factory, inputs):
            child = factory(inputs.argv, **options)
        assert io.bind_returned_child(child) is child
        assert child._returned and child._resume_confirmed and child._owner is api
        assert io.submission is inputs and io.child is child
        raw = response_frame(io.owner, wire.decode_request(frame))
        io.files[1].write(raw); io.files[1].flush()  # Explicit inert helper stdout return.
        def close_child():
            model['alive'] = False
            assert child.wait(timeout=0.01) == 0
            child.close()
            from comsol_mcp._windows_t037_b_observation import settle_original_child_b
            settle_original_child_b(child)  # Existing pure original metadata producer, no flag clearing.
        yield value, factory, io, child, raw, close_child


def test_original_probe_complete_raw_read_and_gate_confirmed_close_once(monkeypatch, tmp_path):
    with returned_probe(monkeypatch, tmp_path) as (value, factory, io, child, raw, close_child):
        close_child()
        child_events = list(value.env.events)
        response = io.capture_response()
        assert response.stdout == raw and response.stderr == b'' and response.exit_code == 0
        assert response.check()['output_observation'] == 'UNVERIFIED_SELF_OUTPUT'
        assert io.state == 'CAPTURED' and value.operation.live_c is None
        assert value.env.events == child_events  # Parent readback never polls/reopens native HANDLEs.
        with pytest.raises(ExecutionContractError): io.retire_submitted()
        assert all(not file.closed for file in io.files)
        factory.close()
        assert factory._profile_participant.terminal is not None
        io.retire_submitted()
        assert io.state == 'RETIRED_SUBMITTED' and all(file.closed for file in io.files)
        assert all(slot['close_entered'] for slot in io.acquisitions) and not io.registry.unresolved()
        assert response.check()['records'][0]['read_count'] == 0
        before = (len(io.calls), list(value.env.events))
        with pytest.raises(ExecutionContractError): io.retire_submitted()
        with pytest.raises(ExecutionContractError): copy.copy(response).check()
        assert before == (len(io.calls), value.env.events) and value.operation.live_c is None
        assert not q.retirement_known(value.record)  # Raw I/O retirement is not whole-operation C.


@pytest.mark.parametrize('damage', ['live_child', 'foreign_thread', 'copy_io', 'entry_unknown',
                                   'different_child', 'parent_not_closed', 'files_substitution',
                                   'file_entry_removed', 'parent_binding'])
def test_unproven_child_refuses_before_any_parent_read_or_native_call(monkeypatch, tmp_path, damage):
    with returned_probe(monkeypatch, tmp_path) as (value, factory, io, child, raw, close_child):
        if damage != 'live_child': close_child()
        if damage == 'entry_unknown':
            child._process_entry.state = 'UNKNOWN'; child._process_entry.error = OSError('inert unknown close')
        elif damage == 'different_child': io.child = copy.copy(child)
        elif damage == 'parent_not_closed': child._parent_binding.closed = False
        elif damage == 'parent_binding': child._parent_binding.handle = 999
        elif damage == 'files_substitution': io.files = (io.files[0], io.files[2], io.files[1])
        elif damage == 'file_entry_removed': io.registry._entries.pop(io.acquisitions[1]['entry'].seq)
        before = (len(io.calls), list(value.env.events)); errors = []
        def call():
            try: (copy.copy(io) if damage == 'copy_io' else io).capture_response()
            except ExecutionContractError as error: errors.append(error)
        if damage == 'foreign_thread':
            thread = threading.Thread(target=call); thread.start(); thread.join(2); assert not thread.is_alive()
        else: call()
        assert len(errors) == 1 and before == (len(io.calls), value.env.events)
        assert io.response is None and all(not file.closed for file in io.files)
        assert value.operation.live_c is None


@pytest.mark.parametrize('fault', ['oversize', 'partial_frame', 'nonce', 'changed_during_read', 'read_return_loss'])
def test_original_output_failure_retains_actual_calls_and_prevents_recapture(monkeypatch, tmp_path, fault):
    with returned_probe(monkeypatch, tmp_path) as (value, factory, io, child, raw, close_child):
        close_child()
        output = io.files[1]
        if fault == 'oversize': output.seek(wire.MAX_RESPONSE); output.write(b'!'); output.flush()
        elif fault == 'partial_frame': output.truncate(len(raw)-1)
        elif fault == 'nonce':
            decoded = wire.decode_response(raw, expected_nonce=io.owner.nonce,
                expected_recipe_sha256=value.operation.recipe.sha256)
            decoded['nonce'] = '0'*32; output.seek(0); output.write(wire.encode_response(decoded)); output.flush()
        else:
            read = output.read; calls = []
            def faulty_read(bound):
                data = read(bound); calls.append(data)
                if fault == 'read_return_loss': raise OSError('inert output read result lost')
                return data if len(calls) == 1 else data[:-1]
            output.read = faulty_read  # Explicit original-file return/processing leaf.
        with pytest.raises((ExecutionContractError, wire.ProtocolError, OSError)) as error: io.capture_response()
        assert io.state == 'UNKNOWN' and io.first_error is value.operation.first_error is error.value
        assert io.calls and all(not file.closed for file in io.files) and io.response is None
        before = (len(io.calls), list(value.env.events))
        with pytest.raises(ExecutionContractError): io.capture_response()
        with pytest.raises(ExecutionContractError): io.retire_submitted()
        assert before == (len(io.calls), value.env.events) and value.operation.live_c is None


def test_substituted_submitted_file_closer_refuses_before_entry(monkeypatch, tmp_path):
    with returned_probe(monkeypatch, tmp_path) as (value, factory, io, child, raw, close_child):
        close_child(); io.capture_response(); factory.close()
        slot = io.acquisitions[-1]; original = slot['closer']; calls = []
        # Pure foreign-target refusal: the SDK/file closer must not be entered.
        slot['closer'] = lambda: calls.append('substituted')
        with pytest.raises(ExecutionContractError): io.retire_submitted()
        assert not calls and slot['entry'].state == 'ACQUIRED' and io.state == 'CAPTURED'
        assert all(not file.closed for file in io.files) and value.operation.live_c is None
        with pytest.raises(ExecutionContractError): io.retire_submitted()
        assert not calls and original is not slot['closer']


@pytest.mark.parametrize('fault', ['before', 'after'])
def test_original_submitted_close_loss_keeps_exact_file_and_calls_once(monkeypatch, tmp_path, fault):
    with returned_probe(monkeypatch, tmp_path, close_fault=fault) as (value, factory, io, child, raw, close_child):
        close_child(); io.capture_response(); factory.close()
        with pytest.raises(OSError) as error: io.retire_submitted()
        slot = io.acquisitions[-1]
        assert value.probe_close_calls == [slot['returned']] and slot['close_entered']
        assert slot['returned'].closed is (fault == 'after') and slot['entry'].state == 'UNKNOWN'
        assert slot['entry'].error is io.first_error is value.operation.first_error is error.value
        assert slot['entry'].keepalive[0] is slot and all(not file.closed for file in io.files[:2])
        before = list(value.env.events)
        with pytest.raises(ExecutionContractError): io.retire_submitted()
        with pytest.raises(ExecutionContractError): io.capture_response()
        assert value.probe_close_calls == [slot['returned']] and value.env.events == before
        assert value.operation.live_c is None


@pytest.mark.parametrize('data', ['stderr', 'unclosed_resource'])
def test_complete_error_data_is_kept_as_data_and_never_upgraded_to_c(monkeypatch, tmp_path, data):
    with returned_probe(monkeypatch, tmp_path) as (value, factory, io, child, raw, close_child):
        if data == 'stderr': io.files[2].write(b'opaque helper diagnostic'); io.files[2].flush()
        else:
            decoded = wire.decode_response(raw, expected_nonce=io.owner.nonce,
                expected_recipe_sha256=value.operation.recipe.sha256)
            decoded['records'][0]['input'].update(close='THREW', close_exception_class='java.io.IOException')
            raw = wire.encode_response(decoded)
            io.files[1].seek(0); io.files[1].write(raw); io.files[1].flush()
        close_child(); response = io.capture_response()
        assert response.stdout == raw and response.stderr == (b'opaque helper diagnostic' if data == 'stderr' else b'')
        assert response.check()['output_observation'] == 'UNVERIFIED_SELF_OUTPUT'
        assert value.operation.os_result is None and value.operation.live_c is None
        factory.close(); io.retire_submitted()
        assert io.state == 'RETIRED_SUBMITTED' and not q.retirement_known(value.record)
