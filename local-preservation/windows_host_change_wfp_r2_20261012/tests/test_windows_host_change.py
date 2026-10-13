from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import uuid

import pytest

from comsol_mcp import _w26_os_resources as resources
from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._host_scoped_provider import (
    CONFIG_SCHEMA,
    HostScopedProvider,
    scope_sha256,
)
from comsol_mcp._operation_store import OperationStore
from comsol_mcp._project_authority import ProjectAuthority
from comsol_mcp._windows_host_change import (
    WindowsHostChangeError,
    WindowsHostChangeExecutor,
    _SOFTWARE_TEST_TOKEN,
    _normalize_supported_plan,
)
from comsol_mcp._windows_t037_wfp import sha256

HOST_ID = "host-test"
PROJECT_ID = "project-test"
ROOT = r"C:\COMSOL\workspace"
SERVICE_LOGON_SID = "S-1-5-21-1000"
SERVICE_SID = "S-1-5-21-1001"
INTERACTIVE_SID = "S-1-5-21-1002"
APPCONTAINER_SID = "S-1-15-2-123456789"
CAPABILITY_SID = "S-1-15-3-123456789"
PROVIDER_KEY = "{11111111-1111-4111-8111-111111111111}"
SUBLAYER_KEY = "{22222222-2222-4222-8222-222222222222}"
FILTER_KEY = "{33333333-3333-4333-8333-333333333333}"
LAYER_KEY = "{C38D57D1-05A7-4C33-904F-7FBCEEE60E82}"
SERVER_PATH = r"C:\COMSOL\server\mphserver.exe"


def _profile() -> resources.HostResourceProfile:
    grant = resources.WindowsWfpGrant(
        role="server", direction="OUTBOUND", layer="ALE_AUTH_CONNECT",
        layer_key=LAYER_KEY, address_family="IPV4", protocol=6,
        application_path=SERVER_PATH, principal_sid=APPCONTAINER_SID,
        local_addresses=("127.0.0.1/32",), remote_addresses=("127.0.0.1/32",),
        local_ports=(), remote_ports=(2040,),
    )
    isolation = resources.WindowsIsolationPolicy(
        service_name="ComsolHost", service_start_name="McpService",
        service_logon_sid=SERVICE_LOGON_SID, service_sid=SERVICE_SID,
        service_sid_type="RESTRICTED", appcontainer_profile_name="comsol-private-v2",
        appcontainer_sid=APPCONTAINER_SID, capability_sids=(CAPABILITY_SID,),
        acl_grants=(resources.WindowsPathAclGrant(
            ROOT, APPCONTAINER_SID, ("READ",), "a" * 64),),
        wfp_provider_key=PROVIDER_KEY, wfp_sublayer_key=SUBLAYER_KEY,
        wfp_filter_keys=(FILTER_KEY,), wfp_grants=(grant,),
        host_fingerprint_sha256="b" * 64,
    )
    return resources.HostResourceProfile(
        profile_id="profile-test", project_id=PROJECT_ID, platform="windows",
        host_id=HOST_ID,
        root=resources.PathIdentity(ROOT, "volume-1", "root-file-1", SERVICE_LOGON_SID),
        launcher_account_id="S-1-5-21-1010", service_account_id=SERVICE_LOGON_SID,
        interactive_account_id=INTERACTIVE_SID, applied_policy_id="policy-test",
        launch_images=(
            resources.LaunchImage("launcher", r"C:\COMSOL\launcher.exe", "c" * 64, "d" * 64),
            resources.LaunchImage("server", SERVER_PATH, "e" * 64, "f" * 64),
            resources.LaunchImage("worker", r"C:\COMSOL\java.exe", "1" * 64, "2" * 64),
        ),
        file_grants=(resources.PathGrant(ROOT, ("READ", "WRITE", "EXECUTE")),),
        network_grants=(resources.NetworkGrant("LOOPBACK_ENDPOINT", "127.0.0.1", 2040),),
        disk_budget=resources.DiskBudgetPolicy("ADMISSION_ONLY", 0, 1024, None),
        allowed_operations=("study.run",), windows_isolation=isolation,
    )


def _grant(profile, action, ref, plan_sha, *, phase, source_plan=None, source_receipt=None):
    scope = {
        "root": profile.root.as_dict(), "host_id": profile.host_id,
        "profile_sha256": profile.sha256, "plan_sha256": plan_sha,
        "phase": phase, "source_plan_sha256": source_plan,
        "source_receipt_sha256": source_receipt,
    }
    return {
        "project_id": profile.project_id, "action": action,
        "authorization_ref_sha256": hashlib.sha256(ref.encode()).hexdigest(),
        "scope_sha256": scope_sha256(profile.project_id, action, scope),
        "expires_at_utc": (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def _write_provider(directory: Path, profile, grants) -> HostScopedProvider:
    directory.mkdir(mode=0o700)
    os.chmod(directory, 0o700)
    path = directory / "provider.json"
    path.write_text(json.dumps({"schema": CONFIG_SCHEMA, "destinations": [], "grants": grants},
                               sort_keys=True, separators=(",", ":")), encoding="utf-8")
    os.chmod(path, 0o600)
    return HostScopedProvider.load(path)


def _plan(profile, *, kind="wfp_filter", operation="CREATE"):
    grant = profile.windows_isolation.wfp_grants[0]
    desired = {
        "filter_key": FILTER_KEY, "provider_key": PROVIDER_KEY,
        "sublayer_key": SUBLAYER_KEY, "action": "PERMIT",
        "grant_sha256": resources._sha256(grant.as_dict()),
    }
    if kind == "windows_service":
        policy = profile.windows_isolation
        desired = {"service_name": policy.service_name,
                   "service_start_name": policy.service_start_name,
                   "service_logon_sid": policy.service_logon_sid,
                   "service_sid": policy.service_sid,
                   "service_sid_type": policy.service_sid_type}
    action = {
        "action_id": "filter-server-loopback", "resource_kind": kind,
        "resource_name": "server-loopback", "operation": operation,
        "desired": desired, "baseline_identity": None, "baseline_sha256": None,
        "backup_path": None, "backup_sha256": None,
    }
    return resources.build_host_change_plan(profile, change_manifest=[action], operator_inputs={})


def _row(profile, action):
    marker = json.dumps({"schema": "comsol-mcp.t037-owned-wfp-filter/1",
                         "profile_sha256": profile.sha256,
                         "plan_sha256": action["plan_sha256"],
                         "action_id": action["action_id"],
                         "ownership_guid": action["ownership_guid"]},
                        sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    return {
        "filter_key": action["filter_key"], "provider_key": PROVIDER_KEY,
        "sublayer_key": SUBLAYER_KEY, "layer_key": LAYER_KEY,
        "flags": 1, "filter_id": 7, "action_type": 0x1002,
        "action_callout_key": None, "has_provider_context": False,
        "context": {"raw_context": 0}, "provider_data_sha256": hashlib.sha256(marker).hexdigest(),
        "display_data": {"name": "COMSOL MCP T037 " + action["action_id"], "description": None},
        "weight": {"type": 0, "value": None}, "effective_weight": {"type": 2, "value": 1},
        "conditions": [], "security_sha256": "9" * 64,
    }


class FakeWfp:
    """Synthetic API is accepted only through the private software-test seam."""
    def __init__(self, profile, action):
        self.profile = profile
        self.action = action
        self.filters = {}
        self.calls = []
        self.fail_after_create = False
        self.before_create = None
        self.provider = {"provider_key": PROVIDER_KEY, "flags": 0,
                         "security_sha256": "3" * 64, "provider_data_sha256": "4" * 64}
        self.sublayer = {"sublayer_key": SUBLAYER_KEY, "flags": 0,
                         "provider_key": PROVIDER_KEY, "security_sha256": "5" * 64,
                         "provider_data_sha256": "6" * 64}

    def observe_snapshot(self):
        providers, sublayers = [dict(self.provider)], [dict(self.sublayer)]
        filters = [dict(row) for row in self.filters.values()]
        content = {"providers": sorted(providers, key=lambda row: row["provider_key"]),
                   "sublayers": sorted(sublayers, key=lambda row: row["sublayer_key"]),
                   "filters": sorted(filters, key=lambda row: (row["layer_key"], row["filter_id"]))}
        serialized = json.dumps(content, sort_keys=True, ensure_ascii=False,
                                separators=(",", ":"), allow_nan=False).encode()
        return {
            "state": "COMPLETE_READ_ONLY",
            "counts": {"total_records": len(providers) + len(sublayers) + len(filters),
                       "total_bytes": len(serialized), "elapsed_ms": 1},
            "limits": {"max_page_size": 100, "max_pages_per_kind": 100,
                       "max_total_records": 100000, "max_total_bytes": 32 * 1024 * 1024,
                       "max_elapsed_ms": 30000},
            "snapshot_sha256": hashlib.sha256(serialized).hexdigest(),
            "reasons": ["WFP_EFFECTIVE_ARBITRATION_NOT_PROVEN"],
            "providers": providers, "sublayers": sublayers, "filters": filters,
        }

    def create_filter(self, *, profile, action, expected_dependency_sha256):
        if self.before_create is not None:
            self.before_create()
        self.calls.append("create")
        row = _row(profile, action)
        if action["filter_key"] in self.filters:
            raise RuntimeError("filter already exists")
        self.filters[action["filter_key"]] = row
        if self.fail_after_create:
            raise RuntimeError("synthetic process loss after WFP mutation")
        return dict(row)

    def delete_filter(self, *, profile, action, expected_resource_sha256,
                      expected_dependency_sha256):
        self.calls.append("delete")
        current = self.filters.get(action["filter_key"])
        if current is None or sha256(current) != expected_resource_sha256:
            raise RuntimeError("synthetic WFP drift")
        del self.filters[action["filter_key"]]


@pytest.fixture
def host_env(tmp_path, monkeypatch):
    profile = _profile()
    plan = _plan(profile)
    _, action = _normalize_supported_plan(plan, profile)
    api = FakeWfp(profile, action)
    store = OperationStore(tmp_path / "operations.sqlite")
    provider = _write_provider(tmp_path / "provider-config", profile, [])
    authority = ProjectAuthority(
        store, workspace_root=tmp_path,
        permission_provider=lambda: {"host_control", "inspect"},
        grant_ceiling={"host_control", "inspect"}, authorization_verifier=provider,
    )
    # Darwin tests cannot represent a C: workspace. Keep the production
    # authority class/store/provider checks but return the exact Windows path
    # a real project record would yield after normal authorization.
    monkeypatch.setattr(authority, "authorize_operation",
                        lambda project_id, permission: {
                            "project_id": project_id, "workspace": profile.root.path,
                            "policy": {"permissions": [permission]},
                        })
    executor = WindowsHostChangeExecutor._for_software_test(
        store=store, authority=authority, profile=profile, api=api,
        token=_SOFTWARE_TEST_TOKEN,
    )
    try:
        yield profile, plan, action, api, store, provider, authority, executor
    finally:
        provider.close()
        store.close()


def _events(store, job_id):
    rows = store.db.execute(
        "SELECT metadata FROM job_events WHERE job_id=? AND event='HostControlMutationJournalV1' ORDER BY id",
        (job_id,),
    ).fetchall()
    return [json.loads(row["metadata"]) for row in rows]


def _apply_grant(profile, plan, ref="apply-once"):
    return _grant(profile, "host.change.apply", ref, plan.sha256, phase="APPLY")


def _restore_sha(profile, plan, receipt, created_sha):
    from comsol_mcp._windows_host_change import _snapshot_dependencies
    _, action = _normalize_supported_plan(plan, profile)
    api = FakeWfp(profile, action)
    api.filters[action["filter_key"]] = _row(profile, action)
    snapshot = api.observe_snapshot()
    baseline = _snapshot_dependencies(snapshot, profile, action, require_filter_absent=False)
    payload = {"schema": "comsol-mcp.host-change-restore/1",
               "profile_sha256": profile.sha256, "source_plan_sha256": plan.sha256,
               "source_receipt_sha256": receipt.receipt_sha256,
               "filter_key": action["filter_key"],
               "expected_created_resource_sha256": created_sha,
               "dependency_sha256": baseline["dependency_sha256"]}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def test_supported_create_and_receipt_bound_restore_are_durably_journalled(host_env, tmp_path):
    profile, plan, action, api, store, provider, authority, executor = host_env
    apply_ref = "apply-once"
    provider.close()
    provider = _write_provider(tmp_path / "provider-config-apply", profile,
                               [_apply_grant(profile, plan, apply_ref)])
    authority.authorization_verifier = provider
    executor.provider = provider

    def assert_write_ahead_intent():
        admission = store.db.execute(
            "SELECT * FROM host_control_admissions WHERE phase='APPLY'"
        ).fetchone()
        assert admission is not None and admission["state"] == "APPLY_INTENT"
        journal = _events(store, admission["job_id"])
        assert len(journal) == 1 and journal[0]["event"] == "APPLY_INTENT"
        assert store.get_operation(admission["operation_id"])["status"] == "RUNNING"
        assert store.job(admission["job_id"])["status"] == "RUNNING"

    api.before_create = assert_write_ahead_intent
    receipt = executor.apply(plan, approval=resources.HostChangeApproval(plan.sha256, apply_ref))
    assert isinstance(receipt, resources.AppliedHostChangeReceipt)
    assert api.calls == ["create"] and FILTER_KEY in api.filters

    operation = store.db.execute("SELECT operation_id FROM host_control_admissions WHERE phase='APPLY'").fetchone()
    admission = store.db.execute("SELECT * FROM host_control_admissions WHERE phase='APPLY'").fetchone()
    assert admission["state"] == "APPLIED"
    journal = _events(store, admission["job_id"])
    assert [item["event"] for item in journal] == ["APPLY_INTENT", "APPLY_READBACK_VERIFIED"]
    assert journal[1]["previous_sha256"] == journal[0]["record_sha256"]
    completed = store.get_operation(operation["operation_id"])
    assert completed["status"] == "SUCCEEDED"
    assert completed["result"]["applied_receipt"] == {
        "schema": "comsol-mcp.applied-host-change-receipt/1",
        "plan_sha256": receipt.plan_sha256,
        "host_id": receipt.host_id,
        "platform": receipt.platform,
        "actions": [dict(item) for item in receipt.actions],
        "receipt_sha256": receipt.receipt_sha256,
    }

    restore_sha = _restore_sha(profile, plan, receipt,
                               receipt.actions[0]["created_resource_sha256"])
    restore_ref = "restore-once"
    provider.close()
    provider = _write_provider(tmp_path / "provider-config-restore", profile, [
        _apply_grant(profile, plan, apply_ref),
        _grant(profile, "host.change.restore", restore_ref, restore_sha,
               phase="RESTORE", source_plan=plan.sha256,
               source_receipt=receipt.receipt_sha256),
    ])
    authority.authorization_verifier = provider
    executor.provider = provider
    result = executor.restore(plan, receipt,
                              approval=resources.HostChangeApproval(restore_sha, restore_ref))
    assert result["execution_state"] == "SUCCEEDED"
    assert result["effective_policy_state"] == "UNVERIFIED"
    assert api.calls == ["create", "delete"] and FILTER_KEY not in api.filters
    restore_admission = store.db.execute(
        "SELECT * FROM host_control_admissions WHERE phase='RESTORE'").fetchone()
    assert restore_admission["state"] == "RESTORED"
    assert [item["event"] for item in _events(store, restore_admission["job_id"])] == [
        "RESTORE_INTENT", "RESTORE_READBACK_VERIFIED"]
    provider.close()


def test_readback_must_match_durable_intent_before_journal_can_advance(host_env, tmp_path):
    profile, plan, action, api, store, provider, authority, executor = host_env
    ref = "journal-once"
    provider.close()
    provider = _write_provider(tmp_path / "provider-config-journal", profile,
                               [_apply_grant(profile, plan, ref)])
    authority.authorization_verifier = provider
    executor.provider = provider

    _, _, baseline = executor._preflight(plan)
    admission = executor._admit(
        phase="APPLY", plan_sha256=plan.sha256,
        approval=resources.HostChangeApproval(plan.sha256, ref), baseline=baseline,
    )
    executor._start(admission)
    intent = {"action_id": action["action_id"], "filter_key": action["filter_key"],
              "desired_identity_sha256": action["desired_identity_sha256"],
              "dependency_sha256": baseline["dependency_sha256"]}
    store.append_host_control_mutation_event(
        admission_id=admission.admission_id, event="APPLY_INTENT", payload=intent,
    )

    with pytest.raises(resources.ResourcePolicyError) as mismatch:
        store.append_host_control_mutation_event(
            admission_id=admission.admission_id,
            event="APPLY_READBACK_VERIFIED",
            payload={**intent, "desired_identity_sha256": "0" * 64,
                     "created_resource_sha256": "1" * 64},
        )
    assert mismatch.value.code == "HOST_CONTROL_JOURNAL_INVALID"
    admission_row = store.db.execute(
        "SELECT * FROM host_control_admissions WHERE admission_id=?", (admission.admission_id,)
    ).fetchone()
    assert admission_row["state"] == "APPLY_INTENT"
    assert [item["event"] for item in _events(store, admission.job_id)] == ["APPLY_INTENT"]
    assert api.calls == []
    provider.close()


def test_tampered_complete_snapshot_refuses_before_authorization_or_mutation(host_env, tmp_path):
    profile, plan, _action, api, store, provider, authority, _executor = host_env
    ref = "snapshot-once"
    provider.close()
    provider = _write_provider(tmp_path / "provider-config-snapshot", profile,
                               [_apply_grant(profile, plan, ref)])
    authority.authorization_verifier = provider

    class TamperedSnapshot(FakeWfp):
        def observe_snapshot(self):
            snapshot = super().observe_snapshot()
            snapshot["providers"][0]["flags"] = 1
            return snapshot

    tampered = TamperedSnapshot(profile, _normalize_supported_plan(plan, profile)[1])
    executor = WindowsHostChangeExecutor._for_software_test(
        store=store, authority=authority, profile=profile, api=tampered,
        token=_SOFTWARE_TEST_TOKEN,
    )
    with pytest.raises(WindowsHostChangeError) as refusal:
        executor.apply(plan, approval=resources.HostChangeApproval(plan.sha256, ref))
    assert refusal.value.code == "HOST_CHANGE_WFP_SNAPSHOT_TAMPERED"
    assert tampered.calls == []
    assert store.db.execute("SELECT count(*) FROM host_control_admissions").fetchone()[0] == 0
    assert provider.verify_scope(PROJECT_ID, "host.change.apply",
                                 {"root": profile.root.as_dict(), "host_id": HOST_ID,
                                  "profile_sha256": profile.sha256,
                                  "plan_sha256": plan.sha256, "phase": "APPLY",
                                  "source_plan_sha256": None, "source_receipt_sha256": None}, ref) is True
    provider.close()


def test_wfp_ipv4_conditions_encode_host_order_and_readback_exactly(host_env):
    import ctypes

    from comsol_mcp import _windows_t037_native as native
    from comsol_mcp._windows_t037_wfp import (
        CtypesWindowsWfpMutationApi,
        FWP_BYTE_BLOB_TYPE,
        FWP_SID,
        FWP_UINT16,
        FWP_UINT8,
        FWPM_CONDITION_ALE_APP_ID,
        FWPM_CONDITION_ALE_PACKAGE_ID,
        FWPM_CONDITION_IP_LOCAL_ADDRESS,
        FWPM_CONDITION_IP_LOCAL_PORT,
        FWPM_CONDITION_IP_PROTOCOL,
        FWPM_CONDITION_IP_REMOTE_ADDRESS,
        FWPM_CONDITION_IP_REMOTE_PORT,
        WindowsWfpMutationError,
        guid_key,
    )

    # The WFP SDK GUID must match the documented FWPM_CONDITION_ALE_APP_ID
    # symbol. Keep a literal assertion so changing the module constant cannot
    # make a malformed GUID self-consistent in this fixture.
    assert FWPM_CONDITION_ALE_APP_ID == "{D78E1E87-8644-4EA5-9437-D809ECEFC971}"
    assert FWPM_CONDITION_ALE_PACKAGE_ID == "{71BC78FA-F17C-4997-A602-6ABB261F351C}"

    profile, plan, action, _api, _store, _provider, _authority, _executor = host_env
    grant = action["grant"]
    adapter = object.__new__(CtypesWindowsWfpMutationApi)
    app_condition = adapter._condition(FWPM_CONDITION_ALE_APP_ID, FWP_BYTE_BLOB_TYPE)
    package_condition = adapter._condition(FWPM_CONDITION_ALE_PACKAGE_ID, FWP_SID)
    package_condition.conditionValue.sid = ctypes.c_void_p(0x1234)
    assert package_condition.conditionValue.type == FWP_SID
    assert package_condition.conditionValue.sid == 0x1234
    conditions, backing = adapter._conditions_for(grant, app_condition, package_condition)
    assert backing

    by_key = {
        guid_key(str(uuid.UUID(bytes_le=ctypes.string_at(
            ctypes.byref(item.fieldKey), ctypes.sizeof(native.GUID))))): item
        for item in conditions
    }
    assert len(by_key) == len(conditions)
    assert set(by_key) == {
        guid_key(FWPM_CONDITION_ALE_APP_ID), guid_key(FWPM_CONDITION_ALE_PACKAGE_ID),
        guid_key(FWPM_CONDITION_IP_LOCAL_ADDRESS), guid_key(FWPM_CONDITION_IP_REMOTE_ADDRESS),
        guid_key(FWPM_CONDITION_IP_PROTOCOL), guid_key(FWPM_CONDITION_IP_REMOTE_PORT),
    }
    local_address = ctypes.cast(
        by_key[guid_key(FWPM_CONDITION_IP_LOCAL_ADDRESS)].conditionValue.v4AddrMask,
        ctypes.POINTER(native.FWP_V4_ADDR_AND_MASK),
    ).contents
    remote_address = ctypes.cast(
        by_key[guid_key(FWPM_CONDITION_IP_REMOTE_ADDRESS)].conditionValue.v4AddrMask,
        ctypes.POINTER(native.FWP_V4_ADDR_AND_MASK),
    ).contents
    assert (local_address.addr, local_address.mask) == (0x7F000001, 0xFFFFFFFF)
    assert (remote_address.addr, remote_address.mask) == (0x7F000001, 0xFFFFFFFF)
    assert by_key[guid_key(FWPM_CONDITION_IP_PROTOCOL)].conditionValue.type == FWP_UINT8
    assert by_key[guid_key(FWPM_CONDITION_IP_PROTOCOL)].conditionValue.uint8 == 6
    assert by_key[guid_key(FWPM_CONDITION_IP_REMOTE_PORT)].conditionValue.type == FWP_UINT16
    assert by_key[guid_key(FWPM_CONDITION_IP_REMOTE_PORT)].conditionValue.uint16 == 2040
    expected = adapter._expected_conditions(grant, None, APPCONTAINER_SID)
    assert guid_key(FWPM_CONDITION_IP_LOCAL_PORT) not in {
        guid_key(item["field_key"]) for item in expected
    }
    assert {"field_key": guid_key(FWPM_CONDITION_ALE_PACKAGE_ID),
            "condition_value": {"type": FWP_SID, "value": APPCONTAINER_SID}} in [
        {"field_key": guid_key(item["field_key"]),
         "condition_value": item["condition_value"]} for item in expected
    ]

    marker = json.dumps({"schema": "comsol-mcp.t037-owned-wfp-filter/1",
                         "profile_sha256": profile.sha256,
                         "plan_sha256": action["plan_sha256"],
                         "action_id": action["action_id"],
                         "ownership_guid": action["ownership_guid"]},
                        sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    app_blob, package_sid = b"exact app id", APPCONTAINER_SID
    row = _row(profile, action)
    row["conditions"] = adapter._expected_conditions(grant, app_blob, package_sid)
    adapter._validate_filter_semantics(row, profile, action, grant, marker,
                                       expected_app_blob=app_blob, expected_package_sid=package_sid)
    row["conditions"][-1]["condition_value"]["value"] = 2041
    with pytest.raises(WindowsWfpMutationError) as mismatch:
        adapter._validate_filter_semantics(row, profile, action, grant, marker,
                                           expected_app_blob=app_blob, expected_package_sid=package_sid)
    assert mismatch.value.code == "WFP_FILTER_READBACK_MISMATCH"


def test_unsupported_action_set_refuses_before_provider_consumption_or_native_call(host_env):
    profile, _plan_value, _action, api, store, provider, authority, _executor = host_env
    unsupported = _plan(profile, kind="windows_service")
    executor = WindowsHostChangeExecutor._for_software_test(
        store=store, authority=authority, profile=profile, api=api,
        token=_SOFTWARE_TEST_TOKEN,
    )
    with pytest.raises(WindowsHostChangeError) as refusal:
        executor.apply(unsupported,
                       approval=resources.HostChangeApproval(unsupported.sha256, "unused-ref"))
    assert refusal.value.code == "HOST_CHANGE_ACTION_UNSUPPORTED"
    assert api.calls == []
    assert store.db.execute("SELECT count(*) FROM host_control_admissions").fetchone()[0] == 0
    assert provider.verify_scope(PROJECT_ID, "host.change.apply",
                                 {"root": profile.root.as_dict(), "host_id": HOST_ID,
                                  "profile_sha256": profile.sha256,
                                  "plan_sha256": unsupported.sha256, "phase": "APPLY",
                                  "source_plan_sha256": None, "source_receipt_sha256": None},
                                 "unused-ref") is False


def test_crash_after_create_records_unknown_and_unique_grant_prevents_replay(host_env, tmp_path):
    profile, plan, _action, api, store, provider, authority, _executor = host_env
    ref = "crash-once"
    provider.close()
    provider = _write_provider(tmp_path / "provider-config-crash", profile,
                               [_apply_grant(profile, plan, ref)])
    authority.authorization_verifier = provider
    executor = WindowsHostChangeExecutor._for_software_test(
        store=store, authority=authority, profile=profile, api=api,
        token=_SOFTWARE_TEST_TOKEN,
    )
    api.fail_after_create = True
    with pytest.raises(WindowsHostChangeError) as unknown:
        executor.apply(plan, approval=resources.HostChangeApproval(plan.sha256, ref))
    assert unknown.value.code == "HOST_CHANGE_RECOVERY_REQUIRED"
    assert unknown.value.safe_retry is False
    admission = store.db.execute("SELECT * FROM host_control_admissions").fetchone()
    assert admission["state"] == "RECOVERY_REQUIRED"
    assert store.get_operation(admission["operation_id"])["status"] == "UNKNOWN"
    assert FILTER_KEY in api.filters
    assert [item["event"] for item in _events(store, admission["job_id"])] == [
        "APPLY_INTENT", "RECOVERY_REQUIRED"]
    with pytest.raises(resources.ResourcePolicyError) as replay:
        # Model a subsequent administrator cleanup restoring the exact
        # pre-apply baseline. The consumed provider grant must still not
        # authorize another CREATE.
        api.filters.clear()
        executor.apply(plan, approval=resources.HostChangeApproval(plan.sha256, ref))
    assert replay.value.code == "HOST_CONTROL_AUTHORIZATION_REPLAYED"
    assert api.calls == ["create"]
    provider.close()


def test_restore_preserves_changed_foreign_filter_before_consuming_authorization(host_env):
    profile, plan, action, api, store, provider, authority, executor = host_env
    row = _row(profile, action)
    actions = ({"action_id": action["action_id"],
                "created_resource_sha256": sha256(row),
                "prior_resource_sha256": None, "prior_backup_path": None},)
    body = {"schema": "comsol-mcp.applied-host-change-receipt/1",
            "plan_sha256": plan.sha256, "host_id": profile.host_id,
            "platform": "windows", "actions": [dict(item) for item in actions]}
    receipt_sha = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False,
                                            separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    receipt = resources.AppliedHostChangeReceipt(plan.sha256, profile.host_id,
                                                "windows", actions, receipt_sha)
    api.filters[FILTER_KEY] = {**row, "display_data": {"name": "foreign", "description": None}}
    with pytest.raises(WindowsHostChangeError) as drift:
        executor.restore(plan, receipt,
                         approval=resources.HostChangeApproval("0" * 64, "unused"))
    assert drift.value.code == "HOST_CHANGE_RESTORE_DRIFT"
    assert FILTER_KEY in api.filters and api.calls == []
    assert store.db.execute("SELECT count(*) FROM host_control_admissions").fetchone()[0] == 0


def test_native_adapter_and_trusted_profile_loader_refuse_on_non_windows(tmp_path):
    from comsol_mcp._windows_t037_wfp import CtypesWindowsWfpMutationApi, WindowsWfpMutationError

    if os.name == "nt":
        pytest.skip("native Windows construction is outside this software-only test")
    with pytest.raises(WindowsWfpMutationError) as native:
        CtypesWindowsWfpMutationApi()
    assert native.value.code == "WFP_NATIVE_UNSUPPORTED"
    profile = _profile()
    provider = _write_provider(tmp_path / "native-provider", profile, [])
    store = OperationStore(tmp_path / "native.sqlite")
    authority = ProjectAuthority(store, workspace_root=tmp_path,
                                 permission_provider=lambda: {"host_control"},
                                 grant_ceiling={"host_control"},
                                 authorization_verifier=provider)
    with pytest.raises(WindowsHostChangeError) as host:
        WindowsHostChangeExecutor.from_trusted_profile_path(
            store=store, authority=authority,
            trusted_profile_path=str(tmp_path / "not-used.json"))
    assert host.value.code == "HOST_CHANGE_WINDOWS_REQUIRED"
    provider.close()
    store.close()
