from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import threading

import pytest

from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._operation_store import OperationStore
from comsol_mcp._project_authority import ProjectAuthority
from comsol_mcp import _w26_os_resources as resources


def _profile(project: Path, *, mode="ADMISSION_ONLY", max_task=1000):
    root_info = project.stat()
    root = resources.PathIdentity(str(project), str(root_info.st_dev), str(root_info.st_ino), str(root_info.st_uid))
    images = (
        resources.LaunchImage("launcher", "/opt/comsol/launcher", "a" * 64, "b" * 64),
        resources.LaunchImage("server", "/opt/comsol/server", "c" * 64, "d" * 64),
        resources.LaunchImage("worker", "/opt/jdk/bin/java", "e" * 64, "f" * 64),
    )
    budget = resources.DiskBudgetPolicy(mode, 10, max_task, "quota-test" if mode == "REQUIRE_HARD_QUOTA" else None)
    return resources.HostResourceProfile(
        "profile-1", "project-1", "macos", "host-test", root,
        "uid-launcher", "uid-service", "uid-user", "policy-1", images,
        (resources.PathGrant(str(project), ("READ", "WRITE", "EXECUTE")),),
        (resources.NetworkGrant("LOOPBACK_ENDPOINT", "127.0.0.1", 2040),),
        budget, ("study.run", "mesh.convergence_study"),
    )


def _storage(profile, *, now=1_000, free=500, quota="ADMISSION_ONLY", attested=False):
    root = profile.root
    native_attestation = resources._NATIVE_STORAGE_ATTESTATION if attested else None
    return resources.StorageObservation(
        "fs-" + root.volume_id, root.path, root.volume_id, root.file_id,
        free, max(free, 1000), quota,
        "quota-test" if quota == "VERIFIED_HARD_QUOTA" else None,
        "project-root" if quota == "VERIFIED_HARD_QUOTA" else None,
        300 if quota == "VERIFIED_HARD_QUOTA" else None,
        50 if quota == "VERIFIED_HARD_QUOTA" else None,
        now - 1, native_attestation,
    )


def _authority(tmp_path: Path, *, auth_verifier=None):
    workspace_root = tmp_path / "authorized"
    workspace_root.mkdir()
    store = OperationStore(tmp_path / "operations.sqlite")
    host_permissions = {"inspect", "project_write", "compute", "host_control"}
    authority = ProjectAuthority(
        store, workspace_root=workspace_root,
        permission_provider=lambda: host_permissions,
        grant_ceiling=host_permissions,
        authorization_verifier=auth_verifier,
    )
    project = authority.dispatch("project.create", {
        "label": "W26 resource fixture", "workspace": "project-one",
        "policy": {"permissions": ["inspect", "compute", "host_control"]},
    })["data"]["project"]
    return store, authority, Path(project["workspace"]), project["project_id"]


def _queued_job(store, project_id: str, project: Path, *, operation="study.run"):
    root = project.stat()
    metadata = {"project_id": project_id, "project_root_identity": str(root.st_ino),
                "operation": operation}
    record, reused = store.begin(
        request_id="request-" + os.urandom(3).hex(),
        idempotency_key="key-" + os.urandom(5).hex(),
        request_hash=hashlib.sha256(os.urandom(16)).hexdigest(),
        operation=operation, metadata=metadata,
    )
    assert not reused
    return record


def _complete_snapshot(profile, *, now=1000):
    launcher, server, worker = profile.launch_images
    processes = (
        resources.ProcessWitness("launcher", 10, "birth-10", launcher.path, launcher.image_sha256,
                                 launcher.argv_sha256, profile.launcher_account_id, None, None, None),
        resources.ProcessWitness("server", 11, "birth-11", server.path, server.image_sha256,
                                 server.argv_sha256, profile.service_account_id, profile.applied_policy_id,
                                 10, "birth-10"),
        resources.ProcessWitness("worker", 12, "birth-12", worker.path, worker.image_sha256,
                                 worker.argv_sha256, profile.service_account_id, profile.applied_policy_id,
                                 11, "birth-11"),
    )
    return resources.HostResourceSnapshot(
        profile.sha256, profile.platform, profile.host_id, profile.project_id, profile.root,
        profile.launcher_account_id, profile.service_account_id, profile.interactive_account_id,
        profile.applied_policy_id, "COMPLETE", "COMPLETE", "COMPLETE", processes,
        profile.file_grants, profile.network_grants, _storage(profile, now=now), now,
    )


def _action(action_id="create-cache", *, kind="file_acl", resource="cache-acl",
            operation="CREATE", desired=None, baseline_identity=None,
            baseline_sha256=None, backup_path=None, backup_sha256=None):
    return {"action_id": action_id, "resource_kind": kind, "resource_name": resource,
            "operation": operation,
            "desired": desired or {"path": "/tmp/project/cache", "rights": ["READ", "WRITE"]},
            "baseline_identity": baseline_identity, "baseline_sha256": baseline_sha256,
            "backup_path": backup_path, "backup_sha256": backup_sha256}


def test_snapshot_checks_exact_process_tree_and_never_upgrades_fixture_to_native_pass(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    profile = _profile(project)
    assessment = resources.assess_snapshot(profile, _complete_snapshot(profile), now_monotonic_ns=1000)
    assert assessment.structurally_consistent is True
    assert assessment.capability_state is resources.CapabilityState.IMPLEMENTED_UNVERIFIED
    assert assessment.snapshot_sha256 and len(assessment.snapshot_sha256) == 64

    broken = _complete_snapshot(profile)
    bad_worker = replace(broken.processes[2], policy_id="other-policy")
    assessment = resources.assess_snapshot(
        profile, replace(broken, processes=(*broken.processes[:2], bad_worker)), now_monotonic_ns=1000)
    assert "PROCESS_POLICY_INHERITANCE_MISSING" in assessment.reasons
    assert assessment.capability_state is resources.CapabilityState.IMPLEMENTED_UNVERIFIED


def test_profile_gate_rejects_request_claims_missing_provider_and_synthetic_native_provider(tmp_path):
    store, authority, project, project_id = _authority(tmp_path)
    profile = replace(_profile(project), project_id=project_id)

    with pytest.raises(resources.ResourcePolicyError) as caller_claim:
        resources.require_profile_before_dispatch(
            profile=profile, provider=None, authority=authority, project_id=project_id,
            request_claims={"os_sandbox": True}, now_monotonic_ns=1000)
    assert caller_claim.value.code == "CALLER_SANDBOX_CLAIM_FORBIDDEN"

    with pytest.raises(resources.ResourcePolicyError) as missing:
        resources.require_profile_before_dispatch(
            profile=profile, provider=None, authority=authority, project_id=project_id,
            now_monotonic_ns=1000)
    assert missing.value.code == "OS_RESOURCE_PROVIDER_NOT_CONFIGURED"

    class SyntheticProvider(resources.NativeHostResourceProvider):
        def read_snapshot(self, _profile):
            return _complete_snapshot(profile)

    with pytest.raises(resources.ResourcePolicyError) as synthetic:
        resources.require_profile_before_dispatch(
            profile=profile, provider=SyntheticProvider(), authority=authority,
            project_id=project_id, now_monotonic_ns=1000)
    assert synthetic.value.code == "OS_RESOURCE_NATIVE_PROOF_UNAVAILABLE"
    assert "os_sandbox" not in json.dumps(synthetic.value.details)
    store.close()


@pytest.mark.parametrize("requested", [0, -1, True, 1 << 100])
def test_disk_admission_rejects_invalid_or_overflowing_requested_bytes(tmp_path, requested):
    project = tmp_path / "project"
    project.mkdir()
    profile = _profile(project)
    with pytest.raises(resources.ResourcePolicyError) as refusal:
        resources.evaluate_disk_admission(profile, _storage(profile), requested,
                                          active_reserved_bytes=0, now_monotonic_ns=1000)
    assert refusal.value.code == "INVALID_DISK_RESERVATION"


def test_disk_admission_rejects_active_reservation_integer_overflow(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    profile = _profile(project)
    with pytest.raises(resources.ResourcePolicyError) as refusal:
        resources.evaluate_disk_admission(
            profile, _storage(profile), 1, active_reserved_bytes=resources._MAX_SQLITE_INT + 1,
            now_monotonic_ns=1000)
    assert refusal.value.code == "RESOURCE_RESERVATION_STATE_UNKNOWN"


def test_disk_admission_is_advisory_and_checks_fresh_root_bound_capacity(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    profile = _profile(project)
    decision = resources.evaluate_disk_admission(profile, _storage(profile, free=60), 40,
                                                 active_reserved_bytes=10, now_monotonic_ns=1000)
    assert decision.accepted is True
    assert decision.available_bytes == 40
    assert decision.enforcement == "ADMISSION_ONLY"
    assert decision.evidence_classification == "SOFTWARE_ONLY"

    stale = _storage(profile, now=0)
    with pytest.raises(resources.ResourcePolicyError) as expired:
        resources.evaluate_disk_admission(profile, stale, 1, active_reserved_bytes=0,
                                          now_monotonic_ns=40_000_000_000)
    assert expired.value.code == "DISK_OBSERVATION_UNVERIFIED"

    changed = replace(_storage(profile), root_file_id="foreign-root")
    with pytest.raises(resources.ResourcePolicyError) as wrong_root:
        resources.evaluate_disk_admission(profile, changed, 1, active_reserved_bytes=0,
                                          now_monotonic_ns=1000)
    assert wrong_root.value.code == "DISK_OBSERVATION_UNVERIFIED"


def test_claimed_hard_quota_cannot_pass_without_native_attestation(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    profile = _profile(project, mode="REQUIRE_HARD_QUOTA")
    observation = _storage(profile, quota="VERIFIED_HARD_QUOTA", attested=False)
    with pytest.raises(resources.ResourcePolicyError) as refusal:
        resources.evaluate_disk_admission(profile, observation, 10,
                                          active_reserved_bytes=0, now_monotonic_ns=1000)
    assert refusal.value.code == "DISK_OBSERVATION_UNVERIFIED"
    assert "HARD_QUOTA_NATIVE_PROVIDER_NOT_CONFIGURED" in refusal.value.details["reasons"]


def test_reservation_is_durable_idempotent_and_unknown_state_retains_bytes(tmp_path):
    store, authority, project, project_id = _authority(tmp_path)
    profile = replace(_profile(project), project_id=project_id)
    observation = _storage(profile, free=100)
    first = _queued_job(store, project_id, project)
    held = resources.reserve_disk_for_job(store=store, authority=authority, profile=profile,
                                          job_id=first["job_id"], requested_bytes=70,
                                          observation=observation, now_monotonic_ns=1000)
    duplicate = resources.reserve_disk_for_job(store=store, authority=authority, profile=profile,
                                               job_id=first["job_id"], requested_bytes=70,
                                               observation=observation, now_monotonic_ns=1000)
    assert held["status"] == "RESERVED" and held["idempotent"] is False
    assert duplicate["idempotent"] is True
    assert held["evidence_classification"] == "SOFTWARE_ONLY"

    store.finish(first["operation_id"], status="UNKNOWN", result={"status": "UNKNOWN"})
    with pytest.raises(resources.ResourcePolicyError) as unknown:
        resources.release_disk_for_job(store=store, authority=authority, profile=profile,
                                       job_id=first["job_id"])
    assert unknown.value.code == "RESOURCE_RESERVATION_UNCONFIRMED"
    second = _queued_job(store, project_id, project)
    with pytest.raises(resources.ResourcePolicyError) as overcommit:
        resources.reserve_disk_for_job(store=store, authority=authority, profile=profile,
                                       job_id=second["job_id"], requested_bytes=40,
                                       observation=observation, now_monotonic_ns=1000)
    assert overcommit.value.code == "INSUFFICIENT_DISK_BUDGET"
    store.close()


def test_unknown_reservation_survives_store_restart_and_blocks_new_admission(tmp_path):
    store, authority, project, project_id = _authority(tmp_path)
    db_path = tmp_path / "operations.sqlite"
    profile = replace(_profile(project), project_id=project_id)
    observation = _storage(profile, free=100)
    first = _queued_job(store, project_id, project)
    resources.reserve_disk_for_job(store=store, authority=authority, profile=profile,
                                   job_id=first["job_id"], requested_bytes=70,
                                   observation=observation, now_monotonic_ns=1000)
    store.finish(first["operation_id"], status="UNKNOWN", result={"status": "UNKNOWN", "safe_retry": False})
    store.close()

    reopened = OperationStore(db_path)
    workspace_root = tmp_path / "authorized"
    host_permissions = {"inspect", "project_write", "compute", "host_control"}
    authority2 = ProjectAuthority(reopened, workspace_root=workspace_root,
                                  permission_provider=lambda: host_permissions,
                                  grant_ceiling=host_permissions)
    second = _queued_job(reopened, project_id, project)
    with pytest.raises(resources.ResourcePolicyError) as blocked:
        resources.reserve_disk_for_job(store=reopened, authority=authority2, profile=profile,
                                       job_id=second["job_id"], requested_bytes=40,
                                       observation=observation, now_monotonic_ns=1000)
    assert blocked.value.code == "INSUFFICIENT_DISK_BUDGET"
    status = reopened.db.execute("SELECT status FROM jobs WHERE job_id=?", (first["job_id"],)).fetchone()[0]
    assert status == "UNKNOWN"
    reopened.close()


def test_reservation_rejects_profile_root_inode_change_before_event_insert(tmp_path):
    store, authority, project, project_id = _authority(tmp_path)
    profile = replace(_profile(project), project_id=project_id)
    changed_root = replace(profile.root, file_id=str(int(profile.root.file_id) + 1))
    changed_profile = replace(profile, root=changed_root)
    record = _queued_job(store, project_id, project)
    with pytest.raises(resources.ResourcePolicyError) as changed:
        resources.reserve_disk_for_job(store=store, authority=authority, profile=changed_profile,
                                       job_id=record["job_id"], requested_bytes=1,
                                       observation=_storage(changed_profile), now_monotonic_ns=1000)
    assert changed.value.code == "RESOURCE_RESERVATION_ROOT_IDENTITY_MISMATCH"
    assert store.db.execute(
        "SELECT COUNT(*) FROM job_events WHERE job_id=? AND event='W26DiskReservation'",
        (record["job_id"],)).fetchone()[0] == 0
    store.close()


def test_reservation_terminal_release_requires_matching_operation_state_and_is_idempotent(tmp_path):
    store, authority, project, project_id = _authority(tmp_path)
    profile = replace(_profile(project), project_id=project_id)
    record = _queued_job(store, project_id, project)
    resources.reserve_disk_for_job(store=store, authority=authority, profile=profile,
                                   job_id=record["job_id"], requested_bytes=25,
                                   observation=_storage(profile), now_monotonic_ns=1000)
    store.update_job(record["job_id"], "FAILED")
    # Deliberately force a persisted job/operation mismatch; release stays held.
    with store.lock:
        store.db.execute("UPDATE operations SET status='UNKNOWN' WHERE operation_id=?", (record["operation_id"],))
    with pytest.raises(resources.ResourcePolicyError) as mismatch:
        resources.release_disk_for_job(store=store, authority=authority, profile=profile,
                                       job_id=record["job_id"])
    assert mismatch.value.code == "RESOURCE_RESERVATION_UNCONFIRMED"
    store.finish(record["operation_id"], status="FAILED", result={"status": "FAILED"})
    released = resources.release_disk_for_job(store=store, authority=authority, profile=profile,
                                              job_id=record["job_id"])
    repeated = resources.release_disk_for_job(store=store, authority=authority, profile=profile,
                                              job_id=record["job_id"])
    assert released["status"] == repeated["status"] == "RELEASED"
    assert released["idempotent"] is False and repeated["idempotent"] is True
    store.close()


def test_expired_status_alone_does_not_release_disk_reservation(tmp_path):
    store, authority, project, project_id = _authority(tmp_path)
    profile = replace(_profile(project), project_id=project_id)
    record = _queued_job(store, project_id, project)
    resources.reserve_disk_for_job(store=store, authority=authority, profile=profile,
                                   job_id=record["job_id"], requested_bytes=25,
                                   observation=_storage(profile), now_monotonic_ns=1000)
    store.update_job(record["job_id"], "EXPIRED")
    with pytest.raises(resources.ResourcePolicyError) as held:
        resources.release_disk_for_job(store=store, authority=authority, profile=profile,
                                       job_id=record["job_id"])
    assert held.value.code == "RESOURCE_RESERVATION_UNCONFIRMED"
    store.close()


def test_concurrent_job_reservations_serialize_on_existing_operation_store(tmp_path):
    store, authority, project, project_id = _authority(tmp_path)
    profile = replace(_profile(project, max_task=80), project_id=project_id)
    observation = _storage(profile, free=100)
    jobs = [_queued_job(store, project_id, project) for _ in range(2)]
    results = []
    barrier = threading.Barrier(2)

    def reserve(record):
        barrier.wait()
        try:
            result = resources.reserve_disk_for_job(
                store=store, authority=authority, profile=profile, job_id=record["job_id"],
                requested_bytes=60, observation=observation, now_monotonic_ns=1000)
            results.append(("RESERVED", result))
        except resources.ResourcePolicyError as exc:
            results.append((exc.code, None))

    threads = [threading.Thread(target=reserve, args=(record,)) for record in jobs]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    assert all(not thread.is_alive() for thread in threads)
    assert sorted(item[0] for item in results) == ["INSUFFICIENT_DISK_BUDGET", "RESERVED"]
    store.close()


def test_host_change_plan_is_exact_fingerprinted_plan_only_and_rejects_secrets_or_escape(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    profile = _profile(project)
    action = _action(desired={"path": str(project / "cache"), "rights": ["READ", "WRITE"]})
    plan = resources.build_host_change_plan(profile, change_manifest=[action],
                                            operator_inputs={"profile_choice": "private-compute-v1"})
    payload = resources.validate_host_change_plan(plan)
    assert payload["plan_status"] == "PLAN_ONLY_UNVERIFIED"
    assert payload["native_baseline_status"] == "NOT_CAPTURED"
    assert payload["evidence_classification"] == "SOFTWARE_ONLY"
    assert payload["launch_image_bindings"] == [item.as_dict() for item in profile.launch_images]
    assert payload["change_manifest"][0]["baseline_evidence_state"] == "NOT_CAPTURED"
    assert resources.verify_host_change_plan(plan).status == "NOT_CONFIGURED"

    tampered = resources.HostChangePlan(plan.payload_json + " ", plan.sha256)
    with pytest.raises(resources.ResourcePolicyError) as changed:
        resources.validate_host_change_plan(tampered)
    assert changed.value.code == "HOST_PLAN_TAMPERED"
    with pytest.raises(ValueError, match="credentials"):
        resources.build_host_change_plan(profile, change_manifest=[action],
                                         operator_inputs={"api_token": "must not persist"})
    with pytest.raises(ValueError, match="inside the exact project root"):
        resources.build_host_change_plan(profile, change_manifest=[_action(desired={
            "path": "/outside/private", "rights": ["READ", "WRITE"]})], operator_inputs={})


def test_host_apply_requires_plan_bound_explicit_authority_then_fails_without_adapter(tmp_path):
    verifier_calls = []
    store, authority, project, project_id = _authority(
        tmp_path, auth_verifier=lambda pid, ref: verifier_calls.append((pid, ref)) or ref == "user-approved")
    profile = replace(_profile(project), project_id=project_id)
    plan = resources.build_host_change_plan(
        profile, change_manifest=[_action(desired={"path": str(project / "scratch"), "rights": ["READ", "WRITE"]})],
        operator_inputs={"owner_selected_profile": "private-compute-v1"})
    bad = resources.HostChangeApproval("0" * 64, "user-approved")
    with pytest.raises(resources.ResourcePolicyError) as mismatch:
        resources.apply_host_change_plan(plan, authority=authority, approval=bad)
    assert mismatch.value.code == "HOST_PLAN_APPROVAL_MISMATCH"
    assert verifier_calls == []

    approved = resources.HostChangeApproval(plan.sha256, "user-approved")
    with pytest.raises(resources.ResourcePolicyError) as absent:
        resources.apply_host_change_plan(plan, authority=authority, approval=approved)
    assert absent.value.code == "HOST_CHANGE_ADAPTER_NOT_IMPLEMENTED"
    assert verifier_calls == [(project_id, "user-approved")]
    assert list(project.iterdir()) == []
    store.close()


def test_restore_plan_preserves_changed_foreign_policy_and_has_no_mutation_fallback(tmp_path):
    store, authority, project, project_id = _authority(
        tmp_path, auth_verifier=lambda _pid, _ref: True)
    profile = replace(_profile(project), project_id=project_id)
    update = _action(
        "update-acl", resource="project-acl", operation="UPDATE",
        desired={"path": str(project / "cache"), "rights": ["READ", "WRITE"]},
        baseline_identity={"file_id": "acl-id-1", "volume_id": "vol-1"},
        baseline_sha256="1" * 64, backup_path=str(project / "backup" / "acl.json"),
        backup_sha256="2" * 64)
    plan = resources.build_host_change_plan(
        profile, change_manifest=[update], operator_inputs={"profile_choice": "private-compute-v1"},
        backup_manifest=[{"resource_name": "project-acl", "path": str(project / "backup" / "acl.json"),
                          "sha256": "2" * 64, "identity": {"file_id": "backup-1"}}])
    receipt = resources.AppliedHostChangeReceipt(
        plan.sha256, profile.host_id, profile.platform,
        ({"action_id": "update-acl", "created_resource_sha256": "3" * 64,
          "prior_resource_sha256": "1" * 64, "prior_backup_path": str(project / "backup" / "acl.json")},),
        "4" * 64)
    changed = resources.build_restore_plan(plan, receipt,
                                           current_resource_sha256={"update-acl": "5" * 64})
    assert changed["status"] == "RECOVERY_REQUIRED"
    assert changed["rollback_actions"] == []
    assert changed["preserved_foreign_or_changed_resources"] == ["update-acl"]

    unchanged = resources.build_restore_plan(plan, receipt,
                                             current_resource_sha256={"update-acl": "3" * 64})
    assert unchanged["status"] == "READY_UNVERIFIED"
    assert unchanged["rollback_actions"][0]["restore_prior_sha256"] == "1" * 64
    wrong_approval = resources.HostChangeApproval(plan.sha256, "approved")
    with pytest.raises(resources.ResourcePolicyError) as refusal:
        resources.apply_restore_plan(unchanged, authority=authority, approval=wrong_approval)
    assert refusal.value.code == "HOST_PLAN_APPROVAL_MISMATCH"
    approval = resources.HostChangeApproval(unchanged["sha256"], "approved")
    with pytest.raises(resources.ResourcePolicyError) as no_adapter:
        resources.apply_restore_plan(unchanged, authority=authority, approval=approval)
    assert no_adapter.value.code == "HOST_RESTORE_ADAPTER_NOT_IMPLEMENTED"
    assert list(project.iterdir()) == []
    store.close()
