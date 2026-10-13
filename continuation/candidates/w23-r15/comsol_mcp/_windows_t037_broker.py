"""Windows SCM entrypoint and sealed internal host-control requests.

This module implements the documented Service Control Manager callback
protocol. It does not install a service, create an account, change ACL/WFP
state, or prove T037 isolation. The actual installation/broker binding and
native Windows validation remain separate deployment work.

SCM APIs are loaded only on Windows. Software tests inject a private fake API
through the explicit test constructor; that path never becomes a production
proof or a launch fallback.
"""
from __future__ import annotations

import ctypes
from dataclasses import dataclass, field
import os
import re
import threading
from typing import Any, Callable, Mapping

from ._execution_contract import ExecutionContractError

ERROR_CALL_NOT_IMPLEMENTED = 120
ERROR_SUCCESS = 0
ERROR_SERVICE_SPECIFIC_ERROR = 1066
SERVICE_CONTROL_STOP = 0x00000001
SERVICE_CONTROL_INTERROGATE = 0x00000004
SERVICE_CONTROL_SHUTDOWN = 0x00000005
SERVICE_WIN32_OWN_PROCESS = 0x00000010
SERVICE_ACCEPT_STOP = 0x00000001
SERVICE_ACCEPT_SHUTDOWN = 0x00000004
SERVICE_STOPPED = 0x00000001
SERVICE_START_PENDING = 0x00000002
SERVICE_STOP_PENDING = 0x00000003
SERVICE_RUNNING = 0x00000004
_SERVICE_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,256}$")
_MAX_START_ARGUMENTS = 32


class WindowsScmError(ExecutionContractError):
    """Typed refusal or failure in the Windows service dispatcher protocol."""


def _fail(code: str, message: str, *, details: Mapping[str, Any] | None = None) -> None:
    raise WindowsScmError(code, message, safe_retry=False, stage="validation", details=details)


# Windows WCHAR is always 16 bits. Do not use c_wchar/wintypes.LPWSTR in
# portable layout declarations: on Darwin c_wchar is 32 bits.
DWORD = ctypes.c_uint32
BOOL = ctypes.c_int32
HANDLE = ctypes.c_void_p
WCHAR = ctypes.c_uint16
LPWSTR16 = ctypes.POINTER(WCHAR)
LPWSTR16_ARRAY = ctypes.POINTER(LPWSTR16)
_CALLBACK_FACTORY = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
SERVICE_MAIN_FUNCTIONW = _CALLBACK_FACTORY(None, DWORD, LPWSTR16_ARRAY)
HANDLER_FUNCTION_EX = _CALLBACK_FACTORY(DWORD, DWORD, DWORD, ctypes.c_void_p, ctypes.c_void_p)


class SERVICE_STATUS(ctypes.Structure):
    _fields_ = [
        ("dwServiceType", DWORD),
        ("dwCurrentState", DWORD),
        ("dwControlsAccepted", DWORD),
        ("dwWin32ExitCode", DWORD),
        ("dwServiceSpecificExitCode", DWORD),
        ("dwCheckPoint", DWORD),
        ("dwWaitHint", DWORD),
    ]


class SERVICE_TABLE_ENTRYW(ctypes.Structure):
    _fields_ = [("lpServiceName", LPWSTR16), ("lpServiceProc", SERVICE_MAIN_FUNCTIONW)]


def scm_abi_layout() -> dict[str, Any]:
    """Return fixed-width declarations and host layout for review/tests."""
    return {
        "DWORD_size": ctypes.sizeof(DWORD),
        "BOOL_size": ctypes.sizeof(BOOL),
        "WCHAR_size": ctypes.sizeof(WCHAR),
        "HANDLE_size": ctypes.sizeof(HANDLE),
        "SERVICE_STATUS_size": ctypes.sizeof(SERVICE_STATUS),
        "SERVICE_STATUS_offsets": {name: getattr(SERVICE_STATUS, name).offset for name, _ in SERVICE_STATUS._fields_},
        "SERVICE_TABLE_ENTRYW_size": ctypes.sizeof(SERVICE_TABLE_ENTRYW),
        "SERVICE_TABLE_ENTRYW_offsets": {
            "lpServiceName": SERVICE_TABLE_ENTRYW.lpServiceName.offset,
            "lpServiceProc": SERVICE_TABLE_ENTRYW.lpServiceProc.offset,
        },
        "pointer_size": ctypes.sizeof(ctypes.c_void_p),
    }


def _validate_abi() -> None:
    layout = scm_abi_layout()
    pointer_size = layout["pointer_size"]
    if (layout["DWORD_size"] != 4 or layout["BOOL_size"] != 4 or layout["WCHAR_size"] != 2
            or layout["SERVICE_STATUS_size"] != 28
            or layout["SERVICE_STATUS_offsets"] != {
                "dwServiceType": 0, "dwCurrentState": 4, "dwControlsAccepted": 8,
                "dwWin32ExitCode": 12, "dwServiceSpecificExitCode": 16,
                "dwCheckPoint": 20, "dwWaitHint": 24,
            }
            or pointer_size not in {4, 8}
            or layout["SERVICE_TABLE_ENTRYW_size"] != 2 * pointer_size
            or layout["SERVICE_TABLE_ENTRYW_offsets"] != {
                "lpServiceName": 0, "lpServiceProc": pointer_size,
            }):
        _fail("SCM_ABI_UNSUPPORTED", "ctypes service ABI layout does not match fixed Win32 widths")


def _bind_export(dll: Any, name: str, argtypes: list[Any], restype: Any) -> Any:
    try:
        fn = getattr(dll, name)
    except AttributeError as exc:
        _fail("SCM_API_EXPORT_UNAVAILABLE", f"required Win32 export {name} is unavailable")
    if not callable(fn):
        _fail("SCM_API_EXPORT_INVALID", f"required Win32 export {name} is not callable")
    try:
        fn.argtypes = argtypes
        fn.restype = restype
    except Exception as exc:
        _fail("SCM_API_ABI_BINDING_FAILED", f"Win32 export {name} rejected its documented ctypes ABI",
              details={"cause_type": type(exc).__name__})
    return fn


class CtypesWindowsScmApi:
    """Late-bound fixed-width wrapper for the documented SCM dispatcher APIs."""

    def __init__(self) -> None:
        if os.name != "nt":
            _fail("SCM_WINDOWS_REQUIRED", "Windows SCM APIs are unavailable on this host")
        _validate_abi()
        try:
            self._advapi = ctypes.WinDLL("advapi32", use_last_error=True)
            self._kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        except Exception as exc:
            _fail("SCM_DLL_LOAD_FAILED", "Windows SCM libraries could not be loaded",
                  details={"cause_type": type(exc).__name__})
        self._bind()
        self._active_callbacks: tuple[Any, ...] = ()

    @staticmethod
    def bind_exports(advapi: Any, kernel: Any) -> dict[str, Any]:
        """Bind only the named Win32 exports; exposed for deterministic ABI tests."""
        _validate_abi()
        return {
            "StartServiceCtrlDispatcherW": _bind_export(
                advapi, "StartServiceCtrlDispatcherW", [ctypes.POINTER(SERVICE_TABLE_ENTRYW)], BOOL),
            "RegisterServiceCtrlHandlerExW": _bind_export(
                advapi, "RegisterServiceCtrlHandlerExW",
                [LPWSTR16, HANDLER_FUNCTION_EX, ctypes.c_void_p], HANDLE),
            "SetServiceStatus": _bind_export(
                advapi, "SetServiceStatus", [HANDLE, ctypes.POINTER(SERVICE_STATUS)], BOOL),
            "GetLastError": _bind_export(kernel, "GetLastError", [], DWORD),
        }

    def _bind(self) -> None:
        exports = self.bind_exports(self._advapi, self._kernel)
        self._start = exports["StartServiceCtrlDispatcherW"]
        self._register = exports["RegisterServiceCtrlHandlerExW"]
        self._set_status = exports["SetServiceStatus"]
        self._get_last_error = exports["GetLastError"]

    @staticmethod
    def _wide_name(value: str):
        raw = value.encode("utf-16-le", errors="strict")
        units = [raw[i] | (raw[i + 1] << 8) for i in range(0, len(raw), 2)] + [0]
        return (WCHAR * len(units))(*units)

    def start_service_ctrl_dispatcher(self, service_name: str,
                                      service_main: Callable[[int, Any], None]) -> None:
        name = self._wide_name(service_name)
        callback = SERVICE_MAIN_FUNCTIONW(service_main)
        table = (SERVICE_TABLE_ENTRYW * 2)()
        table[0].lpServiceName = ctypes.cast(name, LPWSTR16)
        table[0].lpServiceProc = callback
        # The zero-initialized final entry is the required NULL terminator.
        self._active_callbacks = (name, callback, table)
        if not self._start(table):
            error = int(self._get_last_error())
            self._active_callbacks = ()
            _fail("SCM_DISPATCH_FAILED", "StartServiceCtrlDispatcherW failed",
                  details={"winerror": error})
        self._active_callbacks = ()

    def register_service_ctrl_handler_ex(self, service_name: str,
                                         handler: Callable[[int, int, Any, Any], int]) -> Any:
        name = self._wide_name(service_name)
        callback = HANDLER_FUNCTION_EX(handler)
        handle = self._register(ctypes.cast(name, LPWSTR16), callback, None)
        if not handle:
            error = int(self._get_last_error())
            _fail("SCM_HANDLER_REGISTRATION_FAILED", "RegisterServiceCtrlHandlerExW failed",
                  details={"winerror": error})
        current = self._active_callbacks
        self._active_callbacks = (*current, name, callback)
        return handle

    def set_service_status(self, handle: Any, status: "ServiceStatus") -> None:
        native = SERVICE_STATUS(
            SERVICE_WIN32_OWN_PROCESS, status.current_state, status.controls_accepted,
            status.win32_exit_code, status.service_specific_exit_code,
            status.checkpoint, status.wait_hint_ms,
        )
        if not self._set_status(handle, ctypes.byref(native)):
            error = int(self._get_last_error())
            _fail("SCM_STATUS_UPDATE_FAILED", "SetServiceStatus failed",
                  details={"winerror": error, "state": status.current_state})


@dataclass(frozen=True)
class ServiceStatus:
    current_state: int
    controls_accepted: int = 0
    win32_exit_code: int = 0
    service_specific_exit_code: int = 0
    checkpoint: int = 0
    wait_hint_ms: int = 0


_SCM_RUNTIME_SEAL = object()
_SCM_TEST_API_TOKEN = object()
_HOST_CONTROL_REQUEST_SEAL = object()


class ScmServiceRuntime:
    """Short-lived service context only created by the SCM broker callback."""

    def __init__(self, broker: "WindowsScmServiceBroker", seal: object):
        if seal is not _SCM_RUNTIME_SEAL:
            _fail("SCM_RUNTIME_SEAL_REQUIRED", "service runtime can only be created by the SCM broker")
        self._broker = broker
        self._seal = seal
        self._stop_event = threading.Event()
        self._active = True
        self._status_handle: Any = None
        self._state = "REGISTERING"
        self._lock = threading.RLock()

    @property
    def service_name(self) -> str:
        return self._broker.service_name

    @property
    def is_active(self) -> bool:
        with self._lock:
            return self._active and self._state == "RUNNING" and not self._stop_event.is_set()

    @property
    def stop_requested(self) -> bool:
        return self._stop_event.is_set()

    def mark_running(self) -> None:
        self._broker._mark_running(self)

    def wait_for_stop(self, timeout: float | None = None) -> bool:
        """Wait for a stop/shutdown signal; True means a stop was requested."""
        return self._stop_event.wait(timeout)

    def _activate(self, handle: Any) -> None:
        with self._lock:
            self._status_handle = handle
            self._state = "START_PENDING"

    def _deactivate(self) -> None:
        with self._lock:
            self._active = False
            self._state = "STOPPED"


class WindowsScmServiceBroker:
    """Own-process SCM dispatcher wrapper with no console/Popen fallback."""

    def __init__(self, service_name: str,
                 service_main: Callable[[ScmServiceRuntime], None], *,
                 api: Any | None = None, _test_token: object | None = None):
        if not isinstance(service_name, str) or not _SERVICE_NAME.fullmatch(service_name):
            _fail("SCM_SERVICE_NAME_INVALID", "service_name must be a bounded canonical SCM service name")
        if not callable(service_main):
            _fail("SCM_SERVICE_MAIN_REQUIRED", "service_main callback is required")
        if api is not None and _test_token is not _SCM_TEST_API_TOKEN:
            _fail("SCM_TEST_API_FORBIDDEN", "injected SCM APIs are reserved for explicit software tests")
        self.service_name = service_name
        self._service_main = service_main
        self._api = api if api is not None else CtypesWindowsScmApi()
        for name in ("start_service_ctrl_dispatcher", "register_service_ctrl_handler_ex", "set_service_status"):
            if not callable(getattr(self._api, name, None)):
                _fail("SCM_API_PROTOCOL_INVALID", f"SCM adapter is missing {name}")
        self._lock = threading.RLock()
        self._started = False
        self._entered = False
        self._runtime: ScmServiceRuntime | None = None
        self._error: BaseException | None = None

    @classmethod
    def _for_software_test(cls, service_name: str,
                           service_main: Callable[[ScmServiceRuntime], None], *,
                           api: Any, token: object) -> "WindowsScmServiceBroker":
        if token is not _SCM_TEST_API_TOKEN:
            _fail("SCM_TEST_TOKEN_REQUIRED", "synthetic SCM adapter requires its private test token")
        return cls(service_name, service_main, api=api, _test_token=_SCM_TEST_API_TOKEN)

    def run(self) -> None:
        with self._lock:
            if self._started:
                _fail("SCM_DISPATCH_ALREADY_STARTED", "StartServiceCtrlDispatcherW may be invoked once per process")
            self._started = True
        self._api.start_service_ctrl_dispatcher(self.service_name, self._service_main_callback)
        if self._error is not None:
            raise WindowsScmError("SCM_SERVICE_RUNTIME_FAILED", "SCM service callback failed",
                                  safe_retry=False, stage="execution",
                                  details={"cause_type": type(self._error).__name__}) from self._error
        if not self._entered:
            _fail("SCM_DISPATCH_RETURNED_WITHOUT_SERVICE", "SCM dispatcher returned without a ServiceMain callback")

    def _service_main_callback(self, argument_count: Any, _arguments: Any) -> None:
        with self._lock:
            if self._entered:
                self._error = WindowsScmError("SCM_DUPLICATE_SERVICE_MAIN", "ServiceMain callback was invoked more than once")
                return
            self._entered = True
        runtime = ScmServiceRuntime(self, _SCM_RUNTIME_SEAL)
        self._runtime = runtime
        try:
            # The documented sequence registers HandlerEx immediately on entry,
            # before the first SetServiceStatus call or service initialization.
            handler = self._handler_callback
            status_handle = self._api.register_service_ctrl_handler_ex(self.service_name, handler)
            if status_handle in (None, 0):
                _fail("SCM_HANDLER_REGISTRATION_FAILED", "SCM handler registration returned no status handle")
            runtime._activate(status_handle)
            self._set_status(runtime, ServiceStatus(
                SERVICE_START_PENDING, checkpoint=1, wait_hint_ms=30_000,
            ))
            if isinstance(argument_count, int) and argument_count > _MAX_START_ARGUMENTS:
                _fail("SCM_START_ARGUMENTS_EXCESSIVE", "SCM start argument count exceeds the bounded service contract")
            self._service_main(runtime)
            # ServiceMain must remain alive until a real SCM stop/shutdown
            # callback. Returning while START_PENDING is a failed startup;
            # returning while RUNNING is an unannounced service exit.
            if runtime._state == "START_PENDING" and not runtime.stop_requested:
                _fail("SCM_SERVICE_MAIN_RETURNED_BEFORE_RUNNING",
                      "service callback returned before reporting SERVICE_RUNNING")
            if runtime._state == "RUNNING" and not runtime.stop_requested:
                _fail("SCM_SERVICE_MAIN_RETURNED_ACTIVE",
                      "service callback returned while the service was still active")
        except BaseException as exc:
            with self._lock:
                self._error = exc
        finally:
            try:
                if runtime._status_handle is not None and runtime._state != "STOPPED":
                    self._set_status(runtime, ServiceStatus(
                        SERVICE_STOPPED,
                        win32_exit_code=(0 if self._error is None else ERROR_SERVICE_SPECIFIC_ERROR),
                        service_specific_exit_code=(0 if self._error is None else 1),
                    ))
            except BaseException as cleanup_exc:
                if self._error is None:
                    self._error = cleanup_exc
            runtime._deactivate()

    def _handler_callback(self, control: Any, _event_type: Any, _event_data: Any, _context: Any) -> int:
        try:
            code = int(control)
            runtime = self._runtime
            if runtime is None:
                return ERROR_CALL_NOT_IMPLEMENTED
            if code == SERVICE_CONTROL_INTERROGATE:
                return ERROR_SUCCESS
            if code not in {SERVICE_CONTROL_STOP, SERVICE_CONTROL_SHUTDOWN}:
                return ERROR_CALL_NOT_IMPLEMENTED
            runtime._stop_event.set()
            with runtime._lock:
                running = runtime._state == "RUNNING"
            if running:
                self._set_status(runtime, ServiceStatus(
                    SERVICE_STOP_PENDING, checkpoint=1, wait_hint_ms=30_000,
                ))
            return ERROR_SUCCESS
        except BaseException as exc:
            with self._lock:
                if self._error is None:
                    self._error = exc
            return 31  # ERROR_GEN_FAILURE; never unwind an exception through Win32.

    def _set_status(self, runtime: ScmServiceRuntime, status: ServiceStatus) -> None:
        with runtime._lock:
            previous = runtime._state
            allowed = {
                "START_PENDING": {"START_PENDING", "RUNNING", "STOPPED"},
                "RUNNING": {"STOP_PENDING", "STOPPED"},
                "STOP_PENDING": {"STOPPED"},
            }
            target_name = {
                SERVICE_START_PENDING: "START_PENDING", SERVICE_RUNNING: "RUNNING",
                SERVICE_STOP_PENDING: "STOP_PENDING", SERVICE_STOPPED: "STOPPED",
            }.get(status.current_state)
            if target_name is None or target_name not in allowed.get(previous, set()):
                _fail("SCM_STATUS_TRANSITION_INVALID", "service status transition is not allowed",
                      details={"from": previous, "to": status.current_state})
            if previous == "STOPPED":
                _fail("SCM_STATUS_AFTER_STOPPED", "no service status calls are allowed after SERVICE_STOPPED")
            if status.current_state == SERVICE_RUNNING:
                status = ServiceStatus(SERVICE_RUNNING,
                                       SERVICE_ACCEPT_STOP | SERVICE_ACCEPT_SHUTDOWN)
            self._api.set_service_status(runtime._status_handle, status)
            runtime._state = target_name

    def _mark_running(self, runtime: ScmServiceRuntime) -> None:
        if runtime is not self._runtime or runtime._seal is not _SCM_RUNTIME_SEAL:
            _fail("SCM_RUNTIME_MISMATCH", "service readiness came from another or forged SCM runtime")
        if runtime.stop_requested:
            _fail("SCM_START_ABORTED", "service cannot report ready after SCM requested stop")
        self._set_status(runtime, ServiceStatus(SERVICE_RUNNING,
                                                SERVICE_ACCEPT_STOP | SERVICE_ACCEPT_SHUTDOWN))


def _authorization_ref(value: Any) -> str:
    if (not isinstance(value, str) or not value.strip() or len(value) > 512
            or any(ord(char) < 32 for char in value)):
        _fail("HOST_CONTROL_AUTHORIZATION_REQUIRED", "host control requires a bounded trusted authorization reference")
    return value


class InternalHostControlRequest:
    """Non-serializable, one-use request issued only within a live SCM callback."""

    __slots__ = ("phase", "plan", "receipt", "approval", "authorization_ref", "project_id", "profile_sha256",
                 "profile_source_sha256", "runtime", "authority", "provider", "_seal", "_used", "_use_lock")

    def __init__(self, *, phase: str, plan: Any, receipt: Any, approval: Any,
                 authorization_ref: str, project_id: str, profile_sha256: str,
                 profile_source_sha256: str, runtime: ScmServiceRuntime,
                 authority: Any, provider: Any,
                 seal: object):
        if seal is not _HOST_CONTROL_REQUEST_SEAL:
            _fail("HOST_CONTROL_REQUEST_SEAL_REQUIRED", "internal host-control request cannot be constructed by wire input")
        self.phase, self.plan, self.receipt, self.approval = phase, plan, receipt, approval
        self.authorization_ref = authorization_ref
        self.project_id, self.profile_sha256, self.runtime = project_id, profile_sha256, runtime
        self.profile_source_sha256 = profile_source_sha256
        self.authority, self.provider = authority, provider
        self._seal = seal
        self._used = False
        self._use_lock = threading.Lock()

    def __setattr__(self, name: str, value: Any) -> None:
        if hasattr(self, name):
            raise AttributeError("internal host-control request is immutable")
        object.__setattr__(self, name, value)

    def consume(self, *, service_name: str, project_id: str, profile_sha256: str) -> None:
        with self._use_lock:
            if self._seal is not _HOST_CONTROL_REQUEST_SEAL or self._used:
                _fail("HOST_CONTROL_REQUEST_REPLAYED", "internal host-control request is forged or already consumed")
            if (not self.runtime.is_active or self.runtime._seal is not _SCM_RUNTIME_SEAL
                    or self.runtime.service_name != service_name
                    or self.project_id != project_id or self.profile_sha256 != profile_sha256):
                _fail("HOST_CONTROL_REQUEST_BINDING_MISMATCH", "internal host-control request is stale or bound to another service/profile")
            object.__setattr__(self, "_used", True)


class WindowsT037HostControlIssuer:
    """Issue a bounded internal route capability from trusted host state.

    This capability only opens the in-process ControlDaemon route. The
    WindowsHostChangeExecutor still revalidates the exact profile, project
    host_control grant, scoped provider grant and one-use SQLite admission
    immediately before any host mutation.
    """

    def __init__(self, *, authority: Any, provider: Any, profile: Any,
                 profile_source_sha256: str):
        from ._host_scoped_provider import HostScopedProvider
        from ._project_authority import ProjectAuthority
        from ._w26_os_resources import HostResourceProfile

        if (type(authority) is not ProjectAuthority or type(provider) is not HostScopedProvider
                or authority.authorization_verifier is not provider
                or type(profile) is not HostResourceProfile):
            _fail("HOST_CONTROL_ISSUER_TRUSTED_COMPONENT_REQUIRED",
                  "issuer requires the existing project authority, pinned provider and exact host profile")
        if profile.platform != "windows" or profile.windows_isolation is None:
            _fail("HOST_CONTROL_WINDOWS_PROFILE_REQUIRED", "issuer requires the exact Windows isolation profile")
        if (not isinstance(profile_source_sha256, str)
                or not re.fullmatch(r"[0-9a-f]{64}", profile_source_sha256)):
            _fail("HOST_CONTROL_PROFILE_BINDING_INVALID", "issuer requires the exact trusted startup profile digest")
        self.authority, self.provider, self.profile = authority, provider, profile
        self.profile_source_sha256 = profile_source_sha256

    def issue(self, runtime: ScmServiceRuntime, *, phase: str, plan: Any,
              authorization_ref: str, receipt: Any = None) -> InternalHostControlRequest:
        from . import _w26_os_resources as resources
        from ._windows_host_change import _normalize_supported_actions

        policy = self.profile.windows_isolation
        if (type(runtime) is not ScmServiceRuntime or runtime._seal is not _SCM_RUNTIME_SEAL
                or not runtime.is_active or runtime.service_name != policy.service_name):
            _fail("HOST_CONTROL_SCM_RUNTIME_REQUIRED",
                  "host-control capabilities require the active configured SCM service runtime")
        if type(plan) is not resources.HostChangePlan:
            _fail("HOST_CONTROL_PLAN_REQUIRED", "host-control route requires the typed immutable host-change plan")
        try:
            payload = resources.validate_host_change_plan(plan)
            if (payload["project_id"] != self.profile.project_id
                    or payload["profile_sha256"] != self.profile.sha256
                    or payload["profile"] != self.profile.as_dict()
                    or payload["host"] != {"platform": "windows", "host_id": self.profile.host_id}):
                _fail("HOST_CONTROL_SCOPE_MISMATCH", "host-change plan does not bind this exact profile, host and project")
            _normalize_supported_actions(plan, self.profile)
        except resources.ResourcePolicyError:
            raise
        except WindowsScmError:
            raise
        except Exception as exc:
            _fail("HOST_CONTROL_PLAN_INVALID", "host-change plan validation failed",
                  details={"cause_type": type(exc).__name__})
        try:
            self.authority.authorize_operation(self.profile.project_id, "host_control")
        except ExecutionContractError:
            raise
        authorization_ref = _authorization_ref(authorization_ref)
        if phase == "APPLY":
            if receipt is not None:
                _fail("HOST_CONTROL_REQUEST_INVALID", "apply request cannot carry a restore receipt")
            scope = {"root": self.profile.root.as_dict(), "host_id": self.profile.host_id,
                     "profile_sha256": self.profile.sha256, "plan_sha256": plan.sha256,
                     "phase": "APPLY", "source_plan_sha256": None,
                     "source_receipt_sha256": None}
            if self.provider.verify_scope(self.profile.project_id, "host.change.apply",
                                          scope, authorization_ref) is not True:
                _fail("HOST_CONTROL_AUTHORIZATION_REQUIRED",
                      "trusted scoped provider did not authorize this exact apply plan")
            approval = resources.HostChangeApproval(plan.sha256, authorization_ref)
        elif phase == "RESTORE":
            if (type(receipt) is not resources.AppliedHostChangeReceipt
                    or receipt.plan_sha256 != plan.sha256 or receipt.host_id != self.profile.host_id
                    or receipt.platform != "windows"):
                _fail("HOST_CONTROL_RECEIPT_MISMATCH", "restore route requires the exact typed apply receipt")
            # The restore plan hash includes a fresh native dependency snapshot
            # and is therefore generated/authorized inside the executor's
            # transactional admission. The internal executor accepts the raw
            # reference only through this sealed request and binds it after the
            # fresh snapshot; no caller-computed hash is accepted.
            approval = None
        else:
            _fail("HOST_CONTROL_PHASE_UNSUPPORTED", "host-control phase must be APPLY or RESTORE")
        return InternalHostControlRequest(
            phase=phase, plan=plan, receipt=receipt, approval=approval,
            authorization_ref=authorization_ref,
            project_id=self.profile.project_id, profile_sha256=self.profile.sha256,
            profile_source_sha256=self.profile_source_sha256,
            runtime=runtime, authority=self.authority, provider=self.provider,
            seal=_HOST_CONTROL_REQUEST_SEAL,
        )


__all__ = [
    "CtypesWindowsScmApi", "HANDLER_FUNCTION_EX", "InternalHostControlRequest",
    "SERVICE_MAIN_FUNCTIONW", "SERVICE_STATUS", "SERVICE_TABLE_ENTRYW",
    "ScmServiceRuntime", "WindowsScmError", "WindowsScmServiceBroker",
    "WindowsT037HostControlIssuer", "scm_abi_layout",
]
