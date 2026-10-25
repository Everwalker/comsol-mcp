"""Original coordinator controls on inert OS observations; Windows/native NOT_RUN."""
import pytest
from comsol_mcp import _windows_t037_qualification_run as run
from comsol_mcp._execution_contract import ExecutionContractError
from tests._controller_c_software_fixture import first_os_stage


def test_original_file_stage_uses_real_issuer_primary_reader_and_known_retirement(monkeypatch,tmp_path):
    with first_os_stage(monkeypatch,tmp_path) as (value,stage,api,state):
        value.operation.coordinator.run_stage(stage)
        assert stage.accepted and stage.factory._closed and stage.io.state=='RETIRED_SUBMITTED'
        assert stage.child._resume_attempt.state=='HANDED_OFF' and stage.child._resume_attempt.previous_count==1
        assert stage.child._connected_b_observer.result.finite_state=='MATCHED_FINITE_CHECKS_ONLY'
        assert all(p.retired for p in stage.parents) and stage.response.terminal_data()['frame_status']=='PARSED'
        assert value.operation.live_c is None and not value.operation.coordinator.vendor_closed
        assert state['events'].count('RESUME')==1
        with pytest.raises(ExecutionContractError):run.require_live_c(value.env.daemon)


def test_original_file_stage_rejects_extent_without_publishing_c_or_retry(monkeypatch,tmp_path):
    with first_os_stage(monkeypatch,tmp_path,output_fault='extent') as (value,stage,api,state):
        with pytest.raises(ExecutionContractError):value.operation.coordinator.run_stage(stage)
        assert not stage.accepted and value.operation.live_c is None
        assert state['events'].count('RESUME')==1


def test_original_held_a_refuses_control_create_directory_time_change_before_return(monkeypatch,tmp_path):
    """Scheduling counter, not a relaxation of the original fingerprint check."""
    with first_os_stage(monkeypatch,tmp_path,output_fault='directory_time') as (value,stage,api,state):
        with pytest.raises(ExecutionContractError) as caught:
            value.operation.coordinator.run_stage(stage)
        import traceback,json
        child=state['children'][0] if state['children'] else None
        (tmp_path/'directory-counter.json').write_text(json.dumps(dict(error_code=caught.value.code,
            error=str(caught.value),traceback=''.join(traceback.format_exception(caught.value)),
            events=[str(x) for x in state['events']],directory_changed=state.get('directory_changed'),
            child_count=len(state['children']),resume_state=getattr(getattr(child,'_resume_attempt',None),'state',None)),indent=1)+'\n')
        assert caught.value.code=='T037_POLICY_UNVERIFIED'
        assert caught.value.details['reasons']==['FINITE_TARGET_CHANGED']
        assert state['events'].count('RESUME')==1
        assert state['directory_changed'] and len(state['children'])==1
        child=state['children'][0]
        assert child._resume_attempt.previous_count==1 and child._resume_attempt.state=='HANDED_OFF'
        assert not stage.accepted and stage.response is None and value.operation.live_c is None
        from comsol_mcp._windows_t037_targets import NativeTargetReader
        actual=[reader for reader in stage.factory._prebirth_observers if type(reader) is NativeTargetReader
                and getattr(getattr(reader,'_held_a_result',None),'cleanup_error',None) is caught.value]
        assert len(actual)==1 and actual[0]._finish_error is caught.value
        # No held-A identity/security/time requirement is changed by this test.
