from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import socket
import threading
import time
from contextlib import contextmanager

import pytest

from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._execution_contract import SessionLedger, model_ref_from_mapping
from comsol_mcp._execution_service import ExecutionService
from comsol_mcp._control_daemon import ControlDaemon
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


def _disk_profile(project_id: str, root_path: Path, *, operation="study.run",
                  estimate=64 * 1024, reserve_free=0, max_task=1024 * 1024,
                  storage_id=None, root_identity=None):
    root_info = root_path.stat()
    root = root_identity or resources.PathIdentity(
        str(root_path), str(root_info.st_dev), str(root_info.st_ino), str(root_info.st_uid))
    storage_id = storage_id or resources.PosixStorageObservationProvider.current_storage_id(
        str(root_path), root)
    return resources.DiskResourceProfile(
        "disk-profile-1", project_id, resources._current_platform_name(), resources._current_host_id(),
        root, storage_id,
        resources.DiskBudgetPolicy("ADMISSION_ONLY", reserve_free, max_task, None),
        (operation,), (resources.DiskOperationEstimate(operation, estimate),))


def _write_disk_config(tmp_path: Path, profile: resources.DiskResourceProfile, *,
                       host_id=None, platform=None, duplicate_key=False):
    profile_raw = {
        "profile_id": profile.profile_id,
        "project_id": profile.project_id,
        "root": profile.root.as_dict(),
        "storage_id": profile.storage_id,
        "disk_budget": profile.disk_budget.as_dict(),
        "allowed_operations": list(profile.allowed_operations),
        "trusted_estimates": [item.as_dict() for item in profile.estimates],
    }
    config = {"schema": resources.DISK_CONFIG_SCHEMA,
              "host_id": host_id if host_id is not None else resources._current_host_id(),
              "platform": platform if platform is not None else resources._current_platform_name(),
              "profiles": [profile_raw]}
    directory = tmp_path / "disk-startup-config"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    path = directory / "profile.json"
    if duplicate_key:
        raw = json.dumps(config, separators=(",", ":"))
        raw = raw.replace('"schema":"' + resources.DISK_CONFIG_SCHEMA + '"',
                          '"schema":"' + resources.DISK_CONFIG_SCHEMA + '","schema":"' + resources.DISK_CONFIG_SCHEMA + '"',
                          1)
        path.write_text(raw, encoding="utf-8")
    else:
        path.write_text(json.dumps(config, sort_keys=True), encoding="utf-8")
    path.chmod(0o600)
    return path


def _queued_dispatch_job(store, project_id: str, *, inner="study.run", wrapper="operation_call"):
    metadata = {
        "operation": wrapper,
        "arguments": {"operation_id": inner, "arguments": {}},
        "execution": {"project_id": project_id, "request_id": "request-" + os.urandom(3).hex()},
    }
    record, reused = store.begin(
        request_id="request-" + os.urandom(3).hex(),
        idempotency_key="key-" + os.urandom(5).hex(),
        request_hash=hashlib.sha256(os.urandom(16)).hexdigest(),
        operation=wrapper, metadata=metadata,
    )
    assert not reused
    return record


def _make_coordinator(tmp_path, store, authority, project, project_id, *, operation="study.run",
                      estimate=64 * 1024, reserve_free=0, max_task=1024 * 1024):
    profile = _disk_profile(project_id, project, operation=operation, estimate=estimate,
                            reserve_free=reserve_free, max_task=max_task)
    config_path = _write_disk_config(tmp_path, profile)
    config = resources.TrustedDiskResourceConfig.load(config_path)
    return resources.DiskResourceCoordinator(config=config, store=store, authority=authority), config


class _SyntheticQuiescenceProvider(resources.NativeJobQuiescenceProvider):
    """Protocol fixture only; its fields are not native OS evidence."""

    def __init__(self, *, wrong_job=False, stale=False):
        self.wrong_job = wrong_job
        self.stale = stale

    def prove_quiescence(self, *, store, profile, job_binding, reservation_id):
        job_id = "another-job" if self.wrong_job else job_binding["job_id"]
        observed = time.monotonic_ns() - (60_000_000_000 if self.stale else 1)
        watermark, ledger_digest = resources._job_ledger_snapshot(store.db, job_id)
        return resources._issue_native_quiescence_witness(
            schema=resources.QUIESCENCE_SCHEMA,
            provider_id="synthetic-protocol-test-only",
            profile_id=profile.profile_id,
            profile_sha256=profile.sha256,
            project_id=profile.project_id,
            root_file_id=profile.root.file_id,
            host_id=profile.host_id,
            job_id=job_id,
            operation_id=job_binding["operation_id"],
            reservation_id=reservation_id,
            domain_status="UNKNOWN",
            safe_retry=False,
            process_tree_state="TERMINATED",
            process_tree_evidence_sha256="a" * 64,
            owned_writes_state="STOPPED",
            owned_writes_evidence_sha256="b" * 64,
            operation_ledger_state="QUIESCENT",
            operation_ledger_watermark=watermark,
            operation_ledger_evidence_sha256=ledger_digest,
            observed_monotonic_ns=observed,
        )


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
    assert unknown.value.code == "RESOURCE_QUIESCENCE_PROVIDER_NOT_CONFIGURED"
    second = _queued_job(store, project_id, project)
    with pytest.raises(resources.ResourcePolicyError) as overcommit:
        resources.reserve_disk_for_job(store=store, authority=authority, profile=profile,
                                       job_id=second["job_id"], requested_bytes=40,
                                       observation=observation, now_monotonic_ns=1000)
    assert overcommit.value.code == "INSUFFICIENT_DISK_BUDGET"
    store.close()


def test_unknown_quiescence_release_is_exact_durable_and_does_not_resolve_domain(tmp_path):
    store, authority, project, project_id = _authority(tmp_path)
    db_path = tmp_path / "operations.sqlite"
    profile = replace(_profile(project), project_id=project_id)
    observation = _storage(profile, free=100)
    first = _queued_job(store, project_id, project)
    reserved = resources.reserve_disk_for_job(
        store=store, authority=authority, profile=profile, job_id=first["job_id"],
        requested_bytes=70, observation=observation, now_monotonic_ns=1000)
    unknown_result = {
        "success": False,
        "execution": {"status": "UNKNOWN", "job_id": first["job_id"]},
        "error": {"code": "EXECUTION_STATE_UNKNOWN", "safe_retry": False},
    }
    store.finish(first["operation_id"], status="UNKNOWN", result=unknown_result)

    with pytest.raises(resources.ResourcePolicyError) as absent:
        resources.release_disk_for_job(store=store, authority=authority, profile=profile,
                                       job_id=first["job_id"])
    assert absent.value.code == "RESOURCE_QUIESCENCE_PROVIDER_NOT_CONFIGURED"
    with pytest.raises(resources.ResourcePolicyError) as untrusted:
        resources.release_disk_for_job(store=store, authority=authority, profile=profile,
                                       job_id=first["job_id"], quiescence_provider=object())
    assert untrusted.value.code == "RESOURCE_QUIESCENCE_PROVIDER_UNTRUSTED"
    with pytest.raises(resources.ResourcePolicyError) as wrong_identity:
        resources.release_disk_for_job(
            store=store, authority=authority, profile=profile, job_id=first["job_id"],
            quiescence_provider=_SyntheticQuiescenceProvider(wrong_job=True))
    assert wrong_identity.value.code == "RESOURCE_QUIESCENCE_WITNESS_INVALID"
    with pytest.raises(resources.ResourcePolicyError) as stale:
        resources.release_disk_for_job(
            store=store, authority=authority, profile=profile, job_id=first["job_id"],
            quiescence_provider=_SyntheticQuiescenceProvider(stale=True))
    assert stale.value.code == "RESOURCE_QUIESCENCE_WITNESS_STALE"

    released = resources.release_disk_for_job(
        store=store, authority=authority, profile=profile, job_id=first["job_id"],
        quiescence_provider=_SyntheticQuiescenceProvider())
    assert released["status"] == "RELEASED_AFTER_QUIESCENCE"
    assert released["domain_status_at_release"] == "UNKNOWN"
    assert released["safe_retry_at_release"] is False
    assert released["evidence_classification"] == "SOFTWARE_ONLY"
    persisted_job = store.job(first["job_id"])
    assert persisted_job["status"] == persisted_job["operation"]["status"] == "UNKNOWN"
    assert persisted_job["result"] == unknown_result
    again = resources.release_disk_for_job(
        store=store, authority=authority, profile=profile, job_id=first["job_id"])
    assert again["status"] == "RELEASED_AFTER_QUIESCENCE" and again["idempotent"] is True
    store.close()

    # Recovery trusts only the durable reservation transition. It does not
    # rewrite UNKNOWN, infer completion, or enable retry.
    reopened = OperationStore(db_path)
    workspace_root = tmp_path / "authorized"
    host_permissions = {"inspect", "project_write", "compute", "host_control"}
    authority2 = ProjectAuthority(reopened, workspace_root=workspace_root,
                                  permission_provider=lambda: host_permissions,
                                  grant_ceiling=host_permissions)
    recovered = resources._load_reservations(reopened.db)
    assert recovered[reserved["reservation_id"]][1]["status"] == "RELEASED_AFTER_QUIESCENCE"
    still_unknown = reopened.job(first["job_id"])
    assert still_unknown["status"] == still_unknown["operation"]["status"] == "UNKNOWN"
    assert still_unknown["result"]["error"]["safe_retry"] is False

    second = _queued_job(reopened, project_id, project)
    admitted = resources.reserve_disk_for_job(
        store=reopened, authority=authority2, profile=profile, job_id=second["job_id"],
        requested_bytes=80, observation=observation, now_monotonic_ns=1000)
    assert admitted["status"] == "RESERVED" and admitted["requested_bytes"] == 80
    reopened.close()


def test_tampered_persisted_quiescence_receipt_fences_reservation_recovery(tmp_path):
    store, authority, project, project_id = _authority(tmp_path)
    profile = replace(_profile(project), project_id=project_id)
    record = _queued_job(store, project_id, project)
    resources.reserve_disk_for_job(store=store, authority=authority, profile=profile,
                                   job_id=record["job_id"], requested_bytes=25,
                                   observation=_storage(profile), now_monotonic_ns=1000)
    store.finish(record["operation_id"], status="UNKNOWN", result={
        "success": False, "execution": {"status": "UNKNOWN"},
        "error": {"safe_retry": False},
    })
    resources.release_disk_for_job(
        store=store, authority=authority, profile=profile, job_id=record["job_id"],
        quiescence_provider=_SyntheticQuiescenceProvider())
    with store.lock:
        row = store.db.execute(
            "SELECT id,metadata FROM job_events WHERE job_id=? AND event='W26DiskReservation' "
            "ORDER BY id DESC LIMIT 1", (record["job_id"],)).fetchone()
        damaged = json.loads(row["metadata"])
        damaged["quiescence_witness"]["process_tree_state"] = "RUNNING"
        store.db.execute("UPDATE job_events SET metadata=? WHERE id=?",
                         (json.dumps(damaged, sort_keys=True), row["id"]))
    with pytest.raises(resources.ResourcePolicyError) as blocked:
        resources._load_reservations(store.db)
    assert blocked.value.code == "RESOURCE_RESERVATION_STATE_UNKNOWN"
    assert store.job(record["job_id"])["status"] == "UNKNOWN"
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


@pytest.mark.skipif(os.name == "nt", reason="POSIX pinned-fd provider; Windows native adapter NOT_RUN")
def test_posix_provider_observes_real_capacity_without_claiming_quota_or_sandbox(tmp_path):
    root = tmp_path / "capacity-root"
    root.mkdir()
    profile = _disk_profile("project-disk", root, estimate=4096)
    provider = resources.PosixStorageObservationProvider()
    observation = provider.read_observation(profile)

    assert observation._observation_attestation is resources._POSIX_STORAGE_OBSERVATION_ATTESTATION
    assert observation._native_attestation is None
    assert observation.quota_state == "ADMISSION_ONLY"
    assert observation.quota_id is observation.quota_scope_id is None
    assert observation.quota_limit_bytes is observation.quota_used_bytes is None
    assert observation.host_id == profile.host_id
    assert observation.storage_id == profile.storage_id
    assert 0 <= observation.free_bytes <= observation.total_bytes
    assert observation.evidence_classification == "HOST_OBSERVED_ADMISSION_ONLY"
    assert "service_account_id" not in profile.as_dict()
    decision = resources.evaluate_disk_admission(
        profile, observation, profile.estimate_for("study.run"), active_reserved_bytes=0,
        now_monotonic_ns=observation.observed_monotonic_ns)
    assert decision.enforcement == "ADMISSION_ONLY"
    assert decision.evidence_classification == "HOST_OBSERVED_ADMISSION_ONLY"

    forged = replace(observation, _observation_attestation=None)
    with pytest.raises(resources.ResourcePolicyError) as unverified:
        resources.evaluate_disk_admission(
            profile, forged, profile.estimate_for("study.run"), active_reserved_bytes=0,
            now_monotonic_ns=observation.observed_monotonic_ns)
    assert unverified.value.code == "DISK_OBSERVATION_UNVERIFIED"


@pytest.mark.skipif(os.name == "nt", reason="POSIX trusted-config loader; Windows adapter NOT_RUN")
def test_trusted_disk_config_loads_exact_host_scope_and_rejects_tampering(tmp_path):
    root = tmp_path / "config-root"
    root.mkdir()
    profile = _disk_profile("project-disk", root)
    path = _write_disk_config(tmp_path, profile)
    config = resources.TrustedDiskResourceConfig.load(path)
    selected = config.resolve("project-disk")
    assert config.trusted_startup_source is True
    assert selected.trusted_startup_config is True
    assert config.source_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert selected.estimate_for("study.run") == 64 * 1024
    with pytest.raises(resources.ResourcePolicyError) as absent:
        config.resolve("other-project")
    assert absent.value.code == "DISK_RESOURCE_PROFILE_NOT_CONFIGURED"

    duplicate = _write_disk_config(tmp_path, profile, duplicate_key=True)
    with pytest.raises(ExecutionContractError) as duplicate_error:
        resources.TrustedDiskResourceConfig.load(duplicate)
    assert duplicate_error.value.code == "DISK_RESOURCE_CONFIG_INVALID"

    wrong_host = _write_disk_config(tmp_path, profile, host_id="foreign-host")
    with pytest.raises(resources.ResourcePolicyError) as host_mismatch:
        resources.TrustedDiskResourceConfig.load(wrong_host)
    assert host_mismatch.value.code == "DISK_RESOURCE_CONFIG_HOST_MISMATCH"

    unsafe = _write_disk_config(tmp_path, profile)
    unsafe.chmod(0o666)
    with pytest.raises(resources.ResourcePolicyError) as permissions:
        resources.TrustedDiskResourceConfig.load(unsafe)
    assert permissions.value.code == "DISK_RESOURCE_CONFIG_INVALID"


@pytest.mark.skipif(os.name == "nt", reason="POSIX pinned-fd provider; Windows native adapter NOT_RUN")
def test_posix_provider_rejects_filesystem_id_drift_and_root_replacement(tmp_path, monkeypatch):
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "marker").write_text("outside", encoding="utf-8")
    profile = _disk_profile("project-disk", root)
    wrong_storage = replace(profile, storage_id=profile.storage_id + ":foreign")
    provider = resources.PosixStorageObservationProvider()
    with pytest.raises(resources.ResourcePolicyError) as drift:
        provider.read_observation(wrong_storage)
    assert drift.value.code == "DISK_STORAGE_IDENTITY_MISMATCH"

    from comsol_mcp import _state_backup
    original_verify = _state_backup._verify_posix_directory_links
    replaced = False

    def replace_named_root(links):
        nonlocal replaced
        if not replaced:
            parent_fd, leaf, _identity = links[-1]
            parked = tmp_path / "parked-root"
            os.rename(root, parked)
            os.symlink(outside, root, target_is_directory=True)
            replaced = True
        original_verify(links)

    monkeypatch.setattr(_state_backup, "_verify_posix_directory_links", replace_named_root)
    with pytest.raises(resources.ResourcePolicyError):
        provider.read_observation(profile)
    assert replaced is True
    assert (outside / "marker").read_text(encoding="utf-8") == "outside"
    assert root.is_symlink()
    root.unlink()
    os.rename(tmp_path / "parked-root", root)


@pytest.mark.skipif(os.name == "nt", reason="POSIX trusted-config and storage adapters; Windows native adapter NOT_RUN")
def test_disk_coordinator_binds_real_operationstore_then_releases_terminal_reservation(tmp_path):
    store, authority, project, project_id = _authority(tmp_path)
    record = _queued_dispatch_job(store, project_id, inner="study.run", wrapper="registry_call")
    coordinator, config = _make_coordinator(tmp_path, store, authority, project, project_id,
                                            estimate=32 * 1024)
    result = coordinator.reserve_queued_job(record["job_id"])
    assert result["status"] == "RESERVED"
    assert result["requested_bytes"] == result["trusted_estimate_bytes"] == 32 * 1024
    assert result["operation_id"] == "study.run"
    assert result["enforcement"] == "ADMISSION_ONLY"
    assert result["evidence_classification"] == "HOST_OBSERVED_ADMISSION_ONLY"
    assert store.job(record["job_id"])["status"] == "QUEUED"

    job = store.job(record["job_id"])
    operation = store.get_operation(record["operation_id"])
    selected = config.resolve(project_id)
    for metadata in (job["metadata"], operation["metadata"]):
        assert metadata["project_id"] == project_id
        assert metadata["project_root_identity"] == selected.root.file_id
        assert metadata["project_root_volume_id"] == selected.root.volume_id
        assert metadata["disk_resource_profile_sha256"] == selected.sha256
    assert store.db.execute(
        "SELECT COUNT(*) FROM job_events WHERE job_id=? AND event=?",
        (record["job_id"], resources.DISK_ROOT_BINDING_EVENT)).fetchone()[0] == 1
    assert resources._load_reservations(store.db)[result["reservation_id"]][1]["status"] == "RESERVED"

    store.finish(record["operation_id"], status="SUCCEEDED", result={"success": True})
    release = coordinator.release_terminal_job(record["job_id"])
    again = coordinator.release_terminal_job(record["job_id"])
    assert release["status"] == "RELEASED" and release["idempotent"] is False
    assert again["status"] == "RELEASED" and again["idempotent"] is True
    assert store.job(record["job_id"])["status"] == "SUCCEEDED"
    store.close()


@pytest.mark.skipif(os.name == "nt", reason="POSIX coordinator; Windows native adapter NOT_RUN")
def test_disk_coordinator_denies_wrong_inner_operation_and_wrong_authorized_root_without_mutation(tmp_path):
    store, authority, project, project_id = _authority(tmp_path)
    record = _queued_dispatch_job(store, project_id, inner="study.run")
    coordinator, _ = _make_coordinator(tmp_path, store, authority, project, project_id,
                                       operation="model.package")
    with pytest.raises(resources.ResourcePolicyError) as unsupported:
        coordinator.reserve_queued_job(record["job_id"])
    assert unsupported.value.code == "RESOURCE_RESERVATION_OPERATION_UNSUPPORTED"
    assert not any(key in store.job(record["job_id"])["metadata"]
                   for key in ("project_root_identity", "disk_resource_profile_id"))
    assert store.db.execute("SELECT COUNT(*) FROM job_events WHERE job_id=?",
                            (record["job_id"],)).fetchone()[0] == 0

    other_root = tmp_path / "other-real-root"
    other_root.mkdir()
    wrong_profile = _disk_profile(project_id, other_root, operation="study.run")
    wrong_config_path = _write_disk_config(tmp_path / "wrong", wrong_profile)
    wrong_config = resources.TrustedDiskResourceConfig.load(wrong_config_path)
    wrong_coordinator = resources.DiskResourceCoordinator(config=wrong_config, store=store,
                                                          authority=authority)
    with pytest.raises(resources.ResourcePolicyError) as wrong_root:
        wrong_coordinator.reserve_queued_job(record["job_id"])
    assert wrong_root.value.code == "RESOURCE_RESERVATION_ROOT_AUTHORITY_MISMATCH"
    assert not any(key in store.job(record["job_id"])["metadata"]
                   for key in ("project_root_identity", "disk_resource_profile_id"))
    store.close()


@pytest.mark.skipif(os.name == "nt", reason="POSIX coordinator; Windows native adapter NOT_RUN")
def test_disk_coordinator_requires_project_write_authority_for_file_producer(tmp_path):
    store, authority, project, project_id = _authority(tmp_path)
    record = _queued_dispatch_job(store, project_id, inner="model.package")
    coordinator, _ = _make_coordinator(tmp_path, store, authority, project, project_id,
                                       operation="model.package", estimate=32 * 1024)
    with pytest.raises(resources.ResourcePolicyError) as denied:
        coordinator.reserve_queued_job(record["job_id"])
    assert denied.value.code == "PROJECT_PERMISSION_REQUIRED"
    assert store.job(record["job_id"])["status"] == "QUEUED"
    assert store.db.execute("SELECT COUNT(*) FROM job_events WHERE job_id=?",
                            (record["job_id"],)).fetchone()[0] == 0
    store.close()


@pytest.mark.skipif(os.name == "nt", reason="POSIX coordinator; Windows native adapter NOT_RUN")
def test_disk_coordinator_rejects_mismatched_durable_wrapper_records(tmp_path):
    store, authority, project, project_id = _authority(tmp_path)
    record = _queued_dispatch_job(store, project_id, inner="study.run")
    with store.lock:
        row = store.db.execute("SELECT metadata FROM jobs WHERE job_id=?", (record["job_id"],)).fetchone()
        metadata = json.loads(row["metadata"])
        metadata["arguments"]["operation_id"] = "project.inspect"
        store.db.execute("UPDATE jobs SET metadata=? WHERE job_id=?",
                         (json.dumps(metadata, sort_keys=True), record["job_id"]))
    coordinator, _ = _make_coordinator(tmp_path, store, authority, project, project_id)
    with pytest.raises(resources.ResourcePolicyError) as mismatch:
        coordinator.reserve_queued_job(record["job_id"])
    assert mismatch.value.code == "RESOURCE_RESERVATION_OPERATION_UNKNOWN"
    assert store.db.execute("SELECT COUNT(*) FROM job_events WHERE job_id=?",
                            (record["job_id"],)).fetchone()[0] == 0
    store.close()


@pytest.mark.skipif(os.name == "nt", reason="POSIX coordinator; Windows native adapter NOT_RUN")
def test_concurrent_disk_admissions_serialize_actual_operationstore_reservations(tmp_path):
    store, authority, project, project_id = _authority(tmp_path)
    jobs = [_queued_dispatch_job(store, project_id) for _ in range(2)]
    initial = _disk_profile(project_id, project, estimate=6 * 1024 * 1024,
                            reserve_free=0, max_task=8 * 1024 * 1024)
    provider = resources.PosixStorageObservationProvider()
    live = provider.read_observation(initial)
    reserve = max(0, live.free_bytes - 10 * 1024 * 1024)
    profile = _disk_profile(project_id, project, estimate=6 * 1024 * 1024,
                             reserve_free=reserve, max_task=8 * 1024 * 1024)
    config = resources.TrustedDiskResourceConfig.load(_write_disk_config(tmp_path, profile))
    coordinator = resources.DiskResourceCoordinator(config=config, store=store, authority=authority,
                                                    storage_provider=provider)
    barrier = threading.Barrier(2)
    outcomes = []

    def run(job_id):
        barrier.wait()
        try:
            outcomes.append(("reserved", coordinator.reserve_queued_job(job_id)["status"]))
        except resources.ResourcePolicyError as exc:
            outcomes.append(("refused", exc.code))

    threads = [threading.Thread(target=run, args=(item["job_id"],)) for item in jobs]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert all(not thread.is_alive() for thread in threads)
    assert sorted(kind for kind, _ in outcomes) == ["refused", "reserved"]
    assert "INSUFFICIENT_DISK_BUDGET" in [value for _kind, value in outcomes]
    assert sum(1 for _job, item in resources._load_reservations(store.db).values()
               if item["status"] == "RESERVED") == 1
    store.close()


@pytest.mark.skipif(os.name == "nt", reason="POSIX coordinator; Windows native adapter NOT_RUN")
def test_unknown_disk_reservation_survives_restart_and_is_not_age_released(tmp_path):
    store, authority, project, project_id = _authority(tmp_path)
    first = _queued_dispatch_job(store, project_id)
    initial = _disk_profile(project_id, project, estimate=8 * 1024 * 1024,
                            reserve_free=0, max_task=16 * 1024 * 1024)
    live = resources.PosixStorageObservationProvider().read_observation(initial)
    reserve = max(0, live.free_bytes - 12 * 1024 * 1024)
    profile = _disk_profile(project_id, project, estimate=8 * 1024 * 1024,
                             reserve_free=reserve, max_task=16 * 1024 * 1024)
    path = _write_disk_config(tmp_path, profile)
    config = resources.TrustedDiskResourceConfig.load(path)
    coordinator = resources.DiskResourceCoordinator(config=config, store=store, authority=authority)
    reserved = coordinator.reserve_queued_job(first["job_id"])
    store.finish(first["operation_id"], status="UNKNOWN", result={
        "success": False, "execution": {"status": "UNKNOWN", "job_id": first["job_id"]},
        "error": {"code": "EXECUTION_STATE_UNKNOWN", "safe_retry": False},
    })
    with pytest.raises(resources.ResourcePolicyError) as held:
        coordinator.release_terminal_job(first["job_id"])
    assert held.value.code == "RESOURCE_QUIESCENCE_PROVIDER_NOT_CONFIGURED"
    assert resources._load_reservations(store.db)[reserved["reservation_id"]][1]["status"] == "RESERVED"
    store.close()

    reopened = OperationStore(tmp_path / "operations.sqlite")
    permissions = {"inspect", "project_write", "compute", "host_control"}
    authority2 = ProjectAuthority(reopened, workspace_root=tmp_path / "authorized",
                                  permission_provider=lambda: permissions, grant_ceiling=permissions)
    recovered = resources._load_reservations(reopened.db)
    assert recovered[reserved["reservation_id"]][1]["status"] == "RESERVED"
    assert reopened.job(first["job_id"])["status"] == "UNKNOWN"
    assert reopened.job(first["job_id"])["result"]["error"]["safe_retry"] is False
    second = _queued_dispatch_job(reopened, project_id)
    coordinator2 = resources.DiskResourceCoordinator(
        config=resources.TrustedDiskResourceConfig.load(path), store=reopened, authority=authority2)
    with pytest.raises(resources.ResourcePolicyError) as overcommitted:
        coordinator2.reserve_queued_job(second["job_id"])
    assert overcommitted.value.code == "INSUFFICIENT_DISK_BUDGET"
    assert reopened.job(first["job_id"])["status"] == "UNKNOWN"
    reopened.close()


@pytest.mark.skipif(os.name == "nt", reason="POSIX coordinator; Windows kernel adapter NOT_RUN")
def test_queued_only_crash_keeps_disk_reservation_and_reconciles_without_dispatch(tmp_path):
    store, authority, project, project_id = _authority(tmp_path)
    first = _queued_dispatch_job(store, project_id)
    coordinator, config = _make_coordinator(tmp_path, store, authority, project, project_id,
                                            estimate=64 * 1024)
    reserved = coordinator.reserve_queued_job(first["job_id"])
    names = [row[0] for row in store.db.execute(
        "SELECT event FROM job_events WHERE job_id=?", (first["job_id"],)).fetchall()]
    assert "RUNNING" not in names  # crash window is strictly pre-dispatch
    assert reserved["status"] == "RESERVED"
    store_path = tmp_path / "operations.sqlite"
    store.close()

    reopened = OperationStore(store_path)
    interrupted = reopened.reconcile_after_restart()
    assert interrupted == [first["job_id"]]
    assert reopened.job(first["job_id"])["status"] == "RECONCILING"
    durable = resources._load_reservations(reopened.db)[reserved["reservation_id"]]
    assert durable[0] == first["job_id"]
    assert durable[1]["status"] == "RESERVED"

    permissions = {"inspect", "project_write", "compute", "host_control"}
    authority2 = ProjectAuthority(reopened, workspace_root=tmp_path / "authorized",
                                  permission_provider=lambda: permissions, grant_ceiling=permissions)
    coordinator2 = resources.DiskResourceCoordinator(config=config, store=reopened, authority=authority2)
    with pytest.raises(resources.ResourcePolicyError) as fenced:
        coordinator2.reserve_queued_job(first["job_id"])
    assert fenced.value.code == "RESOURCE_RESERVATION_NOT_PREDISPATCH"
    assert resources._load_reservations(reopened.db)[reserved["reservation_id"]][1]["status"] == "RESERVED"
    reopened.close()


class _DaemonResourceAdapter:
    def model_snapshot(self, model_tag):
        return {"model_tag": model_tag, "server_instance_id": "server",
                "external_event_counter": 0, "fingerprint": "stable"}


class _DaemonResourceWorker:
    def __init__(self):
        self.generation = 1
        self.submitted = []
        self.parameters = {}
        self.readback_override = None
        self._event_callback = None

    def client(self):
        from comsol_mcp._java_worker import RemoteClient
        return RemoteClient(self)

    @contextmanager
    def operation_context(self, _operation_id, *, on_request_event=None):
        previous = self._event_callback
        self._event_callback = on_request_event
        try:
            yield
        finally:
            self._event_callback = previous

    def submit(self, kind, payload, **kwargs):
        safe_payload = dict(payload)
        if self._event_callback is not None:
            self._event_callback({"phase": "submitted", "kind": kind,
                                  "request_id": kwargs.get("request_id", f"fixture-{len(self.submitted) + 1}"),
                                  "payload": safe_payload})
        self.submitted.append((kind, safe_payload))
        result = None
        if kind == "model":
            result = {"$worker_handle": f"model:{payload['tag']}", "generation": self.generation,
                      "java_type": "Model"}
        elif kind == "call":
            method = payload["method"]
            args = payload.get("args", [])
            handle = payload["handle"]
            if method == "param" and handle.startswith("model:"):
                result = {"$worker_handle": f"{handle}:param", "generation": self.generation,
                          "java_type": "ParamCollection"}
            elif method == "set" and handle.endswith(":param"):
                self.parameters[str(args[0])] = str(args[1])
            elif method == "get" and handle.endswith(":param"):
                result = (self.readback_override if self.readback_override is not None
                          else self.parameters[str(args[0])])
        return {"ok": True, "status": "SUCCEEDED", "result": result}


def _daemon_disk_environment(tmp_path, *, reserve_free=0, allowed_operation="parameter.set",
                             wrong_root=False, replace_root=False):
    home = tmp_path / "daemon-home"
    workspace_root = tmp_path / "workspace-root"
    workspace_root.mkdir(parents=True)
    bootstrap = ControlDaemon(home, project_root=workspace_root)
    project_result = bootstrap.project_authority.dispatch("project.create", {
        "label": "disk admission test", "workspace": "project-one",
        "policy": {"permissions": ["inspect", "project_write", "compute"]},
    })
    project_data = project_result["data"]["project"]
    project_path = Path(project_data["workspace"])
    project_id = project_data["project_id"]
    bootstrap.close()

    selected_root = tmp_path / "wrong-profile-root" if wrong_root else project_path
    if wrong_root:
        selected_root.mkdir()
    profile = _disk_profile(project_id, selected_root, operation=allowed_operation,
                            estimate=64 * 1024, reserve_free=reserve_free,
                            max_task=2 * 1024 * 1024)
    config_path = _write_disk_config(tmp_path / "resource-config", profile)
    service = ExecutionService(SessionLedger("session", "server"), _DaemonResourceAdapter(),
                               project_root=workspace_root)
    bound = service.bind_model("Model")
    model_ref = model_ref_from_mapping(bound["execution"]["model_ref"])
    daemon = ControlDaemon(
        home, service=service, worker=_DaemonResourceWorker(), registry={},
        project_root=workspace_root, disk_resource_config=config_path,
    )
    daemon.backend._bind_model_project(model_ref.as_dict(), project_id)
    # `_bind_model_project` stages a binding until the backend's next durable
    # persist. This fixture models an already-adopted model, so seed the same
    # exact revision row the daemon's normal adoption path writes before it
    # submits project-scoped work.
    daemon.store.put_metadata("revisions", daemon.backend._model_project_key(model_ref.as_dict()), {
        "model_ref": model_ref.as_dict(), "project_id": project_id,
        "attribution": "PROJECT_BOUND", "revision": 0, "dirty": False,
    })
    if replace_root:
        parked = project_path.with_name("project-one-parked")
        os.rename(project_path, parked)
        project_path.mkdir()
    request = {
        "operation": "parameter.set",
        "arguments": {"parameters": [{"name": "resource_test", "expression": "1"}]},
        # This is the existing default Worker lane: the ModelRef binds the
        # service session, while an outer session id would request a separate
        # registered session lifecycle that this software fixture does not own.
        "execution": {"project_id": project_id, "model_ref": model_ref.as_dict(), "expected_revision": 0,
                      "idempotency_key": "disk-resource-request",
                      "request_id": "disk-resource-request"},
    }
    return daemon, request, project_path, project_id, profile, config_path


@pytest.mark.skipif(os.name == "nt", reason="POSIX trusted config and storage provider; Windows adapter NOT_RUN")
def test_control_daemon_reserves_before_running_then_releases_only_after_terminal(tmp_path, monkeypatch):
    from comsol_mcp import _g3_ops

    monkeypatch.setattr(_g3_ops, "REQUIRES_ISOLATION", frozenset())
    daemon, request, _project, _project_id, _profile, _config = _daemon_disk_environment(tmp_path)
    try:
        result = daemon.dispatch(request)
        job_id = result["execution"]["job_id"]
        job = daemon.store.job(job_id)
        assert result["success"] is True, result
        assert len(daemon.worker.submitted) == 7
        kinds = [kind for kind, _payload in daemon.worker.submitted]
        assert kinds.count("model") == 1 and kinds.count("call") == 6
        methods = [payload["method"] for kind, payload in daemon.worker.submitted if kind == "call"]
        assert methods.count("set") == 1 and methods.count("get") == 1
        assert job["status"] == "SUCCEEDED"
        reservation_rows = [item for bound_job, item in resources._load_reservations(daemon.store.db).values()
                            if bound_job == job_id]
        assert len(reservation_rows) == 1
        assert reservation_rows[0]["status"] == "RELEASED"
        events = daemon.store.db.execute(
            "SELECT event FROM job_events WHERE job_id=? ORDER BY id", (job_id,)
        ).fetchall()
        names = [row[0] for row in events]
        assert names.index("W26DiskReservation") < names.index("W26DiskAdmission") < names.index("RUNNING")
        assert names.index("RUNNING") < names.index("worker_request") < names.index("SUCCEEDED")
        reservation_indexes = [index for index, name in enumerate(names) if name == "W26DiskReservation"]
        assert len(reservation_indexes) == 2
        assert names.index("SUCCEEDED") < reservation_indexes[1] < names.index("W26DiskReservationReleased")
        assert "W26DiskReservationReleased" in names
    finally:
        daemon.close()


@pytest.mark.skipif(os.name == "nt", reason="POSIX trusted config and storage provider; Windows adapter NOT_RUN")
def test_control_daemon_refuses_competing_reservation_before_worker_dispatch(tmp_path, monkeypatch):
    from comsol_mcp import _g3_ops

    monkeypatch.setattr(_g3_ops, "REQUIRES_ISOLATION", frozenset())
    calls = []
    monkeypatch.setitem(_g3_ops.DISPATCH, "parameter.set", lambda *_args: calls.append("dispatch") or {
        "status": {"ok": True, "status": "APPLIED", "applied_count": 1, "failed_count": 0},
        "applied": [{"name": "resource_test"}],
    })
    daemon, request, project, project_id, _profile, _config = _daemon_disk_environment(tmp_path)
    try:
        provider = resources.PosixStorageObservationProvider()
        sample = provider.read_observation(daemon._disk_resource_coordinator.config.resolve(project_id))
        estimate = 64 * 1024
        # Admit one reservation, leaving enough live capacity for it but less
        # than another identical queued operation.
        free_headroom = max(0, sample.free_bytes - estimate - estimate // 2)
        profile = _disk_profile(project_id, project, operation="parameter.set", estimate=estimate,
                                reserve_free=free_headroom, max_task=2 * 1024 * 1024)
        config_path = _write_disk_config(tmp_path / "competing-config", profile)
        daemon._disk_resource_coordinator = resources.DiskResourceCoordinator(
            config=resources.TrustedDiskResourceConfig.load(config_path),
            store=daemon.store, authority=daemon.project_authority, storage_provider=provider,
        )
        competing = _queued_dispatch_job(daemon.store, project_id, inner="parameter.set", wrapper="operation_call")
        daemon._disk_resource_coordinator.reserve_queued_job(competing["job_id"])
        result = daemon.dispatch(request)
        assert result["success"] is False
        assert result["error"]["code"] == "INSUFFICIENT_DISK_BUDGET"
        assert result["data"]["engine_dispatched"] is False
        assert calls == []
        assert daemon.worker.submitted == []
        assert daemon.store.job(result["execution"]["job_id"])["status"] == "FAILED"
        assert daemon.store.job(competing["job_id"])["status"] == "QUEUED"
        held = [item for bound_job, item in resources._load_reservations(daemon.store.db).values()
                if bound_job == competing["job_id"]]
        assert len(held) == 1 and held[0]["status"] == "RESERVED"
    finally:
        daemon.close()


@pytest.mark.skipif(os.name == "nt", reason="POSIX trusted config and storage provider; Windows adapter NOT_RUN")
@pytest.mark.parametrize("root_mode,expected_code", [
    ("wrong_profile", "RESOURCE_RESERVATION_ROOT_AUTHORITY_MISMATCH"),
    ("replaced_after_startup", "RESOURCE_RESERVATION_ROOT_IDENTITY_MISMATCH"),
])
def test_control_daemon_wrong_or_changed_root_fails_before_domain_dispatch(
        tmp_path, monkeypatch, root_mode, expected_code):
    from comsol_mcp import _g3_ops

    monkeypatch.setattr(_g3_ops, "REQUIRES_ISOLATION", frozenset())
    calls = []
    monkeypatch.setitem(_g3_ops.DISPATCH, "parameter.set", lambda *_args: calls.append("dispatch") or {
        "status": {"ok": True, "status": "APPLIED", "applied_count": 1, "failed_count": 0},
        "applied": [{"name": "resource_test"}],
    })
    daemon, request, _project, _project_id, _profile, _config = _daemon_disk_environment(
        tmp_path, wrong_root=root_mode == "wrong_profile",
        replace_root=root_mode == "replaced_after_startup")
    try:
        result = daemon.dispatch(request)
        assert result["success"] is False
        assert result["error"]["code"] == expected_code
        assert result["data"]["engine_dispatched"] is False
        assert calls == []
        assert daemon.worker.submitted == []
        assert daemon.store.job(result["execution"]["job_id"])["status"] == "FAILED"
    finally:
        daemon.close()


@pytest.mark.skipif(os.name == "nt", reason="POSIX trusted config and storage provider; Windows adapter NOT_RUN")
def test_control_daemon_unknown_keeps_reservation_across_restart(tmp_path, monkeypatch):
    from comsol_mcp import _g3_ops

    monkeypatch.setattr(_g3_ops, "REQUIRES_ISOLATION", frozenset())
    daemon, request, _project, _project_id, _profile, config_path = _daemon_disk_environment(tmp_path)
    daemon.worker.readback_override = "1.00"
    home = daemon.home
    try:
        result = daemon.dispatch(request)
        job_id = result["execution"]["job_id"]
        assert daemon.store.job(job_id)["status"] == "UNKNOWN"
        reservation_rows = [item for bound_job, item in resources._load_reservations(daemon.store.db).values()
                            if bound_job == job_id]
        assert result["success"] is False
        assert result["error"].get("safe_retry") is False, result
        assert any(kind == "call" and payload.get("method") == "set"
                   for kind, payload in daemon.worker.submitted)
        assert len(reservation_rows) == 1 and reservation_rows[0]["status"] == "RESERVED"
        assert not any(event[0] == "W26DiskReservationReleased" for event in daemon.store.db.execute(
            "SELECT event FROM job_events WHERE job_id=?", (job_id,)).fetchall())
    finally:
        daemon.close()

    restarted = ControlDaemon(home, disk_resource_config=config_path)
    try:
        recovered = restarted.store.job(job_id)
        assert recovered["status"] == "RECONCILING"
        reservation_rows = [item for bound_job, item in resources._load_reservations(restarted.store.db).values()
                            if bound_job == job_id]
        assert len(reservation_rows) == 1 and reservation_rows[0]["status"] == "RESERVED"
        assert restarted._disk_resource_coordinator is not None
    finally:
        restarted.close()
