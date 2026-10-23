"""Original userenv CREATE output and FreeSid ownership; no DELETE export."""
from __future__ import annotations
import ctypes as C
from ._windows_owned_resources import registry_for
from ._windows_host_preflight_owner import scalar
from ._windows_service_sid import fail

H = C.c_void_p
D = C.c_uint32


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
