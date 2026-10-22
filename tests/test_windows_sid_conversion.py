"""SID grammar and exact LocalFree ownership controls; software ABI doubles only."""
import ctypes as C
from ctypes import wintypes as W
from types import SimpleNamespace

import pytest

from comsol_mcp import _windows_t037 as t037
from comsol_mcp import _windows_t037_native as native
from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._windows_owned_resources import NativeResourceError, registry_for


def profile(sid):
    return t037.parse_profile_document({
        "schema": t037.PROFILE_SCHEMA, "profile_id": "sid-input-control",
        "host_fingerprint_sha256": "a" * 64, "service_account_sid": sid,
        "interactive_account_sid": "S-1-5-21-100-200-300-1001",
        "appcontainer_sid": "S-1-15-2-123", "capability_sids": [],
        "server_image_sha256": "b" * 64, "worker_image_sha256": "c" * 64,
        "workspace_root": "C:\\new-project", "require_hard_quota": False,
    }, source_sha256="d" * 64)


@pytest.mark.parametrize("sid", [
    "S-1-1-0", "S-1-5-18", "S-1-5-19", "S-1-5-20", "S-1-16-12288",
    "S-1-5-32-544", "S-1-4294967295-4294967295",
    "S-1-0x000100000000-0", "S-1-0xFFFFFFFFFFFF-4294967295",
    "S-1-5" + "-4294967295" * 15,
])
def test_canonical_sid_intent_accepts_one_to_fifteen_subauthorities(sid):
    parsed = profile(sid)
    assert parsed.service_account_sid == sid
    assert parsed.as_safe_dict()["verification_state"] == "NOT_VERIFIED"


@pytest.mark.parametrize("sid", [
    None, 19, "S-1-5", "S-2-5-19", "S-1-05-19", "S-1-5-019", "S-1-5--19",
    "S-1-5-4294967296", "S-1-4294967296-0", "S-1-0x1000000000000-0",
    "S-1-0x000000000005-19", "S-1-5-19\x00", " S-1-5-19", "S-1-5-19\n",
    "ſ-1-5-19", "S-1-5-١٩",
    "S-1-5" + "-1" * 16,
])
def test_sid_intent_rejects_malformed_noncanonical_or_out_of_range_values(sid):
    with pytest.raises(ExecutionContractError):
        profile(sid)


def software_api(text, *, policy=False, invalid_pointer=False, conversion_failure=False, free_failure=False):
    storage = C.create_unicode_buffer(text)
    address = C.addressof(storage)
    events = []

    def convert(sid, output):
        events.append(("convert", sid))
        if conversion_failure:
            return 0
        C.cast(output, C.POINTER(C.c_wchar_p))[0] = C.cast(storage, C.c_wchar_p)
        return 1

    def free(pointer):
        actual = C.cast(pointer, C.c_void_p).value
        events.append(("LocalFree", actual))
        assert actual == address
        if free_failure:
            return actual
        C.memset(storage, 0, C.sizeof(storage))
        return None

    def wrong_close(pointer):
        raise AssertionError("SID text must never be released through CloseHandle")

    a = SimpleNamespace(IsValidSid=lambda _: not invalid_pointer, GetLengthSid=lambda _: 12,
                        ConvertSidToStringSidW=convert)
    k = SimpleNamespace(LocalFree=free, CloseHandle=wrong_close)
    api = object.__new__(native.CtypesWindowsPolicyApi if policy else t037._Win32ReadOnlyApi)
    if policy:
        api._advapi, api._kernel32 = a, k
    else:
        api.C = SimpleNamespace(c_void_p=C.c_void_p, cast=C.cast, byref=C.byref, get_last_error=lambda: 87)
        api.W, api.a, api.k = W, a, k
    return api, events, storage, address


@pytest.mark.parametrize("policy", [False, True])
@pytest.mark.parametrize("sid", ["S-1-1-0", "S-1-5-19", "S-1-16-12288"])
def test_actual_sid_conversion_result_copied_before_exact_localfree(policy, sid):
    api, events, storage, address = software_api(sid, policy=policy)
    value = api._sid_string(C.c_void_p(77)) if policy else api._sid_text(C.c_void_p(77))
    assert value == sid
    assert storage.value == ""
    assert [e for e in events if e[0] == "LocalFree"] == [("LocalFree", address)]
    assert registry_for(api).unresolved() == ()


@pytest.mark.parametrize("policy", [False, True])
def test_invalid_converted_text_refuses_after_releasing_original_buffer(policy):
    api, events, storage, address = software_api("not-a-sid", policy=policy)
    with pytest.raises(ExecutionContractError):
        api._sid_string(C.c_void_p(77)) if policy else api._sid_text(C.c_void_p(77))
    assert [e for e in events if e[0] == "LocalFree"] == [("LocalFree", address)]
    assert registry_for(api).unresolved() == ()


def test_failed_conversion_never_frees_an_unreturned_allocation():
    api, events, _, _ = software_api("S-1-5-19", conversion_failure=True)
    with pytest.raises(ExecutionContractError):
        api._sid_text(C.c_void_p(77))
    assert not any(e[0] == "LocalFree" for e in events)
    assert registry_for(api).unresolved() == ()


def test_invalid_native_sid_pointer_never_converts_or_frees():
    api, events, _, _ = software_api("S-1-5-19", invalid_pointer=True)
    with pytest.raises(ExecutionContractError):
        api._sid_text(C.c_void_p(77))
    assert events == []


@pytest.mark.parametrize("policy", [False, True])
def test_localfree_failure_retains_exact_original_owner_and_does_not_retry(policy):
    api, events, _, address = software_api("S-1-5-19", policy=policy, free_failure=True)
    with pytest.raises(NativeResourceError):
        api._sid_string(C.c_void_p(77)) if policy else api._sid_text(C.c_void_p(77))
    registry = registry_for(api)
    entry, = registry.unresolved()
    assert entry.value == address and entry.state == "UNKNOWN"
    with pytest.raises(NativeResourceError):
        entry.release_owned(registry.owner)
    assert [e for e in events if e[0] == "LocalFree"] == [("LocalFree", address)]


@pytest.mark.parametrize("scm_name", ["LocalSystem", ".\\LocalSystem", "localsystem"])
def test_scm_system_label_resolves_through_real_account_lookup_not_sid_fallback(monkeypatch, scm_name):
    api, events, _, address = software_api("S-1-5-18", policy=True)
    lookups = []
    def lookup(host, name, sid, sid_size, domain, domain_size, use):
        lookups.append((host, name))
        C.cast(sid_size, C.POINTER(native.DWORD)).contents.value = 12
        C.cast(domain_size, C.POINTER(native.DWORD)).contents.value = 1
        return int(sid is not None)
    api._advapi.LookupAccountNameW = lookup
    monkeypatch.setattr(C, "get_last_error", lambda: 122, raising=False)
    assert api._lookup_sid(scm_name) == "S-1-5-18"
    assert lookups == [(None, r"NT AUTHORITY\SYSTEM")] * 2
    assert [e for e in events if e[0] == "LocalFree"] == [("LocalFree", address)]


def test_unresolved_account_never_substitutes_a_well_known_sid(monkeypatch):
    api, events, _, _ = software_api("S-1-5-18", policy=True)
    api._advapi.LookupAccountNameW = lambda *args: 0
    monkeypatch.setattr(C, "get_last_error", lambda: 1332, raising=False)
    with pytest.raises(ExecutionContractError):
        api._lookup_sid("NoSuchAccount")
    assert events == []
    assert registry_for(api).unresolved() == ()


@pytest.mark.parametrize("scm_name,lookup_name,rid", [
    ("LocalSystem", r"NT AUTHORITY\SYSTEM", 18),
    (r".\LocalSystem", r"NT AUTHORITY\SYSTEM", 18),
    ("localsystem", r"NT AUTHORITY\SYSTEM", 18),
    (r"NT AUTHORITY\LocalService", r"NT AUTHORITY\LocalService", 19),
    (r"NT AUTHORITY\NetworkService", r"NT AUTHORITY\NetworkService", 20),
])
def test_service_sid_leaf_subject_normalizes_SCM_label_before_actual_lookup(monkeypatch, scm_name, lookup_name, rid):
    """Full production read-only subject path, against a software SDK double."""
    from comsol_mcp import _windows_service_sid_native as scm
    from tests.test_windows_service_sid_native import SDK, actual
    from tests.test_windows_host_preflight_owner import Function

    sdk = SDK()
    sdk.account = scm_name
    calls = []
    original = sdk.lookup

    def lookup(host, name, sid, size, domain, domain_size, use):
        calls.append(name)
        if name.startswith("NT SERVICE\\"):
            return original(host, name, sid, size, domain, domain_size, use)
        if name != lookup_name:
            return 0  # SDK does not recognize SCM's LocalSystem display label.
        raw = bytes((1, 1, 0, 0, 0, 0, 0, 5)) + rid.to_bytes(4, "little")
        C.cast(size, C.POINTER(C.c_uint32))[0] = len(raw)
        C.cast(domain_size, C.POINTER(C.c_uint32))[0] = 1
        if sid is None:
            return 0
        C.memmove(sid, raw, len(raw))
        domain.value = ""
        return 1

    sdk.a.LookupAccountNameW = Function(lookup)
    adapter = actual(monkeypatch, sdk)
    observed = scm.observe(adapter, "SoftwareFixtureService")
    assert observed["service_start_name"] == scm_name
    assert observed["service_start_sid"] == "S-1-5-" + str(rid)
    assert calls[:2] == [lookup_name, lookup_name]
    assert not any(isinstance(c, tuple) and c[0] == "change" for c in sdk.calls)
    assert registry_for(adapter).unresolved() == ()


@pytest.mark.parametrize("fail_at", ["size_query", "value_query"])
def test_service_sid_leaf_failed_native_lookup_has_no_identity_fallback(monkeypatch, fail_at):
    from comsol_mcp import _windows_service_sid_native as scm
    from tests.test_windows_service_sid_native import SDK, actual
    from tests.test_windows_host_preflight_owner import Function

    sdk = SDK()
    sdk.account = "LocalSystem"
    calls = []
    error = [122]

    def lookup(host, name, sid, size, domain, domain_size, use):
        calls.append(name)
        if sid is None and fail_at == "value_query":
            C.cast(size, C.POINTER(C.c_uint32))[0] = 12
            C.cast(domain_size, C.POINTER(C.c_uint32))[0] = 1
            error[0] = 122
        else:
            error[0] = 1332
        return 0

    sdk.a.LookupAccountNameW = Function(lookup)
    adapter = actual(monkeypatch, sdk)
    monkeypatch.setattr(C, "get_last_error", lambda: error[0], raising=False)
    with pytest.raises((ExecutionContractError, OSError)):
        scm.observe(adapter, "SoftwareFixtureService")
    assert calls == [r"NT AUTHORITY\SYSTEM"] * (2 if fail_at == "value_query" else 1)
    assert not any(isinstance(c, tuple) and c[0] == "change" for c in sdk.calls)
    assert registry_for(adapter).unresolved() == ()


@pytest.mark.parametrize("account", [
    "LocalSystem ", " LocalSystem", "Localſystem", "OtherService",
    r"OTHERHOST\LocalSystem", r"NT SERVICE\LocalSystem",
    r"NT AUTHORITY\LocalService", r"NT AUTHORITY\NetworkService",
])
def test_name_normalization_does_not_broaden_other_account_names(account):
    from comsol_mcp._windows_sid import normalize_scm_account_name
    assert normalize_scm_account_name(account) == account


def shared_sid_document(service_sid="S-1-5-19", account=r"NT AUTHORITY\LocalService"):
    """Existing complete shared contract, with software-only standard identity intent."""
    from tests.test_windows_host_change import _profile
    document = _profile().as_dict()
    document["service_account_id"] = service_sid
    document["windows_isolation"].update(service_start_name=account, service_logon_sid=service_sid)
    return document


def parse_shared_sid_wrapper(document, version):
    wrapper = {"schema": t037.PROFILE_SCHEMA_V2, "host_resource_profile": document}
    if version == 2:
        return t037.parse_profile_v2_document(wrapper, source_sha256="d" * 64)
    from comsol_mcp._windows_job_limits import JobLimitsV1
    wrapper.update(schema=t037.PROFILE_SCHEMA_V3, job_limits=JobLimitsV1(4096, 16384, 4).as_dict())
    return t037.parse_profile_v3_document(wrapper, source_sha256="d" * 64)


@pytest.mark.parametrize("version", [2, 3])
@pytest.mark.parametrize("account,sid", [
    ("LocalSystem", "S-1-5-18"),
    (r"NT AUTHORITY\LocalService", "S-1-5-19"),
    (r"NT AUTHORITY\NetworkService", "S-1-5-20"),
])
def test_shared_sid_standard_service_identity_is_intent_only(version, account, sid):
    document = shared_sid_document(sid, account)
    parsed = parse_shared_sid_wrapper(document, version)
    assert parsed.service_account_sid == sid
    assert parsed.host_resource_profile.as_dict() == document
    assert parsed.as_safe_dict()["verification_state"] == "NOT_VERIFIED"


@pytest.mark.parametrize("version", [2, 3])
@pytest.mark.parametrize("sid", [
    "S-1-4294967295-4294967295", "S-1-0x000100000000-0",
    "S-1-0xFFFFFFFFFFFF-4294967295", "S-1-5" + "-4294967295" * 15,
])
def test_shared_sid_legal_authority_and_subauthority_boundaries(version, sid):
    from comsol_mcp._windows_sid import is_canonical_sid
    assert is_canonical_sid(sid)
    document = shared_sid_document()
    # Capability fields exercise full SID width without widening the separate
    # existing 128-character top-level account identifier contract.
    document["windows_isolation"]["capability_sids"] = [sid]
    parsed = parse_shared_sid_wrapper(document, version)
    assert parsed.windows_policy.capability_sids == (sid,)
    assert parsed.as_safe_dict()["verification_state"] == "NOT_VERIFIED"


@pytest.mark.parametrize("field", ["service_logon_sid", "service_sid", "appcontainer_sid", "capability", "ACL", "WFP"])
@pytest.mark.parametrize("bad_sid", [
    "S-1-5", "S-1-5-4294967296", "S-1-4294967296-1",
    "S-1-0x1000000000000-1", "S-1-05-19", "S-1-5" + "-1" * 16,
])
def test_shared_sid_rejects_invalid_grammar_at_every_existing_sid_field(field, bad_sid):
    from comsol_mcp import _w26_os_resources as resources
    document = shared_sid_document()
    policy = document["windows_isolation"]
    if field == "service_logon_sid":
        document["service_account_id"] = bad_sid
        policy[field] = bad_sid
    elif field == "capability":
        policy["capability_sids"] = [bad_sid]
    elif field in {"ACL", "WFP"}:
        policy["acl_grants" if field == "ACL" else "wfp_grants"][0]["principal_sid"] = bad_sid
    else:
        policy[field] = bad_sid
    with pytest.raises(ValueError, match="SID"):
        resources.parse_host_resource_profile(document)


@pytest.mark.parametrize("version", [2, 3])
@pytest.mark.parametrize("fault", [
    "capability_duplicate", "ACL_duplicate", "WFP_duplicate", "filter_duplicate",
    "service_interactive_same", "service_interactive_casefold_same", "logon_binding_mismatch",
    "service_sid_is_logon", "service_sid_is_interactive", "policy_role_swap",
    "WFP_service_principal", "ACL_interactive_principal", "WFP_wrong_role_image",
    "write_outside_root", "invalid_port", "default_allow", "missing_owner", "claimed_proof",
    "account_identifier_too_long",
])
def test_shared_sid_existing_identity_grant_and_resource_gates_still_refuse(version, fault):
    import copy
    document = shared_sid_document()
    policy = document["windows_isolation"]
    if fault == "capability_duplicate":
        policy["capability_sids"].append(policy["capability_sids"][0].lower())
    elif fault == "ACL_duplicate":
        policy["acl_grants"].append(copy.deepcopy(policy["acl_grants"][0]))
    elif fault == "WFP_duplicate":
        policy["wfp_grants"].append(copy.deepcopy(policy["wfp_grants"][0]))
    elif fault == "filter_duplicate":
        policy["wfp_filter_keys"].append(policy["wfp_filter_keys"][0].lower())
    elif fault.startswith("service_interactive"):
        document["interactive_account_id"] = document["service_account_id"]
        if fault.endswith("casefold_same"):
            document["interactive_account_id"] = document["interactive_account_id"].lower()
    elif fault == "logon_binding_mismatch":
        document["service_account_id"] = "S-1-5-20"
    elif fault == "service_sid_is_logon":
        policy["service_sid"] = policy["service_logon_sid"]
    elif fault == "service_sid_is_interactive":
        policy["service_sid"] = document["interactive_account_id"]
    elif fault == "policy_role_swap":
        policy["service_sid"], policy["appcontainer_sid"] = policy["appcontainer_sid"], policy["service_sid"]
    elif fault == "WFP_service_principal":
        policy["wfp_grants"][0]["principal_sid"] = policy["service_sid"]
    elif fault == "ACL_interactive_principal":
        policy["acl_grants"][0]["principal_sid"] = document["interactive_account_id"]
    elif fault == "WFP_wrong_role_image":
        policy["wfp_grants"][0]["application_path"] = next(i["path"] for i in document["launch_images"] if i["role"] == "worker")
    elif fault == "write_outside_root":
        policy["acl_grants"][0].update(path=r"C:\unrelated", rights=["WRITE"])
    elif fault == "invalid_port":
        policy["wfp_grants"][0]["remote_ports"] = [65536]
    elif fault == "default_allow":
        policy["default_network_action"] = "ALLOW"
    elif fault == "missing_owner":
        document["root"]["owner_id"] = ""
    elif fault == "claimed_proof":
        policy["verification_state"] = "VERIFIED"
    elif fault == "account_identifier_too_long":
        document["service_account_id"] = policy["service_logon_sid"] = "S-1-5" + "-4294967295" * 15
    with pytest.raises(t037.WindowsT037Error) as error:
        parse_shared_sid_wrapper(document, version)
    assert error.value.code == t037.ERROR_PROFILE_INVALID


def test_shared_sid_legacy_profile_serialization_and_hash_unchanged():
    from tests.test_windows_host_change import _profile
    from comsol_mcp import _w26_os_resources as resources
    legacy = _profile()
    # Captured from the accepted 03e241b7 checker before this one-module change.
    assert legacy.sha256 == "ce3b4ab1d20d1089549b3eb1bf13961f2450789ff2563e9a6677a3a939a840f8"
    restored = resources.parse_host_resource_profile(legacy.as_dict())
    assert restored.as_dict() == legacy.as_dict() and restored.sha256 == legacy.sha256
