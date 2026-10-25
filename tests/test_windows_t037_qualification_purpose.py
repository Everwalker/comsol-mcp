"""Owner-negative source controls, not positive C/normal-loader qualification."""
import copy
from types import SimpleNamespace as NS
import pytest
from comsol_mcp import _windows_t037_qualification as q
from comsol_mcp._execution_contract import ExecutionContractError


@pytest.mark.parametrize('owner',[None,{},NS(purpose=q.Purpose.QUALIFY_OS),q.Purpose.QUALIFY_OS])
def test_serialized_or_unregistered_purpose_never_mints_qualification(owner):
    factory=NS(_qualification_purpose=owner)
    with pytest.raises(ExecutionContractError) as error:q.require_purpose(factory)
    assert error.value.code=='T037_QUALIFICATION_OWNER_REQUIRED'


def test_private_value_construction_without_original_identity_roots_still_refuses():
    operation=q.OriginalQualificationOperation.__new__(q.OriginalQualificationOperation)
    operation._seal=q._SEAL;operation.state='COMPLETE';operation.first_error=None
    record={'qualification':operation}
    assert not q.retirement_known(record)
    assert not q.retirement_known({'qualification':dict(state='COMPLETE',known=True)})
    assert q.retirement_known({})
    with pytest.raises(ExecutionContractError):q.OriginalPurposeOwner(operation,None,q.Purpose.QUALIFY_OS,seal=object())
    with pytest.raises(ExecutionContractError):q.OriginalQualificationOperation(None,None,seal=object())


@pytest.mark.parametrize('request_value',[None,{},NS(plan='caller',phase='APPLY',_used=True)])
def test_caller_record_cannot_enter_enrollment_or_native_constructor(request_value):
    with pytest.raises(ExecutionContractError) as error:q.enroll_executor(None,request_value,{},None)
    assert error.value.code=='T037_QUALIFICATION_OWNER_REQUIRED'


@pytest.mark.parametrize('owner',[None,{},NS(purpose=q.Purpose.QUALIFY_OS)])
def test_fixed_os_input_builder_refuses_unbound_purpose_before_any_material_or_sdk(owner):
    from comsol_mcp._windows_t037_launch_inputs import build_os_probe_inputs
    class UnreadPaths:
        def executable(self,*args):raise AssertionError('unbound caller reached filesystem/executable')
    with pytest.raises(ExecutionContractError) as error:
        build_os_probe_inputs(NS(_qualification_purpose=owner),UnreadPaths(),frame=b'caller',stdio=())
    assert error.value.code=='T037_QUALIFICATION_OWNER_REQUIRED'
