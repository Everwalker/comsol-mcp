"""Original userenv CREATE and DELETE call owners; no postimage claims."""
from __future__ import annotations
import ctypes as C
import threading
from ._windows_owned_resources import registry_for
from ._windows_host_preflight_owner import scalar
from ._windows_service_sid import fail

H = C.c_void_p
D = C.c_uint32
_DELETE_ATTEMPT_SEAL = object()


class SidAndAttributes(C.Structure):
    _fields_ = [('sid', H), ('attributes', D)]


class _NativeCreateAttempt:
    """Local control-flow witness for whether the exact SDK call was entered."""
    __slots__ = ("_entered", "_finished")

    def __init__(self):
        self._entered = False
        self._finished = False

    @property
    def entered(self):
        return self._entered

    @property
    def finished(self):
        return self._finished

    def mark_entered(self):
        if self._entered or self._finished:
            fail('HOST_CHANGE_APPCONTAINER_OWNER_UNKNOWN',
                 'the original native CREATE attempt entered more than once')
        self._entered = True

    def mark_finished(self):
        if self._finished:
            fail('HOST_CHANGE_APPCONTAINER_OWNER_UNKNOWN',
                 'the original native CREATE attempt completed more than once')
        self._finished = True


class _NativeDeleteAttempt:
    """Captured exact Delete function/argument and one-entry state machine."""
    __slots__ = ("_seal", "effect", "api", "original_call", "userenv", "function",
                 "name_buffer", "arguments", "state", "hresult", "_entered",
                 "_finished", "_reserved", "_state_lock")

    def __init__(self, *, effect, api, original_call, userenv, function,
                 name_buffer, arguments, seal):
        if seal is not _DELETE_ATTEMPT_SEAL:
            fail("HOST_CHANGE_APPCONTAINER_DELETE_OWNER_UNKNOWN",
                 "Delete attempt can only be captured by the original native adapter")
        self._seal = seal
        self.effect, self.api, self.original_call = effect, api, original_call
        self.userenv, self.function = userenv, function
        self.name_buffer, self.arguments = name_buffer, arguments
        self.state, self.hresult = "PRE_SDK", None
        self._entered, self._finished = False, False
        self._reserved = False
        self._state_lock = threading.Lock()

    @property
    def entered(self):
        return self._entered

    @property
    def finished(self):
        return self._finished

    def reserve_entry(self):
        """Reserve the sole SDK entry while the terminal runtime fence is locked."""
        with self._state_lock:
            if (self._reserved or self._entered or self._finished
                    or self.state != "PRE_SDK"):
                fail("HOST_CHANGE_APPCONTAINER_DELETE_REPLAY_REFUSED",
                     "the original Delete attempt already has an entry reservation")
            self._reserved = True
            self.state = "RESERVED"

    def claim_preparation(self):
        """Consume the reservation before external preflight, without holding runtime locks."""
        with self._state_lock:
            if (not self._reserved or self._entered or self._finished
                    or self.state != "RESERVED"):
                fail("HOST_CHANGE_APPCONTAINER_DELETE_REPLAY_REFUSED",
                     "the original Delete attempt has no available entry reservation")
            self.state = "PREPARING"

    def enter(self):
        with self._state_lock:
            if (not self._reserved or self._entered or self._finished
                    or self.state != "PREPARING"):
                fail("HOST_CHANGE_APPCONTAINER_DELETE_REPLAY_REFUSED",
                     "the original Delete attempt can enter the SDK at most once")
            self._entered = True
            self.state = "INVOKING"

    def capture_return(self, value):
        with self._state_lock:
            if (not self._entered or self._finished or self.state != "INVOKING"
                    or type(value) is not int or not -(2**31) <= value < 2**31
                    or self.hresult is not None):
                self.state = "UNKNOWN"
                fail("HOST_CHANGE_APPCONTAINER_DELETE_RETURN_UNKNOWN",
                     "Delete did not return one exact signed 32-bit HRESULT")
            self.hresult = value
            self.state = "API_CONFIRMED" if value == 0 else "UNKNOWN"

    def mark_failed(self):
        with self._state_lock:
            if self._finished:
                return
            if self.state in {"RESERVED", "PREPARING", "INVOKING"}:
                self.state = "UNKNOWN"
            self._finished = True

    def mark_finished(self):
        with self._state_lock:
            if self._finished:
                fail("HOST_CHANGE_APPCONTAINER_DELETE_OWNER_UNKNOWN",
                     "the original Delete attempt finished more than once")
            if self.state in {"RESERVED", "PREPARING", "INVOKING"}:
                self.state = "UNKNOWN"
            self._finished = True


def sdk(api):
    calls = getattr(api, '_captured_mutation_sdk', None)
    if calls is None:
        calls = {}; api._captured_mutation_sdk = calls
    bindings = {
        'CreateAppContainerProfile': ('userenv', [C.c_wchar_p, C.c_wchar_p, C.c_wchar_p,
            C.POINTER(SidAndAttributes), D, C.POINTER(H)], C.c_int32),
        'FreeSid': ('advapi', [H], H), 'IsValidSid': ('advapi', [H], C.c_int32),
        'GetLengthSid': ('advapi', [H], D),
        'OpenThreadToken': ('advapi', [H, D, C.c_int32, C.POINTER(H)], C.c_int32),
        'GetCurrentThread': ('kernel', [], H), 'GetLastError': ('kernel', [], D),
        'CloseHandle': ('kernel', [H], C.c_int32)}
    for name, (family, arguments, result) in bindings.items():
        if name not in calls:
            if family == 'userenv':
                if not hasattr(api, '_appcontainer_userenv'):
                    api._appcontainer_userenv = C.WinDLL('userenv', use_last_error=True)
                dll = api._appcontainer_userenv
            else:
                dll = api._advapi if family == 'advapi' else api._kernel32
            fn = getattr(dll, name); fn.argtypes = arguments; fn.restype = result
            calls[name] = fn
    return calls


def no_impersonation(api):
    calls = sdk(api)
    r = registry_for(api)
    token = H(); entry = None; result = None; primary = None
    try:
        try:
            result = calls['OpenThreadToken'](calls['GetCurrentThread'](), 0x8, 1, C.byref(token))
            error = scalar(calls['GetLastError'](), 'DWORD')
        finally:
            if token.value is not None:
                address = token.value; closer = calls['CloseHandle']
                def release(actual):
                    if (entry is None or entry.adapter is not api or r.owner is not api
                            or r._entries.get(entry.seq) is not entry or actual != address):
                        fail('HOST_CHANGE_APPCONTAINER_TOKEN_UNKNOWN', 'original thread token owner changed')
                    if scalar(closer(H(actual)), 'BOOL') == 0:
                        fail('HOST_CHANGE_APPCONTAINER_TOKEN_UNKNOWN', 'original thread token close unconfirmed')
                entry = r.acquire(api, token, release, mode='VOID', domain='appcontainer_thread_token',
                    label='appcontainer_thread_token', keepalive=(token, calls), value_reader=lambda original: original.value)
        if scalar(result, 'BOOL') != 0 or error != 1008 or token.value is not None:
            fail('HOST_CHANGE_APPCONTAINER_IMPERSONATION', 'AppContainer CREATE requires the configured process user without thread impersonation')
    except BaseException as error:
        primary = error
        if getattr(api, '_first_mutation_cause', None) is None: api._first_mutation_cause = error
        raise
    finally:
        if entry is not None:
            try: entry.release_owned(api)
            except BaseException as error:
                if primary is not None: error._first_cause = primary
                raise


def create(api, policy, expected_subject):
    from ._windows_appcontainer_lifecycle import ProfileEffect
    effect = getattr(api, '_appcontainer_profile_effect', None)
    if type(effect) is not ProfileEffect:
        fail('HOST_CHANGE_APPCONTAINER_OWNER_REQUIRED',
             'the exact daemon-rooted ProfileEffect is required before native CREATE')
    effect.require_rooted(api)
    if (effect.policy is not policy or effect.profile_name != policy.appcontainer_profile_name
            or effect.appcontainer_sid.casefold() != policy.appcontainer_sid.casefold()
            or effect.subject is not expected_subject):
        fail('HOST_CHANGE_APPCONTAINER_OWNER_BINDING_MISMATCH',
             'the original CREATE arguments differ from the retained profile effect')
    if effect.software_only:
        from ._windows_host_change import _SOFTWARE_TEST_TOKEN
        if (not effect.executor.software_only
                or effect.executor._software_test_seal is not _SOFTWARE_TEST_TOKEN):
            fail('HOST_CHANGE_APPCONTAINER_TEST_OWNER_REQUIRED',
                 'synthetic AppContainer calls require the explicit SOFTWARE_ONLY executor seal')
    else:
        from ._windows_host_change import _TrustedProfileLoadOrigin, _NATIVE_PROFILE_LOADER_TOKEN
        origin = effect.loader_origin
        if (effect.executor.software_only is not False
                or effect.executor._software_test_seal is not None
                or type(origin) is not _TrustedProfileLoadOrigin
                or origin._seal is not _NATIVE_PROFILE_LOADER_TOKEN
                or origin is not effect.executor._trusted_profile_load_origin
                or effect.profile_wrapper is not origin.profile_wrapper
                or effect.daemon_profile_wrapper is not effect.daemon._windows_t037_profile
                or effect.profile is not effect.executor.profile
                or effect.profile is not effect.profile_wrapper.host_resource_profile
                or effect.policy is not effect.profile.windows_isolation
                or not origin.matches(effect.profile_wrapper, effect.profile_path)):
            fail('HOST_CHANGE_APPCONTAINER_OWNER_BINDING_MISMATCH',
                 'the native effect lost its exact fresh-loader profile/origin or daemon source binding')
    attempt = _NativeCreateAttempt()
    effect.bind_native_attempt(api, attempt)
    try:
        return _create_original(api, policy, expected_subject, effect, attempt)
    finally:
        attempt.mark_finished()


def capture_delete_attempt(api, effect):
    """Capture one original Userenv export and its exact name buffer, without calling it."""
    from ._windows_appcontainer_lifecycle import ProfileEffect
    if type(effect) is not ProfileEffect:
        fail("HOST_CHANGE_APPCONTAINER_OWNER_REQUIRED",
             "the exact original ProfileEffect is required before Delete")
    effect.require_rooted(api)
    original_call = effect._original_call
    calls = original_call[0] if type(original_call) is tuple and original_call else None
    userenv = getattr(api, "_appcontainer_userenv", None)
    if (original_call is not api._appcontainer_original_call
            or calls is not getattr(api, "_captured_mutation_sdk", None)
            or effect._original_userenv is None or userenv is not effect._original_userenv
            or not isinstance(calls, dict)
            or not callable(calls.get("CreateAppContainerProfile"))
            or not callable(calls.get("FreeSid"))
            or effect._hresult != 0 or not effect._create_entered
            or effect._sid_text is None
            or effect._sid_text.casefold() != effect.appcontainer_sid.casefold()
            or not original_call[5]() or not original_call[6]()):
        fail("HOST_CHANGE_APPCONTAINER_DELETE_OWNER_UNKNOWN",
             "the original successful CREATE/API/userenv owner is not retained")
    function = getattr(userenv, "DeleteAppContainerProfile", None)
    if not callable(function):
        fail("HOST_CHANGE_APPCONTAINER_DELETE_API_UNAVAILABLE",
             "the original Userenv library has no callable DeleteAppContainerProfile export")
    try:
        function.argtypes = [C.c_wchar_p]
        function.restype = C.c_int32
    except (AttributeError, TypeError, ValueError) as exc:
        fail("HOST_CHANGE_APPCONTAINER_DELETE_API_INVALID",
             "the captured Delete export cannot be bound to HRESULT(PCWSTR)")
    prior = calls.get("DeleteAppContainerProfile")
    if prior is not None and prior is not function:
        fail("HOST_CHANGE_APPCONTAINER_DELETE_OWNER_UNKNOWN",
             "the captured original Delete export changed")
    calls["DeleteAppContainerProfile"] = function
    name = effect.profile_name
    try:
        encoded = name.encode("ascii")
    except (AttributeError, UnicodeEncodeError):
        fail("HOST_CHANGE_APPCONTAINER_DELETE_NAME_INVALID",
             "original profile name is not the validated ASCII identifier")
    if not encoded or "\x00" in name or len(encoded) > 64:
        fail("HOST_CHANGE_APPCONTAINER_DELETE_NAME_INVALID",
             "original profile name is outside the documented bounded contract")
    buffer = C.create_unicode_buffer(name)
    if buffer.value != name:
        fail("HOST_CHANGE_APPCONTAINER_DELETE_NAME_INVALID",
             "captured wide-character profile name differs from original CREATE")
    return _NativeDeleteAttempt(
        effect=effect, api=api, original_call=original_call, userenv=userenv,
        function=function, name_buffer=buffer, arguments=(buffer,),
        seal=_DELETE_ATTEMPT_SEAL,
    )


def delete_appcontainer_profile(effect, attempt):
    """Enter the original Userenv Delete once; S_OK confirms only its API return."""
    from ._windows_appcontainer_lifecycle import ProfileEffect
    if (type(effect) is not ProfileEffect or type(attempt) is not _NativeDeleteAttempt
            or attempt._seal is not _DELETE_ATTEMPT_SEAL
            or attempt.effect is not effect or attempt.api is not effect.api
            or attempt.original_call is not effect._original_call
            or effect.api._appcontainer_original_call is not effect._original_call
            or getattr(effect.api, "_appcontainer_userenv", None) is not attempt.userenv
            or effect._original_userenv is not attempt.userenv
            or getattr(effect.api, "_captured_mutation_sdk", None) is not effect._original_call[0]
            or effect._original_call[0].get("DeleteAppContainerProfile") is not attempt.function
            or getattr(attempt.userenv, "DeleteAppContainerProfile", None) is not attempt.function
            or attempt.arguments != (attempt.name_buffer,)
            or attempt.name_buffer.value != effect.profile_name
            or effect._hresult != 0 or effect._sid_text is None
            or effect._sid_text.casefold() != effect.appcontainer_sid.casefold()):
        fail("HOST_CHANGE_APPCONTAINER_DELETE_OWNER_UNKNOWN",
             "captured Delete API/function/name buffer no longer matches the original effect")
    effect.require_rooted(effect.api)
    registry = registry_for(effect.api)
    from ._windows_service_sid import original_preflights_terminal
    if (registry.owner is not effect.api or registry.unresolved()
            or not original_preflights_terminal(effect.api)
            or not effect._original_call[5]() or not effect._original_call[6]()):
        fail("HOST_CHANGE_APPCONTAINER_DELETE_RESOURCES_UNRESOLVED",
             "original CREATE allocations or SDK cleanup are not terminal before Delete")
    attempt.claim_preparation()
    # This final observation uses the same captured process-user adapter and
    # occurs immediately before the irreversible call. It is outside the
    # runtime lock and does not retire the AppContainer lifetime Gate.
    no_impersonation(effect.api)
    attempt.enter()
    try:
        raw = attempt.function(*attempt.arguments)
        attempt.capture_return(raw)
        if attempt.state != "API_CONFIRMED":
            fail("HOST_CHANGE_APPCONTAINER_DELETE_HRESULT_NOT_SUCCESS",
                 "Delete returned a nonzero HRESULT; native outcome is UNKNOWN")
        return attempt.hresult
    except BaseException:
        attempt.mark_failed()
        raise
    finally:
        if not attempt.finished:
            attempt.mark_finished()


def _create_original(api, policy, expected_subject, effect, attempt):
    calls = sdk(api); r = registry_for(api)
    previous = getattr(api, '_appcontainer_original_call', None)
    if r.unresolved() or previous is not None:
        fail('HOST_CHANGE_APPCONTAINER_OWNER_UNKNOWN', 'original native resources require cleanup before CREATE')
    if api._service_sid_observe(policy.service_name) != expected_subject:
        fail('HOST_CHANGE_APPCONTAINER_SUBJECT_CHANGED', 'original active service subject changed before CREATE')
    no_impersonation(api)
    from ._windows_wfp_mutation_owner import release_allocation
    caps = (SidAndAttributes * len(policy.capability_sids))()
    allocations = []; output = H(); result = None; entry = None; first = None
    allocation_confirmed = False
    create_succeeded = False; sid_released = False; capability_terminals = []
    state = {'allocation_confirmed': False, 'address': None, 'result': None, 'outcome': 'UNKNOWN'}
    # Exact SDK call buffers and outcomes remain with the original adapter if
    # return/pointer/cleanup is unknown. They grant no OS profile generation.
    def terminal():
        # Actual SDK/closer facts live in the original call closure. Public
        # diagnostic state cannot classify a missing HRESULT or confirm frees.
        return (api._appcontainer_original_call is original_call
                and (not attempt.entered or (create_succeeded and allocation_confirmed and sid_released))
                and all(confirmed() for confirmed in capability_terminals))
    def entered():
        return attempt.entered
    original_call = (calls, caps, output, state, expected_subject, terminal, entered)
    api._appcontainer_original_call = original_call
    effect.bind_original_call(api, original_call)
    def retain_capability_closer(address):
        original_entry = r.find(api, address, domain='mutation_local')
        if original_entry is None:
            fail('HOST_CHANGE_APPCONTAINER_CAPABILITY_UNKNOWN', 'original capability allocation owner missing')
        original_closer = original_entry.closer
        confirmed = False
        def close(actual):
            nonlocal confirmed
            result = original_closer(actual)
            if result is None:
                confirmed = True
            return result
        original_entry.closer = close
        capability_terminals.append(lambda: confirmed)
    try:
        for index, sid in enumerate(policy.capability_sids):
            allocation = H()
            try:
                ok = api._sdk_alloc('ConvertStringSidToSidW', allocation, sid, C.byref(allocation), local=True, bool_return=True)
            finally:
                if allocation.value:
                    allocations.append(allocation.value)
                    retain_capability_closer(allocation.value)
            if not ok or not allocation.value:
                fail('HOST_CHANGE_APPCONTAINER_CAPABILITY_UNKNOWN', 'original capability SID allocation failed')
            caps[index] = SidAndAttributes(allocation.value, 4)
        try:
            expected_capability_entries = {
                id(r.find(api, address, domain='mutation_local')) for address in allocations
            }
            unresolved = r.unresolved()
            if (None in expected_capability_entries or len(unresolved) != len(expected_capability_entries)
                    or any(id(item) not in expected_capability_entries
                           or item.adapter is not api or item.domain != 'mutation_local'
                           for item in unresolved)):
                fail('HOST_CHANGE_APPCONTAINER_OWNER_UNKNOWN',
                     'only the exact original capability buffers may remain owned before CREATE')
            no_impersonation(api)
            attempt.mark_entered()
            effect.mark_create_entered(api, original_call)
            result = calls['CreateAppContainerProfile'](policy.appcontainer_profile_name,
                policy.appcontainer_profile_name, policy.appcontainer_profile_name,
                caps if len(caps) else None, len(caps), C.byref(output))
            raw = getattr(result, 'value', result)
            if type(raw) is not int or not -(2**31) <= raw < 2**31:
                fail('HOST_CHANGE_APPCONTAINER_HRESULT_UNKNOWN', 'original HRESULT return is not signed 32-bit')
            effect.capture_hresult(api, original_call, raw)
            create_succeeded = raw == 0
            state['result'] = raw
            state['allocation_confirmed'] = False
        finally:
            if output.value is not None:
                sid_address = output.value
                state['address'] = sid_address
                closer = calls['FreeSid']
                def release(actual):
                    nonlocal sid_released
                    if (not allocation_confirmed or entry is None or r.owner is not api
                            or entry.adapter is not api or r._entries.get(entry.seq) is not entry
                            or type(actual) is not int or actual != sid_address or actual <= 0
                            or actual == H(-1).value):
                        fail('HOST_CHANGE_APPCONTAINER_SID_UNKNOWN', 'ambiguous or foreign SID cannot authorize FreeSid')
                    returned_result = closer(H(actual))
                    raw = getattr(returned_result, 'value', returned_result)
                    # ctypes PVOID NULL is None; preserve direct test integer returns.
                    if raw is None:
                        returned = None
                    else: returned = raw
                    if returned is not None:
                        fail('HOST_CHANGE_APPCONTAINER_FREE_UNKNOWN', 'original FreeSid failed')
                    sid_released = True
                entry = r.acquire(api, output, release, mode='VOID', domain='appcontainer_sid',
                    label='appcontainer_sid', keepalive=(output, caps, calls, state), value_reader=lambda original: original.value)
        if state['result'] != 0:
            fail('HOST_CHANGE_APPCONTAINER_ALREADY_EXISTS' if state['result'] == -2147024713 else 'HOST_CHANGE_APPCONTAINER_CREATE_UNKNOWN',
                 'CREATE did not establish a new original profile; no delete/retry authorized')
        if entry is None or scalar(calls['IsValidSid'](H(entry.value)), 'BOOL') == 0:
            fail('HOST_CHANGE_APPCONTAINER_SID_UNKNOWN', 'original CREATE returned no valid SID')
        length = scalar(calls['GetLengthSid'](H(entry.value)), 'DWORD')
        if not 8 <= length <= 68:
            fail('HOST_CHANGE_APPCONTAINER_SID_UNKNOWN', 'original SID extent outside documented SID bounds')
        raw = C.string_at(entry.value, length)
        if raw[0] != 1 or raw[1] > 15 or length != 8 + 4 * raw[1]:
            fail('HOST_CHANGE_APPCONTAINER_SID_UNKNOWN', 'original SID length/revision mismatch')
        text = 'S-1-' + str(int.from_bytes(raw[2:8], 'big')) + ''.join('-' + str(int.from_bytes(raw[i:i+4], 'little')) for i in range(8, length, 4))
        allocation_confirmed = True
        state['allocation_confirmed'] = True
        effect.capture_validated_sid(api, original_call, raw, text)
        if text != policy.appcontainer_sid:
            fail('HOST_CHANGE_APPCONTAINER_SID_MISMATCH', 'CREATE returned a different AppContainer SID')
        return {'profile_name': policy.appcontainer_profile_name, 'appcontainer_sid': text,
                'service_user_sid': policy.service_logon_sid, 'creation_result': 'S_OK',
                'capabilities_state': 'NOT_READ_BACK', 'generation_state': 'UNAVAILABLE'}
    except BaseException as error:
        first = error
        if getattr(api, '_first_mutation_cause', None) is None: api._first_mutation_cause = error
        raise
    finally:
        failures = []
        if entry is not None:
            try: entry.release_owned(api)
            except BaseException as error: failures.append(error)
        for capability_address in reversed(allocations):
            try: release_allocation(api, capability_address, local=True)
            except BaseException as error: failures.append(error)
        if failures:
            failures[0]._first_cause = first
            raise failures[0]
        if first is None:
            state['outcome'] = 'COMPLETED'
