import ctypes as C
import sys
from types import SimpleNamespace
from unittest.mock import patch

import test_t38_windows_create_protocol as protocol
from comsol_mcp import _windows_parameter_publish as publish
from comsol_mcp import _windows_state_restore as state_restore
from comsol_mcp._windows_host_preflight_owner import hold_original
from comsol_mcp._windows_owned_resources import registry_for


def test_dispatched_create_exception_without_output_stays_unknown():
    api, registry, close_calls, retained, state, restore_module = protocol._native_create_case(
        raised=KeyboardInterrupt("injected after native dispatch"), output_value=None,
    )

    with patch.dict(sys.modules, {"comsol_mcp._windows_state_restore": restore_module}):
        try:
            api._open_relative(C.c_void_p(900), "stage.partial", create=True)
        except publish.PublishError as caught:
            error = caught
        else:
            raise AssertionError("dispatched create exception must remain UNKNOWN")

    assert error.code == "ACQUISITION_STATE_UNKNOWN"
    assert error.stage == "post_dispatch"
    assert error.safe_retry is False
    assert error.original_native_output.value is None
    assert error.original_native_context[-1].Information == 2
    assert retained[0]["output"] is error.original_native_output
    assert registry.entries == []
    assert close_calls == []
    assert state["staging_present"] is True


def test_explicit_pre_dispatch_refusal_is_not_reclassified_unknown():
    refusal = publish.PublishError(
        "HOST_PREFLIGHT_REFUSED", "injected before NtCreateFile dispatch", stage="pre_dispatch",
    )

    class PreflightRefusal:
        first_cause = None

        def allocation_call(self, *_args, **_kwargs):
            raise refusal

    api = publish._API.__new__(publish._API)
    api.nt = SimpleNamespace(NtCreateFile=None, RtlNtStatusToDosError=lambda _status: 183)
    api._ownership_registry = lambda: None
    api._resource_closer = lambda: (lambda _handle: True)
    api._readonly_preflight_owner = PreflightRefusal()

    try:
        api._open_relative(C.c_void_p(900), "stage.partial", create=True)
    except publish.PublishError as caught:
        error = caught
    else:
        raise AssertionError("pre-dispatch refusal should propagate as its original definite error")

    assert error is refusal
    assert error.code == "HOST_PREFLIGHT_REFUSED"
    assert error.stage == "pre_dispatch"


def test_preflight_dispatch_exception_without_output_stays_unknown():
    primary = RuntimeError("injected preflight native-call exception")
    api = publish._API.__new__(publish._API)
    api.k = SimpleNamespace(CloseHandle=lambda _handle: 1)

    def nt_create(*_args):
        raise primary

    api.nt = SimpleNamespace(NtCreateFile=nt_create, RtlNtStatusToDosError=lambda _status: 183)
    preflight = hold_original(api)

    try:
        api._open_relative(C.c_void_p(900), "stage.partial", create=True)
    except publish.PublishError as caught:
        error = caught
    else:
        raise AssertionError("recorded preflight dispatch failure must remain UNKNOWN")

    owner = next(row for row in state_restore.retained_unknown_owners() if row["api"] is api)
    assert error.code == "ACQUISITION_STATE_UNKNOWN"
    assert error.stage == "post_dispatch"
    assert error.safe_retry is False
    assert error.original_cause is primary
    assert error.original_native_output.value is None
    assert preflight._allocation_call_dispatched is True
    assert owner["output"] is error.original_native_output


def test_registry_failure_propagates_unknown_through_publish_wrapper():
    registry_error = RuntimeError("injected registry capture failure")
    native, registry, close_calls, _retained, state, _restore_module = protocol._native_create_case(
        registry_error=registry_error,
    )

    class NativeCreateAdapter(protocol.FakeApi):
        def create_owned(self, _parent, leaf):
            self.stage_leaf = leaf
            return native._open_relative(C.c_void_p(900), leaf, create=True)

    api = NativeCreateAdapter()
    try:
        protocol._publish(api)
    except publish.PublishError as caught:
        error = caught
    else:
        raise AssertionError("registry failure after native create must propagate as UNKNOWN")

    cause = error.__cause__
    assert error.code == "ARTIFACT_PUBLISH_UNKNOWN"
    assert error.stage == "post_dispatch"
    assert error.safe_retry is False
    assert error.details["cleanup_verified"] is False
    assert error.details["residual_status"] == "UNKNOWN"
    assert error.details["final_state"] == "UNKNOWN"
    assert error.details["publication_committed"] is False
    assert error.details["cause_code"] == "ACQUISITION_STATE_UNKNOWN"
    assert cause.code == "ACQUISITION_STATE_UNKNOWN"
    assert cause.ownership_registry_error is registry_error
    assert cause.original_native_output.value == 0x1234
    owner = next(row for row in state_restore.retained_unknown_owners() if row["api"] is native)
    assert owner["output"] is cause.original_native_output
    assert owner["closer"] is native._resource_closer()
    assert state["staging_present"] is True
    assert registry.entries == []
    assert close_calls == []
    assert api.delete_calls == []
    assert api.close_calls == []
    assert api.exists_checks == ["model.zip"]


def test_preflight_registry_capture_failure_is_not_retried():
    api = publish._API.__new__(publish._API)
    api.k = SimpleNamespace(CloseHandle=lambda _handle: 1)

    def nt_create(output_arg, _access, _attributes, io_arg, *_args):
        output = C.cast(output_arg, C.POINTER(publish.HANDLE)).contents
        io_status = C.cast(io_arg, C.POINTER(publish._IoStatusBlock)).contents
        output.value = 0x5678
        io_status.Status = 0
        io_status.Information = 2
        return 0

    api.nt = SimpleNamespace(NtCreateFile=nt_create, RtlNtStatusToDosError=lambda _status: 183)
    preflight = hold_original(api)
    registry = registry_for(api)
    registration_error = RuntimeError("injected first registry capture failure")
    attempts = []

    def fail_capture(*_args, **_kwargs):
        attempts.append("capture")
        raise registration_error

    registry.acquire = fail_capture

    try:
        api._open_relative(C.c_void_p(900), "stage.partial", create=True)
    except publish.PublishError as caught:
        error = caught
    else:
        raise AssertionError("preflight registry capture failure must remain UNKNOWN")

    owner = next(row for row in state_restore.retained_unknown_owners() if row["api"] is api)
    assert error.code == "ACQUISITION_STATE_UNKNOWN"
    assert error.safe_retry is False
    assert error.original_native_output.value == 0x5678
    assert owner["output"] is error.original_native_output
    assert error.ownership_registry_error is registration_error
    assert attempts == ["capture"]


def test_stale_preflight_cause_does_not_reclassify_pre_dispatch_refusal():
    refusal = publish.PublishError(
        "HOST_PREFLIGHT_REFUSED", "injected preflight refusal", stage="pre_dispatch",
    )
    api = publish._API.__new__(publish._API)
    api.k = SimpleNamespace(CloseHandle=lambda _handle: 1)
    api.nt = SimpleNamespace(NtCreateFile=None, RtlNtStatusToDosError=lambda _status: 183)
    preflight = hold_original(api)
    preflight.first_cause = RuntimeError("unrelated earlier owner cause")

    def refuse_before_native_call(_name, _dll):
        raise refusal

    preflight.function = refuse_before_native_call
    try:
        api._open_relative(C.c_void_p(900), "stage.partial", create=True)
    except publish.PublishError as caught:
        error = caught
    else:
        raise AssertionError("stale owner cause must not turn refusal into UNKNOWN")

    assert error is refusal
    assert preflight._allocation_call_dispatched is False
    assert all(row["api"] is not api for row in state_restore.retained_unknown_owners())
