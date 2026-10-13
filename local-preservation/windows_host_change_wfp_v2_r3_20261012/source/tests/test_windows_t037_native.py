from __future__ import annotations

import ctypes as C
import hashlib
import json
import struct
from types import SimpleNamespace

import pytest

from comsol_mcp import _windows_t037_native as native


FP = "f" * 64
SHA = "a" * 64
PROVIDER = "{10000000-0000-0000-0000-000000000001}"
SUBLAYER = "{20000000-0000-0000-0000-000000000002}"
FILTER = "{30000000-0000-0000-0000-000000000003}"
LAYER = "{40000000-0000-0000-0000-000000000004}"
SERVICE_SID = "S-1-5-80-1"
LOGON_SID = "S-1-5-21-1000-1000"
INTERACTIVE_SID = "S-1-5-21-2000-2000"
APP_SID = "S-1-15-2-100"
CAP_SID = "S-1-15-3-100"
SERVER_PATH = r"C:\COMSOL\server.exe"
ARGV_SHA = "d" * 64


def _profile():
    grant = SimpleNamespace(layer_key=LAYER)
    policy = SimpleNamespace(
        service_name="ComsolHost",
        service_start_name="McpService",
        service_logon_sid=LOGON_SID,
        service_sid=SERVICE_SID,
        service_sid_type="RESTRICTED",
        appcontainer_sid=APP_SID,
        capability_sids=(CAP_SID,),
        wfp_filter_keys=(FILTER,),
        wfp_provider_key=PROVIDER,
        wfp_sublayer_key=SUBLAYER,
        wfp_grants=(grant,),
    )
    server = SimpleNamespace(role="server", path=SERVER_PATH, argv_sha256=ARGV_SHA)
    worker = SimpleNamespace(role="worker", path=r"C:\COMSOL\worker.exe", argv_sha256="e" * 64)
    return SimpleNamespace(
        windows_policy=policy,
        host_fingerprint_sha256=FP,
        interactive_account_sid=INTERACTIVE_SID,
        launch_images=(server, worker),
        sha256="b" * 64,
    )


def _wfp_snapshot(**overrides):
    provider = {
        "provider_key": PROVIDER,
        "display_data": {"name": "service", "description": None},
        "flags": 0,
        "provider_data_sha256": SHA,
        "service_name": None,
        "security_sha256": "1" * 64,
    }
    sublayer = {
        "sublayer_key": SUBLAYER,
        "display_data": {"name": "restricted", "description": "isolated"},
        "flags": 0,
        "provider_key": PROVIDER,
        "provider_data_sha256": SHA,
        "weight": 10,
        "security_sha256": "2" * 64,
    }
    filter_row = {
        "filter_key": FILTER,
        "display_data": {"name": "loopback", "description": None},
        "provider_key": PROVIDER,
        "layer_key": LAYER,
        "sublayer_key": SUBLAYER,
        "flags": 0,
        "filter_id": 100,
        "action_type": native.CtypesWindowsPolicyApi.FWP_ACTION_PERMIT,
        "action_callout_key": None,
        "context": {"raw_context": 0},
        "weight": {"type": 0, "value": None},
        "effective_weight": {"type": 3, "value": 1},
        "has_provider_context": False,
        "provider_data_sha256": SHA,
        "conditions": [],
        "security_sha256": "3" * 64,
    }
    rows = {"providers": [provider], "sublayers": [sublayer], "filters": [filter_row],
            "provider_contexts": []}
    rows.update(overrides.pop("rows", {}))
    canonical = {
        "providers": sorted(rows["providers"], key=lambda item: item["provider_key"] or ""),
        "sublayers": sorted(rows["sublayers"], key=lambda item: item["sublayer_key"] or ""),
        "filters": sorted(rows["filters"], key=lambda item: (item["layer_key"] or "", item["filter_id"])),
        "provider_contexts": rows["provider_contexts"],
    }
    return {
        "state": "COMPLETE_READ_ONLY",
        "counts": {
            "providers": len(rows["providers"]),
            "sublayers": len(rows["sublayers"]),
            "filters": len(rows["filters"]),
            "provider_contexts": len(rows["provider_contexts"]),
            "conditions": sum(len(item["conditions"]) for item in rows["filters"]),
            "pages": 4,
            "pages_by_kind": {"Provider": 1, "SubLayer": 1, "Filter": 1, "ProviderContext": 1},
            "total_records": sum(len(rows[key]) for key in
                                  ("providers", "sublayers", "filters", "provider_contexts")),
            "total_bytes": len(native._canonical(canonical)),
            "elapsed_ms": 5,
        },
        "limits": {
            "max_page_size": native.MAX_ENUM_PAGE,
            "max_pages_per_kind": native.MAX_ENUM_PAGES,
            "max_total_pages": native.MAX_ENUM_PAGES * 4,
            "max_total_records": native.MAX_ENUM_ROWS,
            "max_total_bytes": native.MAX_ENUM_TOTAL_BYTES,
            "max_elapsed_ms": native.MAX_ENUM_SECONDS * 1000,
        },
        "snapshot_sha256": hashlib.sha256(native._canonical(canonical)).hexdigest(),
        "reasons": ["WFP_EFFECTIVE_ARBITRATION_NOT_PROVEN"],
        **rows,
        **overrides,
    }


def _reseal_wfp_snapshot(wfp):
    canonical = {
        "providers": sorted(wfp["providers"], key=lambda item: item["provider_key"]),
        "sublayers": sorted(wfp["sublayers"], key=lambda item: item["sublayer_key"]),
        "filters": sorted(wfp["filters"], key=lambda item: (item["layer_key"], item["filter_id"])),
        "provider_contexts": wfp["provider_contexts"],
    }
    wfp["counts"]["total_bytes"] = len(native._canonical(canonical))
    wfp["snapshot_sha256"] = hashlib.sha256(native._canonical(canonical)).hexdigest()
    return wfp


def _service(**overrides):
    row = {
        "service_name": "ComsolHost",
        "service_start_name": "McpService",
        "service_start_sid": LOGON_SID,
        "service_sid_type": "RESTRICTED",
        "service_sid": SERVICE_SID,
        "state": "RUNNING",
        "process_id": 456,
        "binary_path": SERVER_PATH,
        "service_binary_image_path": SERVER_PATH,
        "service_binary_argv_sha256": ARGV_SHA,
        "service_security_sha256": "9" * 64,
        "service_security_semantics": "OWNER_DACL_CANONICAL_ACE_ORDER_READ_CONTROL",
    }
    row.update(overrides)
    return row


class _FakeApi:
    def __init__(self, *, service=None, wfp=None, interactive=(INTERACTIVE_SID,)):
        self.service = service or _service()
        self.wfp = wfp or _wfp_snapshot()
        self.interactive = interactive
        self.service_calls = 0

    def observe_service(self, _name):
        self.service_calls += 1
        return dict(self.service)

    def observe_wfp_snapshot(self):
        return json.loads(json.dumps(self.wfp))

    def observe_active_interactive_sids(self):
        return self.interactive


class _FakeProcessObserver:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.calls = 0

    def observe_process(self, _pid):
        self.calls += 1
        if self.rows:
            return dict(self.rows.pop(0))
        return {
            "pid": 456,
            "birth_filetime_100ns": 123456789,
            "exit_filetime_100ns": None,
            "image_path": SERVER_PATH,
            "job_membership_any": True,
            "job_membership_exact": False,
            "job": None,
            "token": {
                "state": "OBSERVED",
                "user_sid": LOGON_SID,
                "group_sids": [SERVICE_SID],
                "logon_sids": [LOGON_SID],
                "is_appcontainer": True,
                "appcontainer_sid": APP_SID,
                "capability_sids": [CAP_SID],
                "integrity_sid": "S-1-16-8192",
                "integrity_attributes": 0,
            },
        }


def _observe(*, api=None, process=None, current_host=FP):
    api = api or _FakeApi()
    process = process or _FakeProcessObserver()
    result = native.WindowsNativePolicyAdapter(api=api).observe(
        _profile(), current_host_fingerprint=current_host, process_observer=process)
    return result, api, process


def _security_descriptor(*, dacl_present=True, ace=b""):
    control = 0x8000 | (0x0004 if dacl_present else 0)
    dacl_offset = 20 if dacl_present and ace is not None else 0
    if not dacl_present:
        acl = b""
        dacl_offset = 0
    elif ace is None:
        acl = b""
        dacl_offset = 0
    else:
        acl = struct.pack("<BBHHH", 2, 0, 8 + len(ace), 1 if ace else 0, 0) + ace
    header = struct.pack("<BBHIIII", 1, 0, control, 0, 0, 0, dacl_offset)
    return header + (b"\x00" * (20 - len(header))) + acl


def test_fixed_width_types_and_wfp_layout_validate_on_non_windows_host():
    assert C.sizeof(native.DWORD) == 4
    assert C.sizeof(native.LONG) == 4
    assert C.sizeof(native.BOOL) == 4
    assert C.sizeof(native.WORD) == 2
    native.CtypesWindowsPolicyApi._validate_abi(object.__new__(native.CtypesWindowsPolicyApi))


def test_v2_profile_bridge_parses_complete_closed_windows_contract_only():
    from comsol_mcp import _w26_os_resources as resources
    from comsol_mcp import _windows_t037 as t037

    root_path = r"C:\COMSOL\workspace"
    images = (
        resources.LaunchImage("launcher", r"C:\COMSOL\launcher.exe", "1" * 64, "2" * 64),
        resources.LaunchImage("server", SERVER_PATH, "3" * 64, ARGV_SHA),
        resources.LaunchImage("worker", r"C:\COMSOL\worker.exe", "4" * 64, "5" * 64),
    )
    isolation = resources.WindowsIsolationPolicy(
        service_name="ComsolHost",
        service_start_name="McpService",
        service_logon_sid=LOGON_SID,
        service_sid=SERVICE_SID,
        service_sid_type="RESTRICTED",
        appcontainer_profile_name="comsol-private-v2",
        appcontainer_sid=APP_SID,
        capability_sids=(CAP_SID,),
        acl_grants=(resources.WindowsPathAclGrant(
            root_path, APP_SID, ("READ",), SHA),),
        wfp_provider_key=PROVIDER,
        wfp_sublayer_key=SUBLAYER,
        wfp_filter_keys=(FILTER,),
        wfp_grants=(resources.WindowsWfpGrant(
            role="server",
            direction="OUTBOUND",
            layer="ALE_AUTH_CONNECT",
            layer_key=LAYER,
            address_family="IPV4",
            protocol=6,
            application_path=SERVER_PATH,
            principal_sid=APP_SID,
            local_addresses=("127.0.0.1/32",),
            remote_addresses=("127.0.0.1/32",),
            local_ports=(),
            remote_ports=(2040,),
        ),),
        host_fingerprint_sha256=FP,
    )
    profile = resources.HostResourceProfile(
        profile_id="profile-1",
        project_id="project-1",
        platform="windows",
        host_id="host-1",
        root=resources.PathIdentity(root_path, "volume-1", "root-1", "owner-1"),
        launcher_account_id="S-1-5-21-3000",
        service_account_id=LOGON_SID,
        interactive_account_id=INTERACTIVE_SID,
        applied_policy_id="comsol-policy-v2",
        launch_images=images,
        file_grants=(resources.PathGrant(root_path, ("READ", "WRITE", "EXECUTE")),),
        network_grants=(resources.NetworkGrant("LOOPBACK_ENDPOINT", "127.0.0.1", 2040),),
        disk_budget=resources.DiskBudgetPolicy("ADMISSION_ONLY", 0, 1024, None),
        allowed_operations=("study.run",),
        windows_isolation=isolation,
    )
    resources.validate_profile(profile)
    document = {"schema": t037.PROFILE_SCHEMA_V2,
                "host_resource_profile": profile.as_dict()}
    parsed = t037.parse_profile_v2_document(document, source_sha256=SHA)
    assert parsed.service_account_sid == LOGON_SID
    assert parsed.service_sid == SERVICE_SID
    assert parsed.windows_policy.wfp_grants[0].remote_ports == (2040,)
    assert parsed.as_safe_dict()["verification_state"] == "NOT_VERIFIED"

    with pytest.raises(t037.WindowsT037Error) as wrapper_extra:
        t037.parse_profile_v2_document({**document, "verified": True}, source_sha256=SHA)
    assert wrapper_extra.value.code == t037.ERROR_PROFILE_INVALID
    inner = profile.as_dict()
    inner["windows_isolation"]["caller_claim"] = True
    with pytest.raises(t037.WindowsT037Error) as inner_extra:
        t037.parse_profile_v2_document({"schema": t037.PROFILE_SCHEMA_V2,
                                        "host_resource_profile": inner}, source_sha256=SHA)
    assert inner_extra.value.code == t037.ERROR_PROFILE_INVALID
    for malformed_guid in ("{" + PROVIDER.strip("{}"), PROVIDER.strip("{}") + "}"):
        inner = profile.as_dict()
        inner["windows_isolation"]["wfp_provider_key"] = malformed_guid
        with pytest.raises(ValueError, match="wfp_provider_key"):
            resources.parse_host_resource_profile(inner)


def test_security_descriptor_digest_is_canonical_and_preserves_dacl_state_and_order():
    first = _security_descriptor(ace=struct.pack("<BBHI", 0, 0, 8, 0x001F01FF))
    same = bytearray(first)
    assert native.canonical_security_descriptor_sha256(first) == native.canonical_security_descriptor_sha256(same)
    absent = _security_descriptor(dacl_present=False)
    null = _security_descriptor(dacl_present=True, ace=None)
    assert native.canonical_security_descriptor_sha256(absent) != native.canonical_security_descriptor_sha256(null)
    ace_a = struct.pack("<BBHI", 0, 0, 8, 1)
    ace_b = struct.pack("<BBHI", 0, 0, 8, 2)
    def with_aces(*aces):
        body = b"".join(aces)
        acl = struct.pack("<BBHHH", 2, 0, 8 + len(body), len(aces), 0) + body
        header = struct.pack("<BBHIIII", 1, 0, 0x8004, 0, 0, 0, 20)
        return header + acl
    assert native.canonical_security_descriptor_sha256(with_aces(ace_a, ace_b)) != \
        native.canonical_security_descriptor_sha256(with_aces(ace_b, ace_a))


def test_security_descriptor_rejects_unrequested_sacl_and_malformed_acl_bounds():
    raw = bytearray(_security_descriptor())
    struct.pack_into("<I", raw, 12, 20)
    with pytest.raises(ValueError, match="SACL"):
        native.canonical_security_descriptor(raw)
    raw = bytearray(_security_descriptor())
    struct.pack_into("<I", raw, 16, len(raw) + 4)
    with pytest.raises(ValueError, match="DACL"):
        native.canonical_security_descriptor(raw)


def test_wfp_budget_rejects_page_row_byte_and_deadline_overruns(monkeypatch):
    monkeypatch.setattr(native, "MAX_ENUM_PAGES", 1)
    budget = native._WfpBudget.start()
    budget.add_page("Filter")
    with pytest.raises(native.NativePolicyError) as page:
        budget.add_page("Filter")
    assert page.value.code == "T037_NATIVE_WFP_PAGE_LIMIT"

    monkeypatch.setattr(native, "MAX_ENUM_ROWS", 1)
    monkeypatch.setattr(native, "MAX_ENUM_TOTAL_BYTES", 1_000)
    budget = native._WfpBudget.start()
    budget.add_row({"small": 1})
    with pytest.raises(native.NativePolicyError) as rows:
        budget.add_row({"small": 2})
    assert rows.value.code == "T037_NATIVE_WFP_ROW_LIMIT"

    monkeypatch.setattr(native, "MAX_ENUM_ROWS", 10)
    monkeypatch.setattr(native, "MAX_ENUM_TOTAL_BYTES", 2)
    budget = native._WfpBudget.start()
    with pytest.raises(native.NativePolicyError) as size:
        budget.add_row({"long": "payload"})
    assert size.value.code == "T037_NATIVE_WFP_BYTE_LIMIT"

    budget = native._WfpBudget(1, 2)
    with pytest.raises(native.NativePolicyError) as elapsed:
        budget.check_time(now_ns=3)
    assert elapsed.value.code == "T037_NATIVE_WFP_TIME_LIMIT"


def test_native_observation_samples_scm_and_process_twice_but_never_issues_binding():
    result, api, process = _observe()
    assert api.service_calls == 2
    assert process.calls == 2
    assert result.service_pid == 456
    assert result.state == "UNVERIFIED"
    assert result.evidence_classification == "SOFTWARE_ONLY"
    assert result.verified is False
    assert result.as_safe_dict()["policy_binding"] == "NOT_ISSUED"
    assert "SCM_PROCESS_NATIVE_OBSERVATION_UNAVAILABLE" not in result.reasons
    assert "EXACT_SERVER_WORKER_JOB_BINDING_UNAVAILABLE" in result.reasons
    assert "WFP_EFFECTIVE_ARBITRATION_NOT_PROVEN" in result.reasons
    assert "NATIVE_T037_PROOF_ISSUER_NOT_IMPLEMENTED" in result.reasons


def test_native_observation_rejects_wrong_host_and_caller_constructed_verified_value():
    with pytest.raises(native.NativePolicyError) as host:
        _observe(current_host="0" * 64)
    assert host.value.code == "T037_NATIVE_HOST_MISMATCH"
    with pytest.raises(ValueError, match="cannot issue"):
        native.NativePolicyObservation(
            "VERIFIED", "SOFTWARE_ONLY", FP, SHA, SHA, SHA, {}, 1, ())
    with pytest.raises(ValueError, match="private producer seal"):
        native.NativePolicyObservation(
            "UNVERIFIED", "NATIVE_READ_ONLY_OBSERVATION", FP, SHA, SHA, SHA, {}, 1, ())


def test_native_observation_cannot_label_a_caller_process_fixture_as_native():
    with pytest.raises(native.NativePolicyError) as error:
        native.WindowsNativePolicyAdapter._require_native_process_observer(_FakeProcessObserver())
    assert error.value.code == "T037_NATIVE_PROCESS_OBSERVER_UNTRUSTED"


def test_native_observation_records_scm_and_process_identity_drift():
    api = _FakeApi(service=_service())
    original = _FakeProcessObserver().observe_process(456)
    after = dict(original)
    after["birth_filetime_100ns"] += 1
    process = _FakeProcessObserver([original, after])
    result = native.WindowsNativePolicyAdapter(api=api).observe(
        _profile(), current_host_fingerprint=FP, process_observer=process)
    assert "SCM_PROCESS_PID_BIRTH_IMAGE_OR_TOKEN_CHANGED_DURING_POLICY_READ" in result.reasons

    api = _FakeApi(service=_service(process_id=457))
    result = native.WindowsNativePolicyAdapter(api=api).observe(
        _profile(), current_host_fingerprint=FP, process_observer=_FakeProcessObserver())
    assert "SCM_CONFIGURATION_OR_PID_CHANGED_DURING_POLICY_READ" not in result.reasons
    assert "SCM_PROCESS_PID_BIRTH_IMAGE_OR_TOKEN_CHANGED_DURING_POLICY_READ" in result.reasons
    assert result.service_pid == 457


def test_native_observation_marks_wrong_service_token_image_and_argv_unverified():
    api = _FakeApi(service=_service(
        service_start_name="Other",
        service_binary_image_path=r"C:\Other\server.exe",
        service_binary_argv_sha256="0" * 64,
        service_sid="S-1-5-80-99",
    ))
    process = _FakeProcessObserver()
    result = native.WindowsNativePolicyAdapter(api=api).observe(
        _profile(), current_host_fingerprint=FP, process_observer=process)
    assert "SCM_SERVICE_START_NAME_MISMATCH" in result.reasons
    assert "SCM_SERVICE_SID_MISMATCH" in result.reasons
    assert "SCM_BINARY_IMAGE_OR_ARGV_MISMATCH" in result.reasons

    rows = _FakeProcessObserver().observe_process(456)
    rows["token"] = dict(rows["token"], user_sid="S-1-5-21-else")
    result = native.WindowsNativePolicyAdapter(api=_FakeApi()).observe(
        _profile(), current_host_fingerprint=FP, process_observer=_FakeProcessObserver([rows, rows]))
    assert "SCM_PROCESS_TOKEN_USER_MISMATCH" in result.reasons


def test_native_observation_rejects_malformed_or_truncated_wfp_snapshot():
    wfp = _wfp_snapshot()
    wfp["counts"]["pages"] = 2
    with pytest.raises(native.NativePolicyError) as pages:
        _observe(api=_FakeApi(wfp=wfp))
    assert pages.value.code == "T037_NATIVE_WFP_ABI_INVALID"

    wfp = _wfp_snapshot()
    wfp["filters"][0]["conditions"].append({"field_key": "unknown", "match_type": 0,
                                            "condition_value": {"type": 1, "value": 1}})
    with pytest.raises(native.NativePolicyError) as digest:
        _observe(api=_FakeApi(wfp=wfp))
    assert digest.value.code == "T037_NATIVE_WFP_ABI_INVALID"


@pytest.mark.parametrize("bad_guid", [
    "{30000000-0000-0000-0000-000000000003",
    "30000000-0000-0000-0000-000000000003}",
])
def test_native_observation_rejects_mismatched_guid_braces_even_with_recomputed_digest(bad_guid):
    wfp = _wfp_snapshot()
    wfp["filters"][0]["filter_key"] = bad_guid
    _reseal_wfp_snapshot(wfp)
    with pytest.raises(native.NativePolicyError) as error:
        _observe(api=_FakeApi(wfp=wfp))
    assert error.value.code == "T037_NATIVE_WFP_ABI_INVALID"


def test_native_observation_keeps_valid_conditions_and_rights_unresolved():
    wfp = _wfp_snapshot()
    wfp["filters"][0]["conditions"] = [{
        "field_key": LAYER, "match_type": 0, "condition_value": {"type": 0, "value": None},
    }]
    wfp["counts"]["conditions"] = 1
    _reseal_wfp_snapshot(wfp)
    result, _, _ = _observe(api=_FakeApi(wfp=wfp))
    assert "WFP_CONDITION_FIELD_SEMANTICS_NOT_CLASSIFIED" in result.reasons
    assert result.state == "UNVERIFIED"

    wfp = _wfp_snapshot()
    wfp["filters"][0]["flags"] = native.CtypesWindowsPolicyApi.FWP_FILTER_FLAG_CLEAR_ACTION_RIGHT
    _reseal_wfp_snapshot(wfp)
    result, _, _ = _observe(api=_FakeApi(wfp=wfp))
    assert "WFP_FILTER_RIGHTS_OR_CALLOUT_FALLBACK_UNRESOLVED" in result.reasons

    wfp = _wfp_snapshot()
    wfp["filters"][0]["flags"] = 0x80
    _reseal_wfp_snapshot(wfp)
    result, _, _ = _observe(api=_FakeApi(wfp=wfp))
    assert "WFP_UNKNOWN_FILTER_FLAGS_PRESENT" in result.reasons


def test_native_observation_rejects_provider_context_flag_shape_mismatch():
    wfp = _wfp_snapshot()
    wfp["filters"][0]["flags"] = native.CtypesWindowsPolicyApi.FWP_FILTER_FLAG_HAS_PROVIDER_CONTEXT
    _reseal_wfp_snapshot(wfp)
    with pytest.raises(native.NativePolicyError) as error:
        _observe(api=_FakeApi(wfp=wfp))
    assert error.value.code == "T037_NATIVE_WFP_ABI_INVALID"


def test_native_observation_refuses_unknown_wfp_conditions_and_external_layer_filters():
    wfp = _wfp_snapshot()
    wfp["state"] = "INCOMPLETE"
    wfp["reasons"] = ["PAGE_BUDGET_REACHED"]
    result, _, _ = _observe(api=_FakeApi(wfp=wfp))
    assert result.state == "UNVERIFIED"
    assert "WFP_SNAPSHOT_INCOMPLETE_OR_UNVERIFIED" in result.reasons

    wfp = _wfp_snapshot()
    extra = dict(wfp["filters"][0])
    extra["filter_key"] = "{50000000-0000-0000-0000-000000000005}"
    extra["filter_id"] = 101
    wfp["filters"].append(extra)
    content = {"providers": wfp["providers"], "sublayers": wfp["sublayers"],
               "filters": sorted(wfp["filters"], key=lambda item: (item["layer_key"], item["filter_id"])),
               "provider_contexts": wfp["provider_contexts"]}
    wfp["counts"]["filters"] += 1
    wfp["counts"]["total_records"] += 1
    wfp["counts"]["total_bytes"] = len(native._canonical(content))
    wfp["snapshot_sha256"] = hashlib.sha256(native._canonical(content)).hexdigest()
    result, _, _ = _observe(api=_FakeApi(wfp=wfp))
    assert "WFP_EXTERNAL_FILTER_AT_CONFIGURED_LAYER_UNRESOLVED" in result.reasons


def test_native_observation_keeps_provider_context_rows_unclassified_and_digest_bound():
    wfp = _wfp_snapshot()
    wfp["provider_contexts"] = [{"state": "PRESENT_UNCLASSIFIED"}]
    wfp["counts"]["provider_contexts"] = 1
    wfp["counts"]["total_records"] += 1
    _reseal_wfp_snapshot(wfp)
    result, _, _ = _observe(api=_FakeApi(wfp=wfp))
    assert result.state == "UNVERIFIED"
    assert "WFP_PROVIDER_CONTEXT_ROWS_UNCLASSIFIED" in result.reasons

    malformed = _wfp_snapshot()
    malformed["provider_contexts"] = [{"state": "OWNED_BY_CONFIGURED_PROVIDER"}]
    malformed["counts"]["provider_contexts"] = 1
    malformed["counts"]["total_records"] += 1
    _reseal_wfp_snapshot(malformed)
    with pytest.raises(native.NativePolicyError) as error:
        _observe(api=_FakeApi(wfp=malformed))
    assert error.value.code == "T037_NATIVE_WFP_ABI_INVALID"


def test_wfp_enum_destroy_failure_keeps_cleanup_unknown_with_primary_error():
    class FakeWfp:
        def FwpmFilterCreateEnumHandle0(self, _engine, _template, out):
            C.cast(out, C.POINTER(native.HANDLE)).contents.value = 12
            return 0

        def FwpmFilterEnum0(self, _engine, _handle, _page, array_out, count_out):
            C.cast(array_out, C.POINTER(C.c_void_p)).contents.value = None
            C.cast(count_out, C.POINTER(native.DWORD)).contents.value = 0
            return 0

        def FwpmFilterDestroyEnumHandle0(self, _engine, _handle):
            return 77

    api = object.__new__(native.CtypesWindowsPolicyApi)
    api._fwpu = FakeWfp()
    with pytest.raises(native.NativePolicyError) as cleanup:
        api._enum(1, "Filter", native._WfpBudget.start())
    assert cleanup.value.code == "T037_NATIVE_WFP_HANDLE_RELEASE_UNKNOWN"


def test_wfp_enum_release_error_preserves_query_failure_code():
    class FakeWfp:
        def FwpmFilterCreateEnumHandle0(self, _engine, _template, out):
            C.cast(out, C.POINTER(native.HANDLE)).contents.value = 12
            return 0

        def FwpmFilterEnum0(self, _engine, _handle, _page, _array_out, _count_out):
            return 88

        def FwpmFilterDestroyEnumHandle0(self, _engine, _handle):
            return 77

    api = object.__new__(native.CtypesWindowsPolicyApi)
    api._fwpu = FakeWfp()
    with pytest.raises(native.NativePolicyError) as cleanup:
        api._enum(1, "Filter", native._WfpBudget.start())
    assert cleanup.value.code == "T037_NATIVE_WFP_HANDLE_RELEASE_UNKNOWN"
    assert cleanup.value.details["primary_code"] == "T037_NATIVE_WFP_QUERY_FAILED"


def _page_allocation_cleanup_probe(monkeypatch, *, enum_status=0, expire_after_return=False,
                                  free_raises=False):
    clock = [100]
    monkeypatch.setattr(native.time, "monotonic_ns", lambda: clock[0])
    sentinel = 0x123450

    class FakeWfp:
        def __init__(self):
            self.free_values = []
            self.destroyed = []
            self.aborted = 0
            self.closed = 0
            self.committed = 0

        def FwpmEngineOpen0(self, _name, _auth, _identity, _session, out):
            C.cast(out, C.POINTER(native.HANDLE)).contents.value = 33
            return 0

        def FwpmTransactionBegin0(self, _engine, _flags):
            return 0

        def FwpmProviderCreateEnumHandle0(self, _engine, _template, out):
            C.cast(out, C.POINTER(native.HANDLE)).contents.value = 12
            return 0

        def FwpmProviderEnum0(self, _engine, _handle, _page_size, array_out, count_out):
            C.cast(array_out, C.POINTER(C.c_void_p)).contents.value = sentinel
            C.cast(count_out, C.POINTER(native.DWORD)).contents.value = 1
            if expire_after_return:
                clock[0] += (native.MAX_ENUM_SECONDS * 1_000_000_000) + 1
            return enum_status

        def FwpmProviderDestroyEnumHandle0(self, _engine, handle):
            self.destroyed.append(handle.value)
            return 0

        def FwpmFreeMemory0(self, pointer_to_pointer):
            value = C.cast(pointer_to_pointer, C.POINTER(C.c_void_p)).contents.value
            self.free_values.append(value)
            if free_raises:
                raise RuntimeError("synthetic WFP page release failure")

        def FwpmTransactionAbort0(self, _engine):
            self.aborted += 1
            return 0

        def FwpmTransactionCommit0(self, _engine):
            self.committed += 1
            return 0

        def FwpmEngineClose0(self, _engine):
            self.closed += 1
            return 0

    api = object.__new__(native.CtypesWindowsPolicyApi)
    api._fwpu = FakeWfp()
    return api, api._fwpu, sentinel


def test_wfp_page_allocation_is_freed_after_successful_page_expires_time_budget(monkeypatch):
    api, fwpu, sentinel = _page_allocation_cleanup_probe(
        monkeypatch, expire_after_return=True)

    with pytest.raises(native.NativePolicyError) as expired:
        api.observe_wfp_snapshot()

    assert expired.value.code == "T037_NATIVE_WFP_TIME_LIMIT"
    assert fwpu.free_values == [sentinel]
    assert fwpu.destroyed == [12]
    assert fwpu.aborted == 1
    assert fwpu.committed == 0
    assert fwpu.closed == 1


def test_wfp_error_status_with_nonnull_page_frees_before_aborting(monkeypatch):
    api, fwpu, sentinel = _page_allocation_cleanup_probe(monkeypatch, enum_status=88)

    with pytest.raises(native.NativePolicyError) as failure:
        api.observe_wfp_snapshot()

    assert failure.value.code == "T037_NATIVE_WFP_QUERY_FAILED"
    assert failure.value.details["status"] == 88
    assert fwpu.free_values == [sentinel]
    assert fwpu.destroyed == [12]
    assert fwpu.aborted == 1
    assert fwpu.committed == 0
    assert fwpu.closed == 1


def test_wfp_page_release_failure_is_unknown_and_retains_enum_primary(monkeypatch):
    api, fwpu, sentinel = _page_allocation_cleanup_probe(
        monkeypatch, enum_status=88, free_raises=True)

    with pytest.raises(native.NativePolicyError) as failure:
        api.observe_wfp_snapshot()

    assert failure.value.code == "T037_NATIVE_WFP_HANDLE_RELEASE_UNKNOWN"
    assert failure.value.details["primary_code"] == "T037_NATIVE_WFP_QUERY_FAILED"
    assert fwpu.free_values == [sentinel]
    assert fwpu.destroyed == [12]
    assert fwpu.aborted == 1
    assert fwpu.committed == 0
    assert fwpu.closed == 1


def test_wfp_readonly_transaction_aborts_and_closes_engine_after_query_failure():
    class FakeWfp:
        def __init__(self):
            self.aborted = 0
            self.closed = 0

        def FwpmEngineOpen0(self, _name, _auth, _identity, _session, out):
            C.cast(out, C.POINTER(native.HANDLE)).contents.value = 33
            return 0

        def FwpmTransactionBegin0(self, _engine, _flags):
            return 0

        def FwpmTransactionAbort0(self, _engine):
            self.aborted += 1
            return 0

        def FwpmEngineClose0(self, _engine):
            self.closed += 1
            return 0

    api = object.__new__(native.CtypesWindowsPolicyApi)
    api._fwpu = FakeWfp()
    api._enum = lambda *_args: (_ for _ in ()).throw(
        native.NativePolicyError("T037_NATIVE_WFP_QUERY_FAILED", "synthetic query failure"))
    with pytest.raises(native.NativePolicyError) as failure:
        api.observe_wfp_snapshot()
    assert failure.value.code == "T037_NATIVE_WFP_QUERY_FAILED"
    assert api._fwpu.aborted == 1
    assert api._fwpu.closed == 1


def test_wfp_engine_close_failure_is_typed_unknown_and_keeps_primary_code():
    class FakeWfp:
        def __init__(self):
            self.closed = 0

        def FwpmEngineOpen0(self, _name, _auth, _identity, _session, out):
            C.cast(out, C.POINTER(native.HANDLE)).contents.value = 33
            return 0

        def FwpmTransactionBegin0(self, _engine, _flags):
            return 0

        def FwpmTransactionAbort0(self, _engine):
            return 0

        def FwpmEngineClose0(self, _engine):
            self.closed += 1
            return 99

    api = object.__new__(native.CtypesWindowsPolicyApi)
    api._fwpu = FakeWfp()
    api._enum = lambda *_args: (_ for _ in ()).throw(
        native.NativePolicyError("T037_NATIVE_WFP_QUERY_FAILED", "synthetic query failure"))
    with pytest.raises(native.NativePolicyError) as cleanup:
        api.observe_wfp_snapshot()
    assert cleanup.value.code == "T037_NATIVE_WFP_HANDLE_RELEASE_UNKNOWN"
    assert cleanup.value.details["primary_code"] == "T037_NATIVE_WFP_QUERY_FAILED"
    assert api._fwpu.closed == 1


@pytest.mark.parametrize("abort_mode", ["status", "exception"])
def test_wfp_abort_failure_still_closes_engine_and_keeps_primary_query_error(abort_mode):
    class FakeWfp:
        def __init__(self):
            self.closed = 0

        def FwpmEngineOpen0(self, _name, _auth, _identity, _session, out):
            C.cast(out, C.POINTER(native.HANDLE)).contents.value = 33
            return 0

        def FwpmTransactionBegin0(self, _engine, _flags):
            return 0

        def FwpmTransactionAbort0(self, _engine):
            if abort_mode == "exception":
                raise RuntimeError("synthetic abort failure")
            return 77

        def FwpmEngineClose0(self, _engine):
            self.closed += 1
            return 0

    api = object.__new__(native.CtypesWindowsPolicyApi)
    api._fwpu = FakeWfp()
    api._enum = lambda *_args: (_ for _ in ()).throw(
        native.NativePolicyError("T037_NATIVE_WFP_QUERY_FAILED", "synthetic query failure"))
    with pytest.raises(native.NativePolicyError) as failure:
        api.observe_wfp_snapshot()
    assert failure.value.code == "T037_NATIVE_WFP_TRANSACTION_RELEASE_UNKNOWN"
    assert failure.value.details["primary_code"] == "T037_NATIVE_WFP_QUERY_FAILED"
    assert api._fwpu.closed == 1


def test_wfp_elapsed_bound_includes_engine_close(monkeypatch):
    clock = [100]
    monkeypatch.setattr(native.time, "monotonic_ns", lambda: clock[0])

    class FakeWfp:
        def __init__(self):
            self.closed = 0

        def FwpmEngineOpen0(self, _name, _auth, _identity, _session, out):
            C.cast(out, C.POINTER(native.HANDLE)).contents.value = 33
            return 0

        def FwpmTransactionBegin0(self, _engine, _flags):
            return 0

        def FwpmTransactionCommit0(self, _engine):
            return 0

        def FwpmEngineClose0(self, _engine):
            self.closed += 1
            clock[0] += (native.MAX_ENUM_SECONDS + 1) * 1_000_000_000
            return 0

    api = object.__new__(native.CtypesWindowsPolicyApi)
    api._fwpu = FakeWfp()
    api._enum = lambda *_args: []
    with pytest.raises(native.NativePolicyError) as elapsed:
        api.observe_wfp_snapshot()
    assert elapsed.value.code == "T037_NATIVE_WFP_TIME_LIMIT"
    assert api._fwpu.closed == 1
