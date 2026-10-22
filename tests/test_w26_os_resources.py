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
from comsol_mcp import _w26_memory_resources as memory_resources


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


def _write_memory_config(tmp_path: Path, *, operation="parameter.set", estimate=1,
                         max_total=2 * 1024 * 1024, reserve_available=0,
                         max_task=2 * 1024 * 1024):
    platform_name = memory_resources._current_platform()
    document = {
        "schema": memory_resources.MEMORY_PROFILE_SCHEMA,
        "profile_id": "integration-memory-profile",
        "host_id": memory_resources._current_host_fingerprint(platform_name),
        "platform": platform_name,
        "budget": {"max_total_reserved_bytes": max_total,
                   "reserve_available_bytes": reserve_available,
                   "max_task_reservation_bytes": max_task},
        "allowed_operations": [operation],
        "trusted_estimates": [{"operation_id": operation, "estimate_bytes": estimate}],
    }
    directory = tmp_path / "private-memory-startup"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    path = directory / "memory-profile.json"
    path.write_text(json.dumps(document, sort_keys=True, separators=(",", ":")), encoding="utf-8")
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


def test_host_change_plan_revalidates_closed_actions_and_backup_secret_boundary(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    profile = _profile(project)
    action = _action(desired={"path": str(project / "cache"), "rights": ["READ", "WRITE"]})
    plan = resources.build_host_change_plan(profile, change_manifest=[action], operator_inputs={})
    payload = plan.as_dict()
    payload["change_manifest"][0]["caller_claim"] = True
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    forged = resources.HostChangePlan(encoded, hashlib.sha256(encoded.encode()).hexdigest())
    with pytest.raises(resources.ResourcePolicyError) as unknown:
        resources.validate_host_change_plan(forged)
    assert unknown.value.code == "HOST_PLAN_INVALID"

    plan = resources.build_host_change_plan(
        profile, change_manifest=[action], operator_inputs={},
        backup_manifest=[{"resource_name": "backup-1", "path": str(project / "backup" / "acl.json"),
                          "sha256": "1" * 64, "identity": {"file_id": "backup-id"}}])
    payload = plan.as_dict()
    payload["backup_manifest"][0]["identity"] = {"api_token": "must-not-persist"}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    forged = resources.HostChangePlan(encoded, hashlib.sha256(encoded.encode()).hexdigest())
    with pytest.raises(resources.ResourcePolicyError) as secret:
        resources.validate_host_change_plan(forged)
    assert secret.value.code == "HOST_PLAN_INVALID"


def test_host_control_plan_journal_is_durable_scoped_chained_and_idempotent(tmp_path):
    store, authority, project, project_id = _authority(tmp_path)
    profile = replace(_profile(project), project_id=project_id, host_id=resources._current_host_id())
    root = profile.root
    metadata = {
        "operation": "study.run",
        "project_id": project_id,
        "project_root_identity": root.file_id,
        "project_root_volume_id": root.volume_id,
        "project_root_owner_id": root.owner_id,
        "host_id": profile.host_id,
    }
    record, reused = store.begin(
        request_id="host-plan-request",
        idempotency_key="host-plan-idempotency",
        request_hash=hashlib.sha256(b"host plan").hexdigest(),
        operation="study.run", metadata=metadata,
    )
    assert not reused
    plan = resources.build_host_change_plan(
        profile,
        change_manifest=[_action(desired={"path": str(project / "cache"), "rights": ["READ", "WRITE"]})],
        operator_inputs={"selected_profile": profile.profile_id},
    )
    first = resources.append_host_control_journal(
        store, authority, profile, job_id=record["job_id"], record_type="PLAN_ONLY", plan=plan)
    repeated = resources.append_host_control_journal(
        store, authority, profile, job_id=record["job_id"], record_type="PLAN_ONLY", plan=plan)
    observed = resources.append_host_control_journal(
        store, authority, profile, job_id=record["job_id"],
        record_type="OBSERVATION_UNVERIFIED", evidence_sha256="9" * 64)
    rows = resources.read_host_control_journal(store, authority, profile, job_id=record["job_id"])
    assert first["sequence"] == 0 and repeated["id"] == first["id"]
    assert observed["sequence"] == 1
    assert [item["record_type"] for item in rows] == ["PLAN_ONLY", "OBSERVATION_UNVERIFIED"]
    assert rows[1]["previous_sha256"] == rows[0]["record_sha256"]
    assert all(item["evidence_classification"] == "SOFTWARE_ONLY" for item in rows)

    with store.lock:
        store.db.execute("UPDATE job_events SET metadata=? WHERE id=?",
                         (json.dumps({**rows[0], "sequence": 7}, sort_keys=True), rows[0]["id"]))
    with pytest.raises(resources.ResourcePolicyError) as damaged:
        resources.read_host_control_journal(store, authority, profile, job_id=record["job_id"])
    assert damaged.value.code == "HOST_JOURNAL_CORRUPT"
    store.close()


def test_host_control_journal_rejects_job_bound_to_other_root_or_host(tmp_path):
    store, authority, project, project_id = _authority(tmp_path)
    profile = replace(_profile(project), project_id=project_id, host_id=resources._current_host_id())
    metadata = {
        "operation": "study.run",
        "project_id": project_id,
        "project_root_identity": "foreign-inode",
        "project_root_volume_id": profile.root.volume_id,
        "project_root_owner_id": profile.root.owner_id,
        "host_id": profile.host_id,
    }
    record, _ = store.begin(
        request_id="foreign-host-request",
        idempotency_key="foreign-host-idempotency",
        request_hash=hashlib.sha256(b"foreign").hexdigest(),
        operation="study.run", metadata=metadata,
    )
    with pytest.raises(resources.ResourcePolicyError) as scope:
        resources.append_host_control_journal(
            store, authority, profile, job_id=record["job_id"],
            record_type="OBSERVATION_UNVERIFIED", evidence_sha256="8" * 64)
    assert scope.value.code == "HOST_JOURNAL_SCOPE_MISMATCH"
    rows = store.db.execute(
        "SELECT id FROM job_events WHERE job_id=? AND event=?",
        (record["job_id"], resources.HOST_CONTROL_JOURNAL_EVENT),
    ).fetchall()
    assert rows == []
    store.close()


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
        self.comments = {}
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

    def describe_public(self, _node):
        from comsol_mcp._g2_public_api import MODEL_ENTITY, VERSION
        return {
            "runtime_version": VERSION,
            "interfaces": [MODEL_ENTITY],
            "methods": [
                {"interface": MODEL_ENTITY, "method": "comments", "parameters": [],
                 "returns": "java.lang.String"},
                {"interface": MODEL_ENTITY, "method": "comments", "parameters": ["java.lang.String"],
                 "returns": MODEL_ENTITY},
            ],
        }

    def entity_identity(self, left, right, **kwargs):
        # Reuse the real client's generation and closed-envelope validation.
        # Fixture handles represent stable references; Python proxy identity
        # cannot prove that a setter returned the same Worker-side receiver.
        from comsol_mcp._java_worker import PersistentJavaWorker
        return PersistentJavaWorker.entity_identity(self, left, right, **kwargs)

    def submit(self, kind, payload, **kwargs):
        safe_payload = dict(payload)
        request_id = kwargs.get("request_id") or f"fixture-{len(self.submitted) + 1}"
        if self._event_callback is not None:
            self._event_callback({"phase": "submitted", "kind": kind,
                                  "request_id": request_id,
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
            elif method == "feature" and handle.startswith("model:") and len(args) == 1:
                result = {"$worker_handle": f"{handle}:feature:{args[0]}",
                          "generation": self.generation, "java_type": "ModelEntity"}
            elif method == "comments" and ":feature:" in handle:
                if args:
                    value = args[0]["data"] if isinstance(args[0], dict) else str(args[0])
                    self.comments[handle] = value
                    result = {"$worker_handle": handle, "generation": self.generation,
                              "java_type": "ModelEntity"}
                else:
                    result = self.comments.get(handle, "")
            elif method == "resolveModelPath" and ":feature:" in handle:
                result = "m/feature/resource_test"
            elif method == "set" and handle.endswith(":param"):
                self.parameters[str(args[0])] = str(args[1])
            elif method == "get" and handle.endswith(":param"):
                result = (self.readback_override if self.readback_override is not None
                          else self.parameters[str(args[0])])
        elif kind == "g2_entity_identity":
            result = {"same_reference": payload["left_handle"] == payload["right_handle"],
                      "generation": self.generation,
                      "identity_scope": "java_reference_identity"}
        stamp = str(time.time_ns() // 1_000_000)
        return {"ok": True, "type": kind, "request_id": request_id,
                "status": "SUCCEEDED", "queued_at_ms": stamp,
                "started_at_ms": stamp, "completed_at_ms": stamp, "result": result}


def _daemon_disk_environment(tmp_path, *, reserve_free=0, allowed_operation="parameter.set",
                             wrong_root=False, replace_root=False, operation="parameter.set",
                             arguments=None, estimate=64 * 1024, disk_enabled=True,
                             memory_resource_config=None):
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
                            estimate=estimate, reserve_free=reserve_free,
                            max_task=2 * 1024 * 1024)
    config_path = _write_disk_config(tmp_path / "resource-config", profile)
    service = ExecutionService(SessionLedger("session", "server"), _DaemonResourceAdapter(),
                               project_root=workspace_root)
    bound = service.bind_model("Model")
    model_ref = model_ref_from_mapping(bound["execution"]["model_ref"])
    daemon = ControlDaemon(
        home, service=service, worker=_DaemonResourceWorker(), registry={},
        project_root=workspace_root,
        disk_resource_config=config_path if disk_enabled else None,
        memory_resource_config=memory_resource_config,
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
    execution = {"project_id": project_id, "model_ref": model_ref.as_dict(), "expected_revision": 0,
                 "idempotency_key": "disk-resource-request", "request_id": "disk-resource-request"}
    if operation in {"api.invoke", "api_invoke"}:
        # Unit A's frozen public schema requires the exact session identity in
        # the outer envelope. This is the bound service ledger session, not a
        # newly fabricated project session.
        execution["session_id"] = model_ref.session_id
    request = {
        "operation": operation,
        "arguments": (arguments if arguments is not None else
                      {"parameters": [{"name": "resource_test", "expression": "1"}]}),
        # The default Worker lane is bound by the exact ModelRef. Only routes
        # whose public schema requires it carry the matching service session.
        "execution": execution,
    }
    return daemon, request, project_path, project_id, profile, config_path


def _dynamic_api_write_request():
    return {
        "path": {"segments": [{"collection": "feature", "tag": "resource_test"}]},
        "method": "comments",
        "arguments": [{"kind": "string", "shape": [], "data": "disk-admitted dynamic write"}],
        "declared_effect": "WRITE",
    }


def _fake_daemon_serve_environment(monkeypatch, daemon_type):
    from comsol_mcp import _control_daemon as daemon_module

    class FakeLock:
        def __init__(self, _path): pass
        def close(self): pass

    class FakeServer:
        server_port = 24974
        daemon_threads = False
        def serve_forever(self): pass

    monkeypatch.setattr(daemon_module, "ProcessLock", FakeLock)
    monkeypatch.setattr(daemon_module, "ControlDaemon", daemon_type)
    monkeypatch.setattr(daemon_module, "ThreadingHTTPServer", lambda *_args: FakeServer())
    monkeypatch.setattr(daemon_module, "process_identity", lambda _pid: {"start_epoch_ms": 100})
    return daemon_module


def test_control_daemon_serve_accepts_disk_config_and_preserves_unconfigured_startup(tmp_path, monkeypatch):
    observed = []

    class FakeDaemon:
        def __init__(self, _home, **options):
            observed.append(options)
            self._control_singleton_lock_held = False

    daemon_module = _fake_daemon_serve_environment(monkeypatch, FakeDaemon)
    monkeypatch.delenv("COMSOL_MCP_DISK_RESOURCE_CONFIG", raising=False)
    config = tmp_path / "trusted-disk-config.json"
    daemon_module.serve(tmp_path / "configured-home", disk_resource_config=config)
    daemon_module.serve(tmp_path / "legacy-home")
    assert observed == [{"disk_resource_config": config}, {}]


def test_control_daemon_serve_reads_disk_config_from_environment(tmp_path, monkeypatch):
    observed = []

    class FakeDaemon:
        def __init__(self, _home, **options):
            observed.append(options)
            self._control_singleton_lock_held = False

    daemon_module = _fake_daemon_serve_environment(monkeypatch, FakeDaemon)
    config = tmp_path / "disk-config-from-env.json"
    monkeypatch.setenv("COMSOL_MCP_DISK_RESOURCE_CONFIG", str(config))
    daemon_module.serve(tmp_path / "environment-home")
    assert observed == [{"disk_resource_config": str(config)}]


def test_control_daemon_cli_forwards_disk_resource_config(tmp_path, monkeypatch):
    from comsol_mcp import _control_daemon as daemon_module

    observed = []
    monkeypatch.setattr(daemon_module, "serve", lambda *args, **kwargs: observed.append((args, kwargs)))
    config = tmp_path / "disk-config-cli.json"
    daemon_module.main(["--home", str(tmp_path / "cli-home"),
                        "--disk-resource-config", str(config)])
    assert observed == [((str(tmp_path / "cli-home"),),
                         {"host_provider_config": None, "disk_resource_config": str(config),
                          "memory_resource_config": None, "windows_t037_profile": None})]


def test_control_daemon_rejects_invalid_disk_config_before_opening_listener(tmp_path, monkeypatch):
    from comsol_mcp import _control_daemon as daemon_module

    class FakeLock:
        def __init__(self, _path): pass
        def close(self): pass

    monkeypatch.setattr(daemon_module, "ProcessLock", FakeLock)
    monkeypatch.setattr(daemon_module, "ThreadingHTTPServer",
                        lambda *_args: pytest.fail("listener must not open with invalid trusted config"))
    config = tmp_path / "invalid-disk-config.json"
    config.write_text("{}", encoding="utf-8")
    with pytest.raises(ExecutionContractError) as refused:
        daemon_module.serve(tmp_path / "invalid-home", disk_resource_config=config)
    assert refused.value.code.startswith("DISK_RESOURCE_CONFIG")


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


@pytest.mark.skipif(os.name == "nt", reason="POSIX coordinator; Windows kernel adapter NOT_RUN")
def test_dynamic_api_write_with_low_budget_refuses_before_any_worker_send(tmp_path, monkeypatch):
    from comsol_mcp import _g3_ops

    monkeypatch.setattr(_g3_ops, "REQUIRES_ISOLATION", frozenset())
    daemon, request, _project, _project_id, _profile, _config = _daemon_disk_environment(
        tmp_path, allowed_operation="api.invoke", operation="api.invoke",
        arguments=_dynamic_api_write_request(), reserve_free=10**18,
    )
    try:
        result = daemon.dispatch(request)
        assert result["success"] is False
        assert result["error"]["code"] == "INSUFFICIENT_DISK_BUDGET", result
        assert result["data"]["engine_dispatched"] is False
        assert daemon.worker.submitted == []
        job_id = result["execution"]["job_id"]
        assert daemon.store.job(job_id)["status"] == "FAILED"
        assert daemon.store.get_operation(result["execution"]["operation_id"])["status"] == "FAILED"
        assert not any(item[0] == "RUNNING" for item in daemon.store.db.execute(
            "SELECT event FROM job_events WHERE job_id=?", (job_id,)).fetchall())
    finally:
        daemon.close()


@pytest.mark.skipif(os.name == "nt", reason="POSIX coordinator; Windows kernel adapter NOT_RUN")
def test_dynamic_api_write_with_sufficient_budget_dispatches_and_releases_terminal_reservation(tmp_path, monkeypatch):
    from comsol_mcp import _g3_ops

    monkeypatch.setattr(_g3_ops, "REQUIRES_ISOLATION", frozenset())
    daemon, request, _project, _project_id, _profile, _config = _daemon_disk_environment(
        tmp_path, allowed_operation="api.invoke", operation="api.invoke",
        arguments=_dynamic_api_write_request(),
    )
    # The product isolation verifier is an independent T037 gate. This test
    # exercises disk reservation and the actual software API route only; the
    # fixture marker below is explicitly not OS-sandbox evidence.
    monkeypatch.setattr(daemon.backend, "_require_g2_isolation",
                        lambda: {"scope": "SOFTWARE_FIXTURE_ONLY"})
    try:
        result = daemon.dispatch(request)
        assert result["success"] is True, result
        assert result["data"]["resolved_effect"] == "WRITE"
        assert result["data"]["readback"] == "disk-admitted dynamic write"
        assert daemon.worker.submitted
        job_id = result["execution"]["job_id"]
        assert daemon.store.job(job_id)["status"] == "SUCCEEDED"
        reservation_rows = [item for bound_job, item in resources._load_reservations(daemon.store.db).values()
                            if bound_job == job_id]
        assert len(reservation_rows) == 1 and reservation_rows[0]["status"] == "RELEASED"
        names = [row[0] for row in daemon.store.db.execute(
            "SELECT event FROM job_events WHERE job_id=? ORDER BY id", (job_id,)).fetchall()]
        assert names.index("W26DiskAdmission") < names.index("RUNNING") < names.index("worker_request")
        assert names.index("SUCCEEDED") < names.index("W26DiskReservationReleased")
    finally:
        daemon.close()


@pytest.mark.parametrize("identity_fault", ["different-reference", "wrong-generation"])
@pytest.mark.skipif(os.name == "nt", reason="POSIX coordinator; Windows kernel adapter NOT_RUN")
def test_dynamic_api_identity_fault_keeps_unknown_and_disk_reservation(tmp_path, monkeypatch,
                                                                     identity_fault):
    from comsol_mcp import _g3_ops

    monkeypatch.setattr(_g3_ops, "REQUIRES_ISOLATION", frozenset())
    daemon, request, _project, _project_id, _profile, _config = _daemon_disk_environment(
        tmp_path, allowed_operation="api.invoke", operation="api.invoke",
        arguments=_dynamic_api_write_request(),
    )
    monkeypatch.setattr(daemon.backend, "_require_g2_isolation",
                        lambda: {"scope": "SOFTWARE_FIXTURE_ONLY"})
    submit = daemon.worker.submit

    def faulty_identity_reply(kind, payload, **kwargs):
        reply = submit(kind, payload, **kwargs)
        if kind == "g2_entity_identity":
            if identity_fault == "different-reference":
                reply["result"]["same_reference"] = False
            else:
                reply["result"]["generation"] += 1
        return reply

    monkeypatch.setattr(daemon.worker, "submit", faulty_identity_reply)
    try:
        result = daemon.dispatch(request)
        assert result["success"] is False, result
        assert result["error"]["code"] == "EXECUTION_STATE_UNKNOWN", result
        assert result["error"]["safe_retry"] is False
        assert result["data"]["dispatch_stage"] == "post_dispatch"
        assert daemon.worker.comments["model:Model:feature:resource_test"] \
            == "disk-admitted dynamic write"
        assert any(kind == "g2_entity_identity" for kind, _payload in daemon.worker.submitted)
        job_id = result["execution"]["job_id"]
        assert daemon.store.job(job_id)["status"] == "UNKNOWN"
        rows = [item for bound_job, item in resources._load_reservations(daemon.store.db).values()
                if bound_job == job_id]
        assert len(rows) == 1 and rows[0]["status"] == "RESERVED"
        events = [row[0] for row in daemon.store.db.execute(
            "SELECT event FROM job_events WHERE job_id=? ORDER BY id", (job_id,)).fetchall()]
        assert events.index("W26DiskAdmission") < events.index("RUNNING") < events.index("worker_request")
        assert "SUCCEEDED" not in events and "W26DiskReservationReleased" not in events
    finally:
        daemon.close()


@pytest.mark.parametrize("operation", [
    "api.invoke", "result.numerical_manage", "result.table_manage", "plot.view_manage",
])
@pytest.mark.skipif(os.name == "nt", reason="POSIX trusted config and storage provider; Windows adapter NOT_RUN")
def test_all_catalog_dynamic_routes_require_configured_disk_reservation(tmp_path, operation):
    daemon, _request, _project, project_id, _profile, _config = _daemon_disk_environment(
        tmp_path, allowed_operation=operation, operation=operation,
    )
    try:
        assert resources._registered_operation_effect(operation) == "DYNAMIC"
        assert daemon._disk_resource_coordinator.requires_reservation(
            project_id=project_id, operation_id=operation,
        ) is True
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
        # Use a multi-MiB margin so incidental APFS metadata/test-temp churn
        # cannot consume the whole first-admission headroom between the
        # observation used to choose reserve_free and the pinned admission.
        estimate = 4 * 1024 * 1024
        # Admit one reservation, leaving enough live capacity for it but less
        # than another identical queued operation.
        free_headroom = max(0, sample.free_bytes - estimate - estimate // 2)
        profile = _disk_profile(project_id, project, operation="parameter.set", estimate=estimate,
                                reserve_free=free_headroom, max_task=8 * 1024 * 1024)
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


@pytest.mark.skipif(memory_resources._current_platform() != "macos",
                    reason="native memory integration currently exercised on macOS; Windows kernel NOT_RUN")
def test_memory_only_admission_precedes_worker_and_terminal_release_is_durable(tmp_path, monkeypatch):
    from comsol_mcp import _g3_ops

    monkeypatch.setattr(_g3_ops, "REQUIRES_ISOLATION", frozenset())
    memory_config = _write_memory_config(
        tmp_path, operation="api.invoke", estimate=1, max_total=1024, max_task=1024,
    )
    daemon, request, _project, _project_id, _profile, _disk_config = _daemon_disk_environment(
        tmp_path, allowed_operation="api.invoke", operation="api.invoke",
        arguments=_dynamic_api_write_request(), disk_enabled=False,
        memory_resource_config=memory_config,
    )
    # Admission is independent of the execution deadline: null means the
    # engine operation has no automatic timeout, not that resource accounting
    # is disabled.
    request["execution"]["execution_timeout_s"] = None
    monkeypatch.setattr(daemon.backend, "_require_g2_isolation",
                        lambda: {"scope": "SOFTWARE_FIXTURE_ONLY"})
    try:
        result = daemon.dispatch(request)
        assert result["success"] is True, result
        assert result["data"]["resolved_effect"] == "WRITE"
        job_id = result["execution"]["job_id"]
        assert daemon.store.job(job_id)["status"] == "SUCCEEDED"
        rows = [item for bound_job, item in memory_resources._load_memory_reservations(
            daemon.store.db).values() if bound_job == job_id]
        assert len(rows) == 1 and rows[0]["status"] == "RELEASED"
        events = [row[0] for row in daemon.store.db.execute(
            "SELECT event FROM job_events WHERE job_id=? ORDER BY id", (job_id,)).fetchall()]
        assert events.index("W26MemoryReservation") < events.index("W26MemoryAdmission")
        assert events.index("W26MemoryAdmission") < events.index("RUNNING") < events.index("worker_request")
        assert events.index("SUCCEEDED") < events.index("W26MemoryReservationReleased")
        assert rows[0]["hard_cap_state"] == "NOT_OBSERVED"
    finally:
        daemon.close()


@pytest.mark.skipif(memory_resources._current_platform() != "macos",
                    reason="native memory integration currently exercised on macOS; Windows kernel NOT_RUN")
def test_sufficient_disk_and_memory_admission_commit_together_and_release_after_terminal(tmp_path, monkeypatch):
    from comsol_mcp import _g3_ops

    monkeypatch.setattr(_g3_ops, "REQUIRES_ISOLATION", frozenset())
    memory_config = _write_memory_config(
        tmp_path, operation="api.invoke", estimate=1, max_total=1024, reserve_available=0,
        max_task=1024,
    )
    daemon, request, _project, _project_id, _profile, _disk_config = _daemon_disk_environment(
        tmp_path, allowed_operation="api.invoke", operation="api.invoke",
        arguments=_dynamic_api_write_request(), memory_resource_config=memory_config,
    )
    monkeypatch.setattr(daemon.backend, "_require_g2_isolation",
                        lambda: {"scope": "SOFTWARE_FIXTURE_ONLY"})
    try:
        result = daemon.dispatch(request)
        assert result["success"] is True, result
        assert result["data"]["resolved_effect"] == "WRITE"
        assert daemon.worker.submitted
        job_id = result["execution"]["job_id"]
        assert daemon.store.job(job_id)["status"] == "SUCCEEDED"

        disk_rows = [item for bound_job, item in resources._load_reservations(daemon.store.db).values()
                     if bound_job == job_id]
        memory_rows = [item for bound_job, item in memory_resources._load_memory_reservations(
            daemon.store.db).values() if bound_job == job_id]
        assert len(disk_rows) == len(memory_rows) == 1
        assert disk_rows[0]["status"] == memory_rows[0]["status"] == "RELEASED"

        events = [row[0] for row in daemon.store.db.execute(
            "SELECT event FROM job_events WHERE job_id=? ORDER BY id", (job_id,)).fetchall()]
        assert events.index("W26DiskAdmission") < events.index("W26MemoryAdmission")
        assert events.index("W26MemoryAdmission") < events.index("RUNNING") < events.index("worker_request")
        assert events.index("SUCCEEDED") < events.index("W26DiskReservationReleased")
        assert events.index("SUCCEEDED") < events.index("W26MemoryReservationReleased")
        assert disk_rows[0]["enforcement"] == "ADMISSION_ONLY"
        assert memory_rows[0]["hard_cap_state"] == "NOT_OBSERVED"
    finally:
        daemon.close()


@pytest.mark.skipif(memory_resources._current_platform() != "macos",
                    reason="native memory integration currently exercised on macOS; Windows kernel NOT_RUN")
def test_low_memory_in_combined_admission_rolls_back_disk_before_running(tmp_path, monkeypatch):
    from comsol_mcp import _g3_ops

    monkeypatch.setattr(_g3_ops, "REQUIRES_ISOLATION", frozenset())
    memory_config = _write_memory_config(
        tmp_path, operation="api.invoke", estimate=1, max_total=1,
        reserve_available=2**62, max_task=1,
    )
    daemon, request, _project, _project_id, _profile, _disk_config = _daemon_disk_environment(
        tmp_path, allowed_operation="api.invoke", operation="api.invoke",
        arguments=_dynamic_api_write_request(), memory_resource_config=memory_config,
    )
    try:
        result = daemon.dispatch(request)
        assert result["success"] is False, result
        assert result["error"]["code"] == "INSUFFICIENT_MEMORY_BUDGET", result
        assert result["data"]["engine_dispatched"] is False
        assert daemon.worker.submitted == []
        job_id = result["execution"]["job_id"]
        events = [row[0] for row in daemon.store.db.execute(
            "SELECT event FROM job_events WHERE job_id=? ORDER BY id", (job_id,)).fetchall()]
        assert "RUNNING" not in events and "worker_request" not in events
        assert not any(bound_job == job_id for bound_job, _item in resources._load_reservations(daemon.store.db).values())
        assert not any(bound_job == job_id for bound_job, _item in memory_resources._load_memory_reservations(
            daemon.store.db).values())
    finally:
        daemon.close()


@pytest.mark.skipif(memory_resources._current_platform() != "macos",
                    reason="native memory integration currently exercised on macOS; Windows kernel NOT_RUN")
def test_low_disk_in_combined_admission_never_writes_memory_reservation(tmp_path, monkeypatch):
    from comsol_mcp import _g3_ops

    monkeypatch.setattr(_g3_ops, "REQUIRES_ISOLATION", frozenset())
    memory_config = _write_memory_config(
        tmp_path, operation="api.invoke", estimate=1, max_total=1024, reserve_available=0,
        max_task=1024,
    )
    daemon, request, _project, _project_id, _profile, _disk_config = _daemon_disk_environment(
        tmp_path, reserve_free=2**62, allowed_operation="api.invoke", operation="api.invoke",
        arguments=_dynamic_api_write_request(), memory_resource_config=memory_config,
    )
    try:
        result = daemon.dispatch(request)
        assert result["success"] is False, result
        assert result["error"]["code"] == "INSUFFICIENT_DISK_BUDGET", result
        assert result["data"]["engine_dispatched"] is False
        assert daemon.worker.submitted == []
        job_id = result["execution"]["job_id"]
        events = [row[0] for row in daemon.store.db.execute(
            "SELECT event FROM job_events WHERE job_id=? ORDER BY id", (job_id,)).fetchall()]
        assert "W26DiskAdmission" not in events and "W26MemoryAdmission" not in events
        assert "RUNNING" not in events and "worker_request" not in events
        assert not any(bound_job == job_id for bound_job, _item in resources._load_reservations(
            daemon.store.db).values())
        assert not any(bound_job == job_id for bound_job, _item in memory_resources._load_memory_reservations(
            daemon.store.db).values())
    finally:
        daemon.close()


@pytest.mark.skipif(memory_resources._current_platform() != "macos",
                    reason="native memory integration currently exercised on macOS; Windows kernel NOT_RUN")
def test_stale_memory_observation_after_prepare_rolls_back_both_reservations_before_dispatch(
        tmp_path, monkeypatch):
    from types import SimpleNamespace
    from comsol_mcp import _g3_ops

    monkeypatch.setattr(_g3_ops, "REQUIRES_ISOLATION", frozenset())
    memory_config = _write_memory_config(
        tmp_path, operation="api.invoke", estimate=1, max_total=1024, max_task=1024,
    )
    daemon, request, _project, _project_id, _profile, _disk_config = _daemon_disk_environment(
        tmp_path, allowed_operation="api.invoke", operation="api.invoke",
        arguments=_dynamic_api_write_request(), memory_resource_config=memory_config,
    )
    monkeypatch.setattr(daemon.backend, "_require_g2_isolation",
                        lambda: {"scope": "SOFTWARE_FIXTURE_ONLY"})
    coordinator = daemon._memory_resource_coordinator
    prepare = coordinator.prepare_queued_job
    prepared_rows = []

    def prepare_then_advance_clock(job_id):
        prepared = prepare(job_id)
        prepared_rows.append(prepared)
        observed = prepared["observation"]
        # Advance only the memory module's reservation-time clock after the
        # native sample has been captured. Disk admission and daemon clocks
        # remain real; no sleep or timing race is involved.
        monkeypatch.setattr(memory_resources, "time", SimpleNamespace(
            monotonic_ns=lambda: observed.observed_monotonic_ns
            + coordinator.max_observation_age_ns + 1,
            get_clock_info=time.get_clock_info,
        ))
        return prepared

    monkeypatch.setattr(coordinator, "prepare_queued_job", prepare_then_advance_clock)
    try:
        result = daemon.dispatch(request)
        assert prepared_rows and prepared_rows[0]["observation"]._attestation \
            is memory_resources._NATIVE_OBSERVATION_ATTESTATION
        assert result["success"] is False, result
        assert result["error"]["code"] == "MEMORY_OBSERVATION_STALE", result
        assert result["data"]["engine_dispatched"] is False
        assert daemon.worker.submitted == []
        job_id = result["execution"]["job_id"]
        names = [row[0] for row in daemon.store.db.execute(
            "SELECT event FROM job_events WHERE job_id=? ORDER BY id", (job_id,)).fetchall()]
        assert "W26DiskReservation" not in names
        assert "W26MemoryReservation" not in names
        assert "W26DiskAdmission" not in names and "W26MemoryAdmission" not in names
        assert "RUNNING" not in names and "worker_request" not in names
        assert not any(bound_job == job_id for bound_job, _item in resources._load_reservations(
            daemon.store.db).values())
        assert not any(bound_job == job_id for bound_job, _item in memory_resources._load_memory_reservations(
            daemon.store.db).values())
    finally:
        daemon.close()


@pytest.mark.parametrize("terminal_status", ["LOST", "EXPIRED", "CANCELLED"])
@pytest.mark.skipif(memory_resources._current_platform() != "macos",
                    reason="native memory integration currently exercised on macOS; Windows kernel NOT_RUN")
def test_dispatched_lost_or_expired_memory_reservation_stays_held(tmp_path, monkeypatch,
                                                                  terminal_status):
    from comsol_mcp import _g3_ops

    monkeypatch.setattr(_g3_ops, "REQUIRES_ISOLATION", frozenset())
    memory_config = _write_memory_config(
        tmp_path, operation="parameter.set", estimate=1, max_total=1024, max_task=1024,
    )
    daemon, request, _project, _project_id, _profile, _disk_config = _daemon_disk_environment(
        tmp_path, allowed_operation="parameter.set", operation="parameter.set",
        memory_resource_config=memory_config, disk_enabled=False,
    )
    daemon.worker.readback_override = "mismatched-readback"
    try:
        result = daemon.dispatch(request)
        assert result["success"] is False
        job_id = result["execution"]["job_id"]
        assert daemon.store.job(job_id)["status"] == "UNKNOWN"
        assert daemon.worker.submitted
        operation_id = result["execution"]["operation_id"]
        daemon.store.db.execute("UPDATE jobs SET status=? WHERE job_id=?", (terminal_status, job_id))
        daemon.store.db.execute("UPDATE operations SET status=? WHERE operation_id=?",
                                (terminal_status, operation_id))
        daemon.store.add_event(job_id, terminal_status, {
            "operation_id": operation_id, "engine_dispatched": True,
            "test_control": "dispatched terminal label without quiescence",
        })

        outcome = daemon._memory_resource_coordinator.release_terminal_if_reserved(job_id)
        assert outcome is not None
        expected_hold = ("HELD_DISPATCH_UNPROVEN" if terminal_status == "CANCELLED"
                         else "HELD_QUIESCENCE_UNPROVEN")
        assert outcome["status"] == expected_hold
        assert outcome["reservation_id"]
        assert outcome["idempotent"] is False
        assert outcome["domain_status"] == terminal_status
        rows = [item for bound_job, item in memory_resources._load_memory_reservations(
            daemon.store.db).values() if bound_job == job_id]
        assert len(rows) == 1 and rows[0]["status"] == "RESERVED"
    finally:
        daemon.close()


@pytest.mark.skipif(memory_resources._current_platform() != "macos",
                    reason="native memory integration currently exercised on macOS; Windows kernel NOT_RUN")
def test_durable_queued_abort_proof_releases_memory_reservation_without_worker_send(tmp_path):
    memory_config = _write_memory_config(
        tmp_path, operation="parameter.set", estimate=1, max_total=1024, max_task=1024,
    )
    daemon, _request, _project, project_id, _profile, _disk_config = _daemon_disk_environment(
        tmp_path, allowed_operation="parameter.set", operation="parameter.set",
        memory_resource_config=memory_config, disk_enabled=False,
    )
    try:
        queued = _queued_dispatch_job(daemon.store, project_id,
                                     inner="parameter.set", wrapper="operation_call")
        reserved = daemon._memory_resource_coordinator.reserve_queued_job(queued["job_id"])
        assert reserved["status"] == "RESERVED"
        cancelled, status, _job = daemon.store.cancel_queued(queued["job_id"])
        assert cancelled and status == "CANCELLED"

        outcome = daemon._memory_resource_coordinator.release_terminal_if_reserved(queued["job_id"])
        assert outcome is not None and outcome["status"] == "RELEASED"
        rows = [item for bound_job, item in memory_resources._load_memory_reservations(
            daemon.store.db).values() if bound_job == queued["job_id"]]
        assert len(rows) == 1 and rows[0]["status"] == "RELEASED"
        assert daemon.worker.submitted == []
        cancel_event = daemon.store.db.execute(
            "SELECT metadata FROM job_events WHERE job_id=? AND event='CANCELLED'",
            (queued["job_id"],),
        ).fetchone()
        assert cancel_event is not None
        proof = json.loads(cancel_event["metadata"])
        assert proof["engine_dispatched"] is False
        assert proof["cancelled_while"] == "QUEUED" and proof["mode"] == "QUEUED_ABORT"
    finally:
        daemon.close()


@pytest.mark.skipif(memory_resources._current_platform() != "macos",
                    reason="native memory integration currently exercised on macOS; Windows kernel NOT_RUN")
def test_memory_unknown_reservation_survives_restart_and_counts_other_projects(tmp_path, monkeypatch):
    from comsol_mcp import _g3_ops

    monkeypatch.setattr(_g3_ops, "REQUIRES_ISOLATION", frozenset())
    memory_config = _write_memory_config(
        tmp_path, operation="parameter.set", estimate=1, max_total=1,
        reserve_available=0, max_task=1,
    )
    daemon, request, _project, _project_id, _profile, _disk_config = _daemon_disk_environment(
        tmp_path, allowed_operation="parameter.set", operation="parameter.set",
        memory_resource_config=memory_config, disk_enabled=False,
    )
    daemon.worker.readback_override = "mismatched-readback"
    home = daemon.home
    try:
        result = daemon.dispatch(request)
        assert result["success"] is False
        assert result["error"].get("safe_retry") is False
        job_id = result["execution"]["job_id"]
        assert daemon.store.job(job_id)["status"] == "UNKNOWN"
        rows = [item for bound_job, item in memory_resources._load_memory_reservations(
            daemon.store.db).values() if bound_job == job_id]
        assert len(rows) == 1 and rows[0]["status"] == "RESERVED"
        assert not any(event[0] == "W26MemoryReservationReleased" for event in daemon.store.db.execute(
            "SELECT event FROM job_events WHERE job_id=?", (job_id,)).fetchall())

        # The active reservation is host-wide: an independently registered
        # project cannot evade it by changing its project identifier.
        second = daemon.project_authority.dispatch("project.create", {
            "label": "memory cross-project admission", "workspace": "project-two",
            "policy": {"permissions": ["inspect", "project_write", "compute"]},
        })["data"]["project"]
        competing = _queued_dispatch_job(daemon.store, second["project_id"],
                                         inner="parameter.set", wrapper="operation_call")
        with pytest.raises(ExecutionContractError) as refused:
            daemon._memory_resource_coordinator.reserve_queued_job(competing["job_id"])
        assert refused.value.code == "INSUFFICIENT_MEMORY_BUDGET"
        assert daemon.worker.submitted  # only the earlier UNKNOWN call reached Worker
        competing_events = [row[0] for row in daemon.store.db.execute(
            "SELECT event FROM job_events WHERE job_id=?", (competing["job_id"],)).fetchall()]
        assert "W26MemoryReservation" not in competing_events
    finally:
        daemon.close()

    restarted = ControlDaemon(home, memory_resource_config=memory_config)
    try:
        rows = [item for bound_job, item in memory_resources._load_memory_reservations(
            restarted.store.db).values() if bound_job == job_id]
        assert len(rows) == 1 and rows[0]["status"] == "RESERVED"
        assert restarted._memory_resource_coordinator.active_reserved_bytes() == 1
        assert restarted.store.job(job_id)["status"] == "RECONCILING"
    finally:
        restarted.close()


def test_control_daemon_memory_config_startup_and_cli_wiring(tmp_path, monkeypatch):
    from comsol_mcp import _control_daemon as daemon_module

    observed = []

    class FakeDaemon:
        def __init__(self, _home, **options):
            observed.append(options)
            self._control_singleton_lock_held = False

    daemon_module = _fake_daemon_serve_environment(monkeypatch, FakeDaemon)
    monkeypatch.delenv("COMSOL_MCP_MEMORY_RESOURCE_CONFIG", raising=False)
    config = tmp_path / "memory-config.json"
    daemon_module.serve(tmp_path / "memory-home", memory_resource_config=config)
    assert observed == [{"memory_resource_config": config}]

    cli_seen = []
    monkeypatch.setattr(daemon_module, "serve", lambda *args, **kwargs: cli_seen.append((args, kwargs)))
    daemon_module.main(["--home", str(tmp_path / "memory-cli-home"),
                        "--memory-resource-config", str(config)])
    assert cli_seen == [((str(tmp_path / "memory-cli-home"),), {
        "host_provider_config": None, "disk_resource_config": None,
        "memory_resource_config": str(config), "windows_t037_profile": None,
    })]
