from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
import sys
import time

import pytest

from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp import _w26_memory_resources as memory


def _profile(*, operations=("study.run",), estimates=(64,), host="a" * 64,
             platform="macos", budget=None):
    budget = budget or memory.MemoryBudgetPolicy(1024, 8, 512)
    return memory.MemoryResourceProfile(
        "memory-test", host, platform, budget, tuple(operations),
        tuple(memory.MemoryOperationEstimate(operation, size)
              for operation, size in zip(operations, estimates)),
        "b" * 64,
    )


def _synthetic_observation(*, host="a" * 64, platform="macos", total=2048,
                           available=256, observed_ns=100, boot_id="c" * 64):
    return memory.HostMemoryObservation(
        platform=platform, host_id=host, boot_id=boot_id,
        boot_identity_semantics="PROTOCOL_FIXTURE_ONLY", provider_id="protocol-fixture-only",
        observed_monotonic_ns=observed_ns,
        monotonic_clock_id=time.get_clock_info("monotonic").implementation,
        total_physical_bytes=total, available_physical_bytes=available,
        available_semantics="PROTOCOL_FIXTURE_ONLY", hard_cap_state="NOT_OBSERVED",
        hard_cap_bytes=None, evidence_classification="SOFTWARE_TEST_SYNTHETIC",
    )


def test_memory_profile_parser_is_closed_and_never_attests_trust():
    doc = {
        "schema": memory.MEMORY_PROFILE_SCHEMA,
        "profile_id": "memory-prod",
        "host_id": "a" * 64,
        "platform": "macos",
        "budget": {"max_total_reserved_bytes": 1024,
                   "reserve_available_bytes": 128,
                   "max_task_reservation_bytes": 512},
        "allowed_operations": ["study.run"],
        "trusted_estimates": [{"operation_id": "study.run", "estimate_bytes": 64}],
    }
    profile = memory.parse_memory_profile_document(
        doc, actual_host_id="a" * 64, actual_platform="macos", source_sha256="d" * 64,
    )
    assert profile.trusted_startup_config is False
    assert profile.estimate_for("study.run") == 64
    with pytest.raises(ExecutionContractError):
        memory.parse_memory_profile_document(
            {**doc, "unexpected": True}, actual_host_id="a" * 64,
            actual_platform="macos", source_sha256="d" * 64,
        )
    with pytest.raises(ExecutionContractError):
        memory.parse_memory_profile_document(
            {**doc, "host_id": "e" * 64}, actual_host_id="a" * 64,
            actual_platform="macos", source_sha256="d" * 64,
        )


def test_profile_rejects_duplicate_or_generic_operation_estimates():
    with pytest.raises(ValueError):
        memory.MemoryResourceProfile(
            "memory-test", "a" * 64, "macos", memory.MemoryBudgetPolicy(1024, 0, 512),
            ("study.run",),
            (memory.MemoryOperationEstimate("study.run", 1),
             memory.MemoryOperationEstimate("study.run", 1)),
            "b" * 64,
        )
    with pytest.raises(ValueError):
        memory.MemoryOperationEstimate("operation_call", 10)
    with pytest.raises(ValueError):
        memory.MemoryBudgetPolicy(0, 0, 1)


@pytest.mark.skipif(sys.platform not in {"darwin", "win32"}, reason="native startup identity only")
def test_trusted_memory_config_loader_binds_current_host_and_private_file(tmp_path):
    platform_name = memory._current_platform()
    host_id = memory._current_host_fingerprint(platform_name)
    document = {
        "schema": memory.MEMORY_PROFILE_SCHEMA,
        "profile_id": "memory-startup-test",
        "host_id": host_id,
        "platform": platform_name,
        "budget": {"max_total_reserved_bytes": 1024,
                   "reserve_available_bytes": 0,
                   "max_task_reservation_bytes": 512},
        "allowed_operations": ["study.run"],
        "trusted_estimates": [{"operation_id": "study.run", "estimate_bytes": 64}],
    }
    directory = tmp_path / "private-memory-config"
    directory.mkdir(mode=0o700)
    directory.chmod(0o700)
    path = directory / "profile.json"
    raw = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
    path.write_bytes(raw)
    path.chmod(0o600)
    loaded = memory.TrustedMemoryResourceConfig.load(path)
    assert loaded.trusted_startup_source is True
    assert loaded.profile.trusted_startup_config is True
    assert loaded.source_sha256 == hashlib.sha256(raw).hexdigest()
    assert loaded.profile.host_id == host_id


def test_injected_os_observation_is_synthetic_and_cannot_reserve(monkeypatch):
    class FakeApi:
        provider_id = "synthetic-test-api"

        def read(self):
            return {"host_id": "a" * 64, "boot_id": "c" * 64,
                    "boot_identity_semantics": "SYNTHETIC",
                    "total_physical_bytes": 2048, "available_physical_bytes": 1024,
                    "available_semantics": "SYNTHETIC"}

    provider = memory.HostMemoryObservationProvider(
        _platform_name="macos", _api=FakeApi(), _host_id_reader=lambda: "a" * 64,
        _clock=lambda: 1000,
    )
    observation = provider.read_observation()
    assert observation.evidence_classification == "SOFTWARE_TEST_SYNTHETIC"
    assert observation._attestation is None
    trusted_profile = replace(_profile(), _trusted_attestation=memory._TRUSTED_PROFILE_ATTESTATION)
    with pytest.raises(ExecutionContractError) as refused:
        memory.assess_memory_admission(
            trusted_profile, observation, "study.run", active_reserved_bytes=0,
            now_monotonic_ns=1001,
        )
    assert refused.value.code == "MEMORY_OBSERVATION_UNVERIFIED"


def test_synthetic_protocol_only_headroom_math_and_refusal(monkeypatch):
    # This monkeypatch exercises the policy arithmetic only; the fixture is not
    # an OS receipt and must never be used to authorize production dispatch.
    marker = object()
    monkeypatch.setattr(memory, "_NATIVE_OBSERVATION_ATTESTATION", marker)
    profile = replace(
        _profile(operations=("study.run",), estimates=(50,),
                 budget=memory.MemoryBudgetPolicy(200, 10, 100)),
        _trusted_attestation=memory._TRUSTED_PROFILE_ATTESTATION,
    )
    observed = replace(_synthetic_observation(available=100),
                       evidence_classification="HOST_OBSERVED_ADMISSION_ONLY",
                       _attestation=marker)
    accepted = memory.assess_memory_admission(
        profile, observed, "study.run", active_reserved_bytes=0, now_monotonic_ns=101,
    )
    assert accepted.requested_bytes == 50
    assert accepted.available_bytes == 90
    assert accepted.evidence_classification == "HOST_OBSERVED_ADMISSION_ONLY"
    with pytest.raises(ExecutionContractError) as refused:
        memory.assess_memory_admission(
            profile, observed, "study.run", active_reserved_bytes=80, now_monotonic_ns=101,
        )
    assert refused.value.code == "INSUFFICIENT_MEMORY_BUDGET"
    stale = replace(observed, observed_monotonic_ns=1)
    with pytest.raises(ExecutionContractError) as refused:
        memory.assess_memory_admission(
            profile, stale, "study.run", active_reserved_bytes=0,
            now_monotonic_ns=memory.MEMORY_OBSERVATION_MAX_AGE_NS + 2,
        )
    assert refused.value.code == "MEMORY_OBSERVATION_STALE"
    wrong_clock = replace(observed, monotonic_clock_id="unrelated-monotonic-clock")
    with pytest.raises(ExecutionContractError) as refused:
        memory.assess_memory_admission(
            profile, wrong_clock, "study.run", active_reserved_bytes=0, now_monotonic_ns=101,
        )
    assert refused.value.code == "MEMORY_OBSERVATION_CLOCK_UNKNOWN"


def test_unknown_and_other_project_reservations_share_host_wide_ledger():
    class Rows:
        def __init__(self, rows):
            self._rows = rows

        def fetchall(self):
            return self._rows

    def reservation(job_id, project_id, amount):
        return {"schema": memory.MEMORY_RESERVATION_SCHEMA,
                "reservation_id": hashlib.sha256(job_id.encode()).hexdigest(),
                "status": "RESERVED", "job_id": job_id, "operation_id": "study.run",
                "project_id": project_id, "host_id": "a" * 64, "boot_id": "c" * 64,
                "profile_id": "memory-test", "profile_sha256": "b" * 64,
                "requested_bytes": amount, "observation_sha256": "d" * 64,
                "at_monotonic_ns": 100, "monotonic_clock_id": "mach_absolute_time()",
                "available_bytes": 512,
                "evidence_classification": "HOST_OBSERVED_ADMISSION_ONLY",
                "hard_cap_state": "NOT_OBSERVED"}

    events = [reservation("job-1", "project-a", 100),
              reservation("job-2", "project-b", 200)]

    class FakeDB:
        def execute(self, _query, _parameters):
            return Rows([{"id": index, "job_id": item["job_id"],
                          "metadata": json.dumps(item)} for index, item in enumerate(events, 1)])

    ledger = memory._load_memory_reservations(FakeDB())
    assert len(ledger) == 2
    assert {item["project_id"] for _, item in ledger.values()} == {"project-a", "project-b"}
    assert sum(item["requested_bytes"] for _, item in ledger.values()
               if item["host_id"] == "a" * 64 and item["status"] == "RESERVED") == 300


def test_platform_native_abi_layouts_are_bounded_and_explicit():
    assert memory.ctypes.sizeof(memory._WinMemoryStatusEx) == 64
    assert memory._WinMemoryStatusEx.ullAvailPhys.offset == 16
    assert memory.ctypes.sizeof(memory._DarwinTimeval) == 16
    assert memory.ctypes.sizeof(memory._DarwinVmStatsRev0) == 96
    assert memory._DarwinVmStatsRev0.free_count.offset == 0
    assert memory._DarwinVmStatsRev0.speculative_count.offset == 92


@pytest.mark.skipif(sys.platform != "darwin", reason="Darwin native observer only")
def test_current_darwin_observation_is_native_admission_only_when_available():
    try:
        observation = memory.HostMemoryObservationProvider().read_observation()
    except ExecutionContractError as exc:
        pytest.skip(f"read-only Darwin Mach/sysctl observation unavailable: {exc.code}")
    assert observation.evidence_classification == "HOST_OBSERVED_ADMISSION_ONLY"
    assert observation._attestation is memory._NATIVE_OBSERVATION_ATTESTATION
    assert observation.available_physical_bytes <= observation.total_physical_bytes
    assert observation.hard_cap_state == "NOT_OBSERVED"
    assert observation.hard_cap_bytes is None
    assert observation.observed_monotonic_ns <= time.monotonic_ns()
    assert observation.boot_identity_semantics == "KERN_BOOTTIME_SYSCTL"
