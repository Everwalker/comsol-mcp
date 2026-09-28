#!/usr/bin/env python3
"""Run the separately frozen W24 cure-science campaign on one owned server.

The runner will not create a work directory or inspect/start COMSOL until the
setup-only receipt names an intact native configured template and the complete
COMSOL-side API map needed by the acceptance gates.  Planned cases never count
as solves: only durable ``study_run_submitted`` ledger entries do.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib
import json
import math
import os
import re
import shutil
import stat
import struct
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Protocol, Sequence
from zoneinfo import ZoneInfo
from uuid import uuid4


REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
PLAN = REPO / "docs/full_project_execution/w24/W24_CURE_FIXTURE_PROPOSAL.md"
SCIENCE_FIXTURE = REPO / "tools/java/W24CureScienceFixture.java"
V2_CONTROL_FIXTURE = REPO / "tools/java/W24CureLawV2ControlFixture.java"
COUPON_FIXTURE = REPO / "tools/java/W24CureCouponFixture.java"
SETUP_RUNNER = REPO / "tools/run_native_w24_cure_preflight.py"
MAX_BIRTH_BUDGET_S = 3600.0
MAX_STUDY_RUN_SUBMISSIONS = 10
MAX_SEQUENTIAL_WORKERS = 2
CLEANUP_RESERVE_S = 90.0
EXPECTED_ENGINE = "COMSOL Multiphysics 6.4.0.293"
CANDIDATE_ID = "W24-CURE-HISTORY-AXISYM-SCIENCE-01"
APPROVAL_STATUS = "ROOT_APPROVED_FOR_EXACT_FROZEN_CANDIDATE"
SCIENCE_EVIDENCE_ROOT = REPO / "docs/full_project_execution/w24/evidence"
INPUT_MODEL_NAMES = (
    "staged_baseline", "continuous_comparator", "reset_negative_control",
    "coarse_mesh", "tight_time", "dose_sensitivity", "mechanics_parent",
)
TIME_TOLERANCE_S = 1e-12
SHA256_RE = re.compile(r"[0-9a-f]{64}")
STRESS_ROLES = ("radial_normal", "hoop_normal", "axial_normal", "rz_shear")
STRESS_PROBE_COORDINATES_M = ((25e-6, 520e-6), (50e-6, 530e-6), (75e-6, 540e-6))
CURE_FIELD_NAMES = {
    "temperature": ("comp1.T",),
    "alpha": ("comp1.alpha",),
    "qpost": ("comp1.qpost",),
    "displacement_r": ("comp1.u",),
    "displacement_z": ("comp1.w",),
}
V2_HISTORY_ABS_TOLERANCES = {
    "T": 1e-6, "alpha": 1e-12, "Duv_rel": 1e-12, "qpost": 1e-12,
    "u": 1e-15, "w": 1e-15,
    "solid.isactive": 0.0, "solid.wasactive": 0.0,
}
T0_K = 298.15


class CampaignError(RuntimeError):
    """A frozen campaign input, receipt, or native action is incomplete."""


class PrebirthRefusal(CampaignError):
    """An explicit validation/setup gate refused work before server launch."""


def _capture_value_sha256(value: Any, memo: dict[int, str] | None = None) -> str:
    """Hash decoded capture content as a Merkle walk without a JSON copy."""
    if memo is None:
        memo = {}
    container = isinstance(value, Mapping) or (
        isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)))
    if container and id(value) in memo:
        return memo[id(value)]
    digest = hashlib.sha256()
    if value is None:
        digest.update(b"N")
    elif isinstance(value, bool):
        digest.update(b"B1" if value else b"B0")
    elif isinstance(value, int):
        raw = str(value).encode("ascii")
        digest.update(b"I" + struct.pack(">I", len(raw)) + raw)
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise CampaignError("authenticated capture digest refuses a nonfinite number")
        digest.update(b"F" + struct.pack(">d", value))
    elif isinstance(value, str):
        raw = value.encode("utf-8")
        digest.update(b"S" + struct.pack(">I", len(raw)) + raw)
    elif isinstance(value, Mapping):
        digest.update(b"M" + struct.pack(">I", len(value)))
        for key in sorted(value):
            if not isinstance(key, str):
                raise CampaignError("authenticated capture digest requires string object keys")
            digest.update(bytes.fromhex(_capture_value_sha256(key, memo)))
            digest.update(bytes.fromhex(_capture_value_sha256(value[key], memo)))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        digest.update(b"L" + struct.pack(">Q", len(value)))
        for item in value:
            digest.update(bytes.fromhex(_capture_value_sha256(item, memo)))
    else:
        raise CampaignError(f"authenticated capture digest does not support {type(value).__name__}")
    result = digest.hexdigest()
    if container:
        memo[id(value)] = result
    return result


def _authenticated_capture_frame_sha256(frame: Mapping[str, Any],
                                        memo: dict[int, str] | None = None) -> str:
    return _capture_value_sha256(frame, memo)


@dataclass(frozen=True)
class SolveSlot:
    case_id: str
    study_tag: str
    purpose: str


@dataclass(frozen=True)
class BirthBudget:
    """A wall-clock budget tied to one exact server birth, including cleanup."""

    birth_epoch_s: float
    budget_s: float = MAX_BIRTH_BUDGET_S
    cleanup_reserve_s: float = CLEANUP_RESERVE_S

    @property
    def deadline_epoch_s(self) -> float:
        return self.birth_epoch_s + self.budget_s

    def rpc_timeout_s(self, preferred_s: float, *, now_epoch_s: float | None = None) -> float:
        now = _current_epoch_s() if now_epoch_s is None else float(now_epoch_s)
        usable = self.deadline_epoch_s - now - self.cleanup_reserve_s
        if not math.isfinite(usable) or usable <= 1.0:
            raise CampaignError("science campaign is in its reserved cleanup window; no new native request may start")
        if not math.isfinite(preferred_s) or preferred_s <= 0:
            raise CampaignError("preferred native RPC timeout must be positive and finite")
        return min(float(preferred_s), usable)

    def receipt(self, *, now_epoch_s: float | None = None) -> dict[str, float]:
        now = _current_epoch_s() if now_epoch_s is None else float(now_epoch_s)
        return {
            "birth_epoch_s": self.birth_epoch_s,
            "deadline_epoch_s": self.deadline_epoch_s,
            "budget_s": self.budget_s,
            "cleanup_reserve_s": self.cleanup_reserve_s,
            "elapsed_from_birth_s": max(0.0, now - self.birth_epoch_s),
            "remaining_to_deadline_s": self.deadline_epoch_s - now,
        }


@dataclass(frozen=True)
class ManagedModelBinding:
    """Exact production model identity returned by project-scoped adoption."""

    project_id: str
    session_id: str
    model_ref: Mapping[str, Any]
    revision: int

    @property
    def model_tag(self) -> str:
        return str(self.model_ref["model_tag"])

    @classmethod
    def from_adopt_response(cls, response: Any, *, project_id: str,
                            expected_model_tag: str) -> "ManagedModelBinding":
        if not isinstance(response, Mapping) or response.get("success") is not True:
            raise CampaignError("canonical model.adopt did not return success=true")
        execution = response.get("execution")
        if not isinstance(execution, Mapping):
            raise CampaignError("canonical model.adopt omitted its managed execution envelope")
        if execution.get("project_id") != project_id:
            raise CampaignError("model.adopt response is not associated with the exact registered project")
        session_id = execution.get("session_id")
        model_ref = execution.get("model_ref")
        revision = execution.get("revision")
        if not isinstance(session_id, str) or not session_id:
            raise CampaignError("model.adopt response omitted its managed session id")
        if (not isinstance(model_ref, Mapping) or model_ref.get("model_tag") != expected_model_tag or
                model_ref.get("session_id") != session_id or
                not isinstance(model_ref.get("server_instance_id"), str) or
                not model_ref.get("server_instance_id") or
                isinstance(model_ref.get("generation"), bool) or
                not isinstance(model_ref.get("generation"), int) or model_ref.get("generation") < 1):
            raise CampaignError("model.adopt returned a ModelRef that does not identify the requested native tag")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            raise CampaignError("model.adopt response omitted a non-negative model revision")
        return cls(project_id, session_id, dict(model_ref), revision)

    @classmethod
    def from_load_response(cls, response: Any, *, project_id: str) -> "ManagedModelBinding":
        """Read a managed project-scoped model_load result; binding is checked separately."""
        if not isinstance(response, Mapping) or response.get("success") is not True:
            raise CampaignError("project-scoped model_load did not return success=true")
        execution = response.get("execution")
        if not isinstance(execution, Mapping):
            raise CampaignError("project-scoped model_load omitted its execution envelope")
        echoed_project = execution.get("project_id")
        if echoed_project is not None and echoed_project != project_id:
            raise CampaignError("model_load echoed a different project id")
        session_id = execution.get("session_id")
        model_ref = execution.get("model_ref")
        revision = execution.get("revision")
        data = response.get("data")
        model_tag = (data.get("model_tag") if isinstance(data, Mapping) else None)
        if not isinstance(model_tag, str) or not model_tag:
            model_tag = model_ref.get("model_tag") if isinstance(model_ref, Mapping) else None
        if (not isinstance(session_id, str) or not session_id or
                not isinstance(model_ref, Mapping) or model_ref.get("model_tag") != model_tag or
                model_ref.get("session_id") != session_id or
                not isinstance(model_ref.get("server_instance_id"), str) or
                not model_ref.get("server_instance_id") or
                isinstance(model_ref.get("generation"), bool) or
                not isinstance(model_ref.get("generation"), int) or model_ref.get("generation") < 1 or
                isinstance(revision, bool) or not isinstance(revision, int) or revision < 0):
            raise CampaignError("model_load did not return the exact managed native tag/ModelRef/revision")
        return cls(project_id, session_id, dict(model_ref), revision)

    def require_native_tag(self, expected_model_tag: str) -> None:
        if self.model_tag != expected_model_tag:
            raise CampaignError("managed ModelRef does not identify the requested native model tag")

    def as_record(self) -> dict[str, Any]:
        return {"project_id": self.project_id, "session_id": self.session_id,
                "model_ref": dict(self.model_ref), "revision": self.revision,
                "model_tag": self.model_tag}


class MechanicsModelBindings:
    """Bind each analytic mechanics case to a distinct adopted COMSOL model."""

    CASES = frozenset({"mechanics_free_expansion", "mechanics_fully_fixed"})

    def __init__(self, project_id: str) -> None:
        if not isinstance(project_id, str) or not project_id:
            raise CampaignError("mechanics models require the registered project id")
        self.project_id = project_id
        self._by_case: dict[str, ManagedModelBinding] = {}

    def register(self, case_id: str, response: Any, *, expected_model_tag: str) -> ManagedModelBinding:
        if case_id not in self.CASES:
            raise CampaignError("only the two frozen mechanics benchmark cases may be registered")
        if case_id in self._by_case:
            raise CampaignError(f"mechanics model was already adopted for {case_id}")
        binding = ManagedModelBinding.from_adopt_response(
            response, project_id=self.project_id, expected_model_tag=expected_model_tag)
        if any(existing.model_ref == binding.model_ref or existing.model_tag == binding.model_tag
               for existing in self._by_case.values()):
            raise CampaignError("the two mechanics benchmarks must use distinct managed ModelRefs/native tags")
        self._by_case[case_id] = binding
        return binding

    def for_case(self, case_id: str, *, expected_model_tag: str | None = None) -> ManagedModelBinding:
        try:
            binding = self._by_case[case_id]
        except KeyError as exc:
            raise CampaignError(f"mechanics ModelRef has not been adopted for {case_id}") from exc
        if expected_model_tag is not None:
            binding.require_native_tag(expected_model_tag)
        return binding

    def as_records(self) -> dict[str, dict[str, Any]]:
        if set(self._by_case) != self.CASES:
            raise CampaignError("both distinct mechanics models must be adopted before campaign preparation passes")
        return {case_id: self._by_case[case_id].as_record() for case_id in sorted(self._by_case)}


def _capture_worker_process_identity(server: Any) -> dict[str, Any]:
    """Return a fresh exact identity for the task-owned Java Worker child."""
    worker = getattr(server, "worker", None)
    process = getattr(worker, "_process", None)
    pid = getattr(process, "pid", None)
    if worker is None or process is None or type(pid) is not int or process.poll() is not None:
        raise CampaignError("the task-owned Worker process is absent or not running")
    from comsol_mcp._g2_isolation import _process_snapshot
    identity = _process_snapshot(pid)
    if (not isinstance(identity, Mapping) or identity.get("pid") != pid or
            not isinstance(identity.get("birth"), str) or not identity.get("birth") or
            not isinstance(identity.get("command"), str) or not identity.get("command")):
        raise CampaignError("the task-owned Worker PID/birth/command identity is incomplete")
    return {key: identity[key] for key in ("pid", "birth", "command")}


def _identity_same(expected: Any, current: Any) -> bool:
    return (isinstance(expected, Mapping) and isinstance(current, Mapping) and
            type(expected.get("pid")) is int and current.get("pid") == expected.get("pid") and
            isinstance(expected.get("birth"), str) and current.get("birth") == expected.get("birth") and
            isinstance(expected.get("command"), str) and current.get("command") == expected.get("command"))


def _model_ref_epoch(binding: ManagedModelBinding) -> tuple[str, str, int]:
    """Return the logical session plus exact Worker/model generation identity."""
    ref = binding.model_ref
    session_id = ref.get("session_id")
    server_instance_id = ref.get("server_instance_id")
    generation = ref.get("generation")
    if (not isinstance(session_id, str) or not session_id or session_id != binding.session_id or
            not isinstance(server_instance_id, str) or not server_instance_id or
            isinstance(generation, bool) or not isinstance(generation, int) or generation < 1):
        raise CampaignError("managed ModelRef lacks a complete Worker epoch identity")
    return session_id, server_instance_id, generation


def _model_ref_matches_worker_epoch(binding: ManagedModelBinding,
                                    worker_identity: Any) -> bool:
    """Check the Worker epoch while allowing a stable logical session id."""
    try:
        _, server_instance_id, _ = _model_ref_epoch(binding)
    except CampaignError:
        return False
    return (isinstance(worker_identity, Mapping) and
            worker_identity.get("server_instance_id") == server_instance_id)


def _observe_worker_birth(server: Any, *, attempt_index: int) -> dict[str, Any]:
    """Record a Popen birth separately from a validated connected session.

    Popen assigns a PID only after child creation. The process may already have
    exited by the time startup raises; that still counts as a birth and must
    never be erased or retried. OS identity is attached when the PID remains
    observable, but a missing snapshot does not undo the Popen evidence.
    """
    worker = getattr(server, "worker", None)
    process = getattr(worker, "_process", None)
    pid = getattr(process, "pid", None)
    if process is None or type(pid) is not int or pid <= 0:
        return {"attempt_index": attempt_index, "birth_observed": False,
                "pid": None, "process_returncode": getattr(process, "returncode", None),
                "identity_status": "NO_POPEN_PID_OBSERVED"}
    identity: dict[str, Any] | None = None
    identity_error: str | None = None
    try:
        from comsol_mcp._g2_isolation import _process_snapshot
        raw = _process_snapshot(pid)
        if isinstance(raw, Mapping) and raw.get("pid") == pid:
            identity = {key: raw.get(key) for key in ("pid", "birth", "command")}
        else:
            identity_error = "process_snapshot_did_not_match_popen_pid"
    except BaseException as exc:
        identity_error = f"{type(exc).__name__}: {exc}"
    return {"attempt_index": attempt_index, "birth_observed": True,
            "pid": pid, "process_returncode": process.poll(),
            "identity_status": "PID_BIRTH_AND_OS_IDENTITY_OBSERVED" if identity else "Popen_PID_BIRTH_OBSERVED_IDENTITY_UNAVAILABLE",
            "process_identity": identity, "identity_error": identity_error}


def _write_json_fsynced(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(dict(value), stream, ensure_ascii=False, sort_keys=True,
                      indent=2, allow_nan=False, default=str)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _read_direct_rpc_journal(path: Path, *, worker_session_index: int,
                             project_id: str) -> dict[str, Any]:
    starts: dict[str, dict[str, Any]] = {}
    finishes: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    try:
        with path.open("r", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    errors.append(f"invalid_json_line:{line_number}")
                    continue
                if not isinstance(row, dict) or row.get("worker_session_index") != worker_session_index:
                    continue
                call_id = row.get("call_id")
                if not isinstance(call_id, str) or not call_id or row.get("project_id") != project_id:
                    errors.append(f"invalid_identity_line:{line_number}")
                    continue
                target = starts if row.get("event") == "direct_rpc_started" else (
                    finishes if row.get("event") == "direct_rpc_finished" else None)
                if target is None:
                    continue
                if call_id in target:
                    errors.append(f"duplicate_{row.get('event')}:{call_id}")
                target[call_id] = row
    except OSError as exc:
        return {"complete": False, "status": "UNREADABLE",
                "errors": [f"{type(exc).__name__}: {exc}"], "started_count": 0,
                "finished_count": 0, "pending_call_ids": []}
    pending = sorted(set(starts) - set(finishes))
    orphan = sorted(set(finishes) - set(starts))
    nonterminal = sorted(call_id for call_id, row in finishes.items()
                         if row.get("rpc_returned") is not True or row.get("terminal") is not True)
    complete = bool(starts) and not (errors or pending or orphan or nonterminal) and set(starts) == set(finishes)
    return {"complete": complete,
            "status": "ALL_DIRECT_RPC_CALLS_TERMINAL" if complete else "DIRECT_RPC_JOURNAL_INCOMPLETE_OR_UNKNOWN",
            "started_count": len(starts), "finished_count": len(finishes),
            "pending_call_ids": pending, "orphan_finished_call_ids": orphan,
            "nonterminal_call_ids": nonterminal, "errors": errors,
            "operations": [starts[key].get("operation") for key in sorted(starts)]}


def _worker_quiescence_readback_is_safe(readback: Any, *, project_id: str,
                                        session_id: str, worker: Any,
                                        worker_identity: Mapping[str, Any] | None = None) -> bool:
    """Validate the published admission-fenced ControlDaemon snapshot contract."""
    if not isinstance(readback, Mapping) or readback.get("success") is not True:
        return False
    data = readback.get("data")
    if not isinstance(data, Mapping) or data.get("schema_version") != 1:
        return False
    binding = data.get("binding")
    checks = data.get("checks")
    if not isinstance(binding, Mapping) or not isinstance(checks, Mapping):
        return False
    if (data.get("scope") != "exact-daemon-worker+all-projects+durable-direct-rpc" or
            data.get("worker_status") != "QUIESCENT" or
            data.get("safe_to_retire_worker") is not True or
            binding.get("project_id") != project_id or
            binding.get("session_id") != session_id or
            binding.get("worker_object_identity") != f"python-object:{id(worker)}"):
        return False
    if not all(checks.get(key) is True for key in (
            "exact_worker_binding", "all_projects_in_daemon_ledger_scanned",
            "snapshot_stable", "all_accepted_jobs_terminal",
            "no_durable_unknown_or_unclassified_jobs",
            "all_direct_rpc_requests_observed_once_and_terminal")):
        return False
    if worker_identity is not None:
        # The public ControlDaemon contract binds the Python Worker instance
        # and connection epoch. It deliberately does not duplicate the
        # COMSOL server_instance_id; ModelRef epoch checks below bind that
        # identity separately.
        for key in ("worker_instance_id", "connection_epoch"):
            if binding.get(key) != worker_identity.get(key):
                return False
        if (not isinstance(worker_identity.get("server_instance_id"), str) or
                not worker_identity.get("server_instance_id")):
            return False
    return True


def _worker_transition_reconciliation(*, daemon: Any, project_id: str,
                                      immutable_project_id: str,
                                      project_workspace: Path,
                                      server: Any,
                                      expected_worker_object: Any,
                                      expected_worker_identity: Mapping[str, Any],
                                      direct_rpc_journal: Path,
                                      worker_session_index: int,
                                      worker_session_id: str,
                                      worker_quiescence: Mapping[str, Any]) -> dict[str, Any]:
    """Prove the first Worker is quiescent before close/replacement.

    This gate is intentionally stricter than cleanup: UNKNOWN project jobs or
    execution-unknown requests block a scientific session transition.
    """
    from tools.run_native_w24_cure_preflight import (
        _cleanup_reconciliation, _project_job_inventory,
        _worker_request_activity_inventory,
    )
    checks: dict[str, bool] = {}
    details: dict[str, Any] = {}
    jobs: dict[str, Any] | None = None
    worker_requests: dict[str, Any] | None = None
    try:
        authority = getattr(daemon, "project_authority", None)
        get_project = getattr(authority, "get_project", None)
        if not callable(get_project):
            raise CampaignError("ControlDaemon lacks persisted project authority readback")
        project_record = get_project(project_id)
        record_workspace = Path(str(project_record.get("workspace", ""))).resolve(strict=True)
        expected_workspace = project_workspace.resolve(strict=True)
        checks["immutable_project_identity_and_workspace"] = (
            project_id == immutable_project_id == project_record.get("project_id") and
            record_workspace == expected_workspace)
        details["project_record"] = dict(project_record)
        details["project_workspace"] = str(record_workspace)
    except Exception as exc:
        checks["immutable_project_identity_and_workspace"] = False
        details["project_record_error"] = f"{type(exc).__name__}: {exc}"

    try:
        jobs = _project_job_inventory(daemon, project_id)
        worker_requests = _worker_request_activity_inventory(daemon.store, project_id)
        checks["all_paged_project_jobs_terminal"] = not jobs.get("active") and not jobs.get("unknown")
        checks["all_worker_requests_observed_terminal"] = (
            worker_requests.get("safe_to_cleanup") is True and
            worker_requests.get("pending_request_ids") == [] and
            worker_requests.get("orphan_observed_request_ids") == [] and
            worker_requests.get("nonterminal_observed_request_ids") == [])
        checks["no_execution_unknown_worker_effect"] = (
            worker_requests.get("execution_state_unknown_request_ids") == [])
        details["job_inventory"] = jobs
        details["worker_request_inventory"] = worker_requests
    except Exception as exc:
        checks["all_paged_project_jobs_terminal"] = False
        checks["all_worker_requests_observed_terminal"] = False
        checks["no_execution_unknown_worker_effect"] = False
        details["ledger_error"] = f"{type(exc).__name__}: {exc}"

    rpc = _read_direct_rpc_journal(direct_rpc_journal,
                                   worker_session_index=worker_session_index,
                                   project_id=project_id)
    checks["complete_direct_rpc_journal_terminal"] = rpc.get("complete") is True
    details["direct_rpc_journal"] = rpc
    if jobs is not None and worker_requests is not None:
        details["cleanup_reconciliation"] = _cleanup_reconciliation(
            jobs, worker_requests, direct_native_calls_safe=rpc.get("complete") is True)

    backend_identity = getattr(getattr(daemon, "backend", None), "worker_identity", None)
    checks["daemon_idle"] = _worker_quiescence_readback_is_safe(
        worker_quiescence, project_id=project_id, session_id=worker_session_id,
        worker=expected_worker_object,
        worker_identity=backend_identity if isinstance(backend_identity, Mapping) else None)
    details["control_daemon_quiescence_readback"] = dict(worker_quiescence)

    try:
        current_worker = _capture_worker_process_identity(server)
        backend = getattr(daemon, "backend", None)
        worker_object_matches = (getattr(server, "worker", None) is expected_worker_object and
                                 getattr(daemon, "worker", None) is expected_worker_object and
                                 getattr(backend, "worker", None) is expected_worker_object)
        checks["exact_current_worker_identity"] = worker_object_matches and _identity_same(
            expected_worker_identity, current_worker)
        details["expected_worker_identity"] = dict(expected_worker_identity)
        details["current_worker_identity"] = current_worker
        details["worker_object_matches_server_and_daemon"] = worker_object_matches
    except Exception as exc:
        checks["exact_current_worker_identity"] = False
        details["worker_identity_error"] = f"{type(exc).__name__}: {exc}"

    try:
        server_identity = getattr(server, "process_identity", None)
        server_process = getattr(server, "proc", None)
        if server_process is None or server_process.poll() is not None:
            raise CampaignError("owned COMSOL server process is absent or exited before Worker transition")
        from comsol_mcp._g2_isolation import _process_snapshot
        current_server_identity = _process_snapshot(server_process.pid)
        checks["owned_server_identity_stable"] = _identity_same(server_identity, current_server_identity)
        details["server_identity"] = {"expected": server_identity, "current": current_server_identity}
    except Exception as exc:
        checks["owned_server_identity_stable"] = False
        details["server_identity_error"] = f"{type(exc).__name__}: {exc}"

    safe = bool(checks) and all(checks.values())
    return {"schema": "W24_WORKER_TRANSITION_RECONCILIATION_V1",
            "status": "SAFE_TO_CLOSE_FIRST_WORKER" if safe else "TRANSITION_BLOCKED_ACTIVE_OR_UNKNOWN",
            "safe_to_close_first_worker": safe,
            "project_id": project_id, "immutable_project_id": immutable_project_id,
            "worker_session_index": worker_session_index,
            "worker_session_id": worker_session_id,
            "worker_quiescence_readback": dict(worker_quiescence),
            "checks": checks, **details}


def _validate_mechanics_binding_records(value: Any, *, project_id: str | None = None) -> dict[str, dict[str, Any]]:
    if not isinstance(value, Mapping) or set(value) != MechanicsModelBindings.CASES:
        raise CampaignError("campaign preparation must expose both adopted mechanics ModelRefs")
    records: dict[str, dict[str, Any]] = {}
    tags: set[str] = set()
    refs: list[Mapping[str, Any]] = []
    for case_id in sorted(MechanicsModelBindings.CASES):
        record = value[case_id]
        if not isinstance(record, Mapping):
            raise CampaignError(f"prepared mechanics binding is missing for {case_id}")
        pid = record.get("project_id")
        if not isinstance(pid, str) or not pid or (project_id is not None and pid != project_id):
            raise CampaignError("prepared mechanics ModelRefs do not share the registered project")
        binding = ManagedModelBinding.from_adopt_response(
            {"success": True, "execution": {
                "project_id": pid,
                "session_id": record.get("session_id"),
                "model_ref": record.get("model_ref"),
                "revision": record.get("revision"),
            }},
            project_id=pid,
            expected_model_tag=str(record.get("model_tag", "")),
        )
        if binding.model_tag in tags or any(binding.model_ref == existing for existing in refs):
            raise CampaignError("the two mechanics benchmarks must use distinct managed ModelRefs/native tags")
        tags.add(binding.model_tag)
        refs.append(binding.model_ref)
        records[case_id] = binding.as_record()
    return records


def _require_mechanics_slot_binding(slot: SolveSlot, prepared: Mapping[str, Any],
                                    configured: Mapping[str, Any]) -> dict[str, Any] | None:
    if slot.case_id not in MechanicsModelBindings.CASES:
        return None
    expected_all = _validate_mechanics_binding_records(prepared.get("mechanics_model_bindings"))
    expected = expected_all[slot.case_id]
    observed = configured.get("model_binding")
    if not isinstance(observed, Mapping):
        raise CampaignError("mechanics action lacks a fresh project-scoped model identity readback")
    if (observed.get("project_id") != expected["project_id"] or
            observed.get("session_id") != expected["session_id"] or
            observed.get("model_ref") != expected["model_ref"] or
            observed.get("model_tag") != expected["model_tag"] or
            observed.get("revision") != expected["revision"]):
        raise CampaignError("mechanics action model identity differs from its individually adopted model")
    return dict(observed)


class ScienceCampaignAdapter(Protocol):
    """Native I/O boundary used by the deterministic ten-slot controller."""

    def prepare_campaign(self, stress_components: Mapping[str, Mapping[str, str]],
                         *, timeout_s: float) -> Mapping[str, Any]: ...

    def configure_slot(self, slot: SolveSlot, *, timeout_s: float) -> Mapping[str, Any]: ...

    def run_study(self, slot: SolveSlot, *, ledger_path: Path,
                  save_after_success_path: Path | None,
                  timeout_s: float) -> Mapping[str, Any]: ...

    def capture_solution(self, slot: SolveSlot, *, output_dir: Path,
                         timeout_s: float) -> Mapping[str, Any]: ...

    def validate_slot(self, slot: SolveSlot, capture: Mapping[str, Any],
                      prior_captures: Mapping[str, Mapping[str, Any]],
                      *, timeout_s: float) -> Mapping[str, Any]: ...

    def reopen_staged_baseline(self, saved_path: Path, *, timeout_s: float) -> Mapping[str, Any]: ...


class CampaignLifecycleRuntime(Protocol):
    """Lifecycle boundary shared by the production runner and synthetic tests."""

    def prepare_prebirth(self, *, setup: Mapping[str, Any], candidate: Mapping[str, Any],
                         work: Path, evidence: Path) -> Mapping[str, Any]: ...

    def start_server(self) -> Mapping[str, Any]: ...

    def observe_server_birth(self) -> Mapping[str, Any]: ...

    def start_worker(self, *, birth_budget: BirthBudget) -> Mapping[str, Any]: ...

    def create_adapter(self, *, stress_components: Mapping[str, Mapping[str, str]],
                       work: Path, evidence: Path) -> ScienceCampaignAdapter: ...

    def run_controller(self, adapter: ScienceCampaignAdapter, *, birth_budget: BirthBudget,
                       stress_components: Mapping[str, Mapping[str, str]],
                       work: Path, evidence: Path) -> Mapping[str, Any]: ...

    def reconcile_for_cleanup(self) -> Mapping[str, Any]: ...

    def cleanup_exact_owned(self, reconciliation: Mapping[str, Any]) -> Mapping[str, Any]: ...


def _validate_candidate_path(path: Path, expected_sha256: str) -> Path:
    resolved = path.expanduser().resolve(strict=True)
    if resolved.is_symlink() or not resolved.is_file():
        raise CampaignError("campaign freeze must be a regular non-symlink file")
    if not isinstance(expected_sha256, str) or not SHA256_RE.fullmatch(expected_sha256):
        raise CampaignError("expected campaign freeze SHA-256 is invalid")
    return resolved


def execute_campaign_lifecycle(*, template_receipt: Path, candidate_path: Path,
                               expected_candidate_sha256: str, approval_path: Path,
                               work: Path, evidence: Path,
                               runtime_factory: Any,
                               platform_name: str | None = None,
                               clock: Any | None = None) -> dict[str, Any]:
    """Execute one exact approved campaign through injectable owned lifecycle.

    Candidate/approval/template validation precedes all path creation and
    process inventory. A server Popen PID is counted as a birth even if later
    listener readback raises. Cleanup is possible only through a positive
    runtime reconciliation; exceptions after that point never become a
    PREBIRTH result.
    """
    if clock is None:
        clock = time.time
    try:
        setup = validate_template_receipt(template_receipt)
        frozen_path = _validate_candidate_path(candidate_path, expected_candidate_sha256)
        candidate, approval = validate_campaign_candidate(
            frozen_path, expected_candidate_sha256, approval_path, setup)
        work = work.expanduser().absolute()
        evidence = evidence.expanduser().absolute()
        if (not work.as_posix().startswith("/private/tmp/comsol-mcp-w24-cure-science-") or
                work == Path("/private/tmp") or work.exists()):
            raise CampaignError("--work must be a new task-owned /private/tmp/comsol-mcp-w24-cure-science-* path")
        if evidence.exists():
            raise FileExistsError("W24 science evidence path already exists; existing evidence is never overwritten")
        observed_platform = sys.platform if platform_name is None else platform_name
        if observed_platform != "darwin" and runtime_factory is _production_runtime_factory:
            raise CampaignError(f"W24 native campaign requires macOS; observed {observed_platform}")
    except Exception as exc:
        raise PrebirthRefusal(f"prebirth validation refused the campaign: {type(exc).__name__}: {exc}") from exc

    # Only after every static gate above passes do we create receipts/directories.
    try:
        runtime = runtime_factory(work=work, evidence=evidence)
        work.mkdir(parents=True, exist_ok=False)
        evidence.mkdir(parents=True, exist_ok=False)
        approval_resolved = approval_path.expanduser().resolve(strict=True)
        result: dict[str, Any] = {
            "schema": "W24_NATIVE_CURE_SCIENCE_LIFECYCLE_V1",
            "status": "PREBIRTH_SETUP_RUNNING",
            "candidate_id": candidate["candidate_id"],
            "candidate_freeze_sha256": candidate["freeze_sha256"],
            "candidate_freeze_file_sha256": sha256(frozen_path),
            "approval_path": str(approval_resolved),
            "approval_sha256": sha256(approval_resolved),
            "template_receipt_sha256": setup["receipt_sha256"],
            "work_dir": str(work), "evidence_dir": str(evidence),
            "engine_births": 0, "study_run_submissions": 0,
            "native_acceptance": "NOT_RUN",
        }
        _write_json_fsynced(evidence / "campaign_lifecycle_started.json", result)
    except Exception as exc:
        raise PrebirthRefusal(f"prebirth lifecycle initialization failed before server launch: {type(exc).__name__}: {exc}") from exc
    birth_record: dict[str, Any] | None = None
    birth_budget: BirthBudget | None = None
    adapter: ScienceCampaignAdapter | None = None
    controller_result: dict[str, Any] | None = None
    lifecycle_error: str | None = None
    setup_receipt: Mapping[str, Any] | None = None
    cleanup_receipt: Mapping[str, Any] | None = None
    server_start_attempted = False
    stress_components = candidate["stress_components_from_complete_native_equation_view"]
    try:
        setup_receipt = runtime.prepare_prebirth(
            setup=setup, candidate=candidate, work=work, evidence=evidence)
        if not isinstance(setup_receipt, Mapping) or setup_receipt.get("status") != "PREBIRTH_GATES_PASSED":
            raise CampaignError("production prebirth runtime/project/workspace gates did not pass")
        verify_source_manifest(candidate["source_manifest"])
        _write_json_fsynced(evidence / "prebirth_runtime_receipt.json", dict(setup_receipt))

        server_start_attempted = True
        launch = runtime.start_server()
        if not isinstance(launch, Mapping):
            launch = {}
        birth_record = dict(launch)
        if birth_record.get("birth_observed") is not True:
            observed = runtime.observe_server_birth()
            if isinstance(observed, Mapping) and observed.get("birth_observed") is True:
                birth_record = {**dict(launch), **dict(observed)}
        if birth_record.get("birth_observed") is not True:
            raise CampaignError("owned server launch returned without an exact process birth identity")
        birth_epoch = birth_record.get("birth_epoch_s")
        if isinstance(birth_epoch, bool) or not isinstance(birth_epoch, (int, float)) or not math.isfinite(float(birth_epoch)):
            raise CampaignError("owned server birth identity lacks a finite epoch for the campaign budget")
        birth_budget = BirthBudget(float(birth_epoch))
        result.update({"status": "OWNED_SERVER_BIRTH_VERIFIED", "engine_births": 1,
                       "server_birth": dict(birth_record),
                       "birth_budget": birth_budget.receipt(now_epoch_s=clock())})
        _write_json_fsynced(evidence / "server_birth.json", {
            "status": "OWNED_SERVER_BIRTH_VERIFIED", "candidate_freeze_sha256": candidate["freeze_sha256"],
            "server_birth": dict(birth_record), "birth_budget": birth_budget.receipt(now_epoch_s=clock())})

        if clock() >= birth_budget.deadline_epoch_s - CLEANUP_RESERVE_S:
            raise CampaignError("campaign birth budget is inside the reserved cleanup window before Worker start")
        worker_receipt = runtime.start_worker(birth_budget=birth_budget)
        if not isinstance(worker_receipt, Mapping) or worker_receipt.get("status") != "WORKER_CONNECTED_LOOPBACK_PROOF_PASSED":
            raise CampaignError("owned Worker did not pass exact process/endpoint/isolation readback")
        _write_json_fsynced(evidence / "worker_connection_receipt.json", dict(worker_receipt))

        adapter = runtime.create_adapter(stress_components=stress_components, work=work, evidence=evidence)
        controller = runtime.run_controller(
            adapter, birth_budget=birth_budget, stress_components=stress_components,
            work=work, evidence=evidence)
        controller_result = dict(controller) if isinstance(controller, Mapping) else {
            "status": "FAIL_OR_INCOMPLETE", "error": "science controller returned a non-object"}
        ledger_path_text = controller_result.get("study_run_ledger")
        ledger_path = Path(ledger_path_text) if isinstance(ledger_path_text, str) else None
        if ledger_path is None:
            ledger_path = work / "project" / "science" / "study_run_events.jsonl"
        if ledger_path.exists():
            result["study_run_submissions"] = len(read_solve_ledger(ledger_path))
        else:
            result["study_run_submissions"] = 0
        result["science_controller"] = controller_result
        _write_json_fsynced(evidence / "science_controller_receipt.json", controller_result)
    except BaseException as exc:
        lifecycle_error = f"{type(exc).__name__}: {exc}"
        result["error"] = lifecycle_error
        result["traceback"] = traceback.format_exc()
        observed: Mapping[str, Any] | None = None
        try:
            observed = runtime.observe_server_birth()
            if isinstance(observed, Mapping):
                result["birth_observation"] = dict(observed)
        except BaseException as observe_exc:
            result["birth_observation_error"] = f"{type(observe_exc).__name__}: {observe_exc}"
        if (birth_record is None and isinstance(observed, Mapping) and
                observed.get("birth_observed") is True):
            birth_record = dict(observed)
        if birth_record and birth_record.get("birth_observed") is True:
            result["engine_births"] = 1
            result["server_birth"] = dict(birth_record)
            if birth_budget is None:
                epoch = birth_record.get("birth_epoch_s")
                if isinstance(epoch, (int, float)) and not isinstance(epoch, bool) and math.isfinite(float(epoch)):
                    birth_budget = BirthBudget(float(epoch))

    if birth_record and birth_record.get("birth_observed") is True:
        reconciliation: Mapping[str, Any] | None = None
        try:
            reconciliation = runtime.reconcile_for_cleanup()
        except BaseException as exc:
            reconciliation = {"status": "RECONCILIATION_FAILED", "safe_for_owned_cleanup": False,
                              "error": f"{type(exc).__name__}: {exc}"}
        result["cleanup_reconciliation"] = dict(reconciliation)
        if reconciliation.get("safe_for_owned_cleanup") is True:
            try:
                cleanup_receipt = runtime.cleanup_exact_owned(reconciliation)
            except BaseException as exc:
                cleanup_receipt = {"status": "CLEANUP_UNVERIFIED",
                                   "error": f"{type(exc).__name__}: {exc}"}
        else:
            cleanup_receipt = {"status": "LEFT_RUNNING_ACTIVE_OR_UNKNOWN",
                               "action": "no signal sent because complete terminal/quiescence/identity proof is missing",
                               "server_birth": dict(birth_record),
                               "reconciliation": dict(reconciliation)}
        result["cleanup"] = dict(cleanup_receipt)
        if cleanup_receipt.get("status") == "CLEANUP_VERIFIED_EXACT_OWNED_PIDS_AND_PORTS_ABSENT":
            if controller_result and controller_result.get("status") == "NATIVE_SCIENCE_EXECUTION_COMPLETE_ACCEPTANCE_RECEIPTS_RECORDED":
                result["status"] = "NATIVE_SCIENCE_EXECUTION_COMPLETE_CLEANUP_VERIFIED"
            elif controller_result and controller_result.get("status") == "SYNTHETIC_TEST_ONLY":
                result["status"] = "SYNTHETIC_TEST_ONLY_CLEANUP_VERIFIED"
            else:
                result["status"] = "FAIL_OR_INCOMPLETE_CLEANUP_VERIFIED"
        else:
            result["status"] = "LEFT_RUNNING_ACTIVE_OR_UNKNOWN"
    elif lifecycle_error and not server_start_attempted:
        result["status"] = "PREBIRTH_REFUSED_NO_ENGINE"
        result["engine_births"] = 0
        result["study_run_submissions"] = 0
    elif lifecycle_error:
        observation = result.get("birth_observation")
        if isinstance(observation, Mapping) and observation.get("birth_observed") is False:
            result["status"] = "SERVER_START_FAILED_NO_ENGINE"
            result["engine_births"] = 0
            result["study_run_submissions"] = 0
        else:
            result["status"] = "OUTCOME_UNKNOWN"
            result["engine_births"] = None
            result["study_run_submissions"] = None
    elif controller_result:
        result["status"] = "FAIL_OR_INCOMPLETE_NO_SERVER_BIRTH"

    result["finished_at_epoch_s"] = clock()
    if birth_budget is not None:
        result["final_birth_budget"] = birth_budget.receipt(now_epoch_s=clock())
    if result.get("status") == "NATIVE_SCIENCE_EXECUTION_COMPLETE_CLEANUP_VERIFIED":
        result["native_acceptance"] = "PER_SLOT_RECEIPTS_RECORDED_REQUIRES_INDEPENDENT_REVIEW"
    elif result.get("status") == "SYNTHETIC_TEST_ONLY_CLEANUP_VERIFIED":
        result["native_acceptance"] = "NOT_NATIVE_SYNTHETIC_TEST_ONLY"
    else:
        result["native_acceptance"] = "NOT_ACCEPTED"
    try:
        _write_json_fsynced(evidence / "summary.json", result)
    except Exception as exc:
        # The native lifecycle may already have run. Never let the CLI's
        # generic exception handler turn a final-receipt failure into a false
        # PREBIRTH/zero-birth result.
        result["status"] = "OUTCOME_UNKNOWN"
        result["native_acceptance"] = "NOT_ACCEPTED"
        result["summary_write_error"] = f"{type(exc).__name__}: {exc}"
        result["outcome_note"] = "lifecycle reached terminal handling but summary.json was not durably written"
        try:
            _write_json_fsynced(evidence / "summary_write_failure.json", result)
        except Exception as receipt_exc:
            result["summary_write_failure_receipt_error"] = f"{type(receipt_exc).__name__}: {receipt_exc}"
    return result


def _production_runtime_factory(*, work: Path, evidence: Path) -> "NativeCampaignRuntime":
    return NativeCampaignRuntime(work=work, evidence=evidence)


def _copy_file_exclusive(source: Path, destination: Path) -> dict[str, Any]:
    source = source.resolve(strict=True)
    source_meta = source.lstat()
    if not stat.S_ISREG(source_meta.st_mode) or source.is_symlink():
        raise CampaignError(f"campaign source must be a regular non-symlink file: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    size = 0
    with source.open("rb") as source_stream, destination.open("xb") as target:
        while True:
            chunk = source_stream.read(1024 * 1024)
            if not chunk:
                break
            target.write(chunk)
            digest.update(chunk)
            size += len(chunk)
        target.flush()
        os.fsync(target.fileno())
    if (size != source.stat().st_size or digest.hexdigest() != sha256(source) or
            destination.stat().st_size != size or sha256(destination) != digest.hexdigest()):
        raise CampaignError("staged project input differs from its protected source bytes")
    return {"source": str(source), "source_size_bytes": size,
            "source_sha256": digest.hexdigest(), "project_path": str(destination.resolve(strict=True)),
            "project_size_bytes": destination.stat().st_size,
            "project_sha256": sha256(destination)}


class NativeCampaignRuntime:
    """Production project-scoped COMSOL setup and exact owned cleanup runtime."""

    def __init__(self, *, work: Path, evidence: Path):
        self.work = work
        self.evidence = evidence
        self.server: Any = None
        self.daemon: Any = None
        self.prebirth_daemon: Any = None
        self.preflight: Any = None
        self.project_id: str | None = None
        self.project_workspace: Path | None = None
        self.project_creation: dict[str, Any] | None = None
        self.prebirth_host_grants: dict[str, Any] | None = None
        self.input_paths: dict[str, Path] = {}
        self.science_fixture_path: Path | None = None
        self.v2_control_fixture_path: Path | None = None
        self.coupon_fixture_path: Path | None = None
        self.cure_law_v2_enabled = False
        self.candidate_source_manifest: Mapping[str, Mapping[str, Any]] = {}
        self.solve_ledger_path: Path | None = None
        self.worker_identity: dict[str, Any] | None = None
        self.worker_port: int | None = None
        self.server_birth: dict[str, Any] | None = None
        self.adapter: NativeScienceCampaignAdapter | None = None
        self.direct_native_call_guard: dict[str, Any] = {
            "status": "NO_DIRECT_NATIVE_CALLS_YET", "all_returned": True, "calls": [],
        }
        self.birth_inventory: dict[str, Any] | None = None
        self.runtime_preflight_receipt: dict[str, Any] | None = None

    def prepare_prebirth(self, *, setup: Mapping[str, Any], candidate: Mapping[str, Any],
                         work: Path, evidence: Path) -> Mapping[str, Any]:
        from tools import run_native_w24_cure_preflight as preflight
        from tools.run_native_resume_smoke import NativeLoopbackServer
        from comsol_mcp._control_daemon import ControlDaemon

        self.preflight = preflight
        source_manifest = candidate.get("source_manifest")
        if not isinstance(source_manifest, Mapping):
            raise CampaignError("frozen native candidate omitted its source manifest")
        self.candidate_source_manifest = source_manifest
        v2_setup = setup.get("cure_law_v2_capture")
        if not isinstance(v2_setup, Mapping) or not isinstance(v2_setup.get("enabled"), bool):
            raise CampaignError("validated template omitted its explicit v1/v2 capture-mode readback")
        self.cure_law_v2_enabled = v2_setup["enabled"]
        if sys.platform != "darwin":
            raise CampaignError(f"native W24 execution requires macOS 6.4, observed {sys.platform}")
        startup_opt_in = preflight._configure_trusted_code_startup_opt_in()
        self.runtime_preflight_receipt = preflight._runtime_environment_preflight(
            dict(setup["runtime_environment"]), work, evidence)
        if self.runtime_preflight_receipt.get("status") != "PASS":
            raise CampaignError("exact-Python production imports/registry/ControlDaemon prebirth gate did not pass")
        first_inventory = preflight._process_inventory()
        _write_json_fsynced(evidence / "prelaunch_inventory.json", first_inventory)
        if first_inventory.get("quiescent") is not True:
            raise CampaignError("fresh COMSOL/Worker/listener inventory is not quiescent before setup")
        if (not preflight.INSTALL_ROOT.is_dir() or not preflight.JAVA_HOME.is_dir() or
                not preflight.JAVAC.is_file()):
            raise CampaignError("the inventoried COMSOL 6.4 or Corretto 11 installation is unavailable")

        self.server = NativeLoopbackServer(work, evidence,
                                           event_log=evidence / "campaign_events.jsonl")
        shadow = self.server.prepare_shadow()
        _write_json_fsynced(evidence / "private_shadow_receipt.json", shadow)
        self.prebirth_daemon = ControlDaemon(work / "control", project_root=self.server.project)
        try:
            self.prebirth_host_grants = preflight._require_trusted_code_host_grant(self.prebirth_daemon)
            self.project_creation = preflight._create_registered_project_workspace(
                self.prebirth_daemon, self.server.project)
        finally:
            self.prebirth_daemon.close()
            self.prebirth_daemon = None
        self.project_id = self.project_creation["project_id"]
        self.project_workspace = Path(self.project_creation["workspace"]).resolve(strict=True)
        _write_json_fsynced(evidence / "project_workspace_created_before_birth.json", self.project_creation)

        source_template = Path(setup["template_path"]).resolve(strict=True)
        if (source_template.stat().st_size != setup["template_size_bytes"] or
                sha256(source_template) != setup["template_sha256"]):
            raise CampaignError("protected native setup template changed while preparing the science workspace")
        template_copy = self.project_workspace / "inputs" / "configured_template_source.mph"
        copies: dict[str, Any] = {"configured_template": _copy_file_exclusive(source_template, template_copy)}
        inputs_dir = self.project_workspace / "inputs"
        self.input_paths = {}
        for name in INPUT_MODEL_NAMES:
            destination = inputs_dir / f"{name}.mph"
            copies[name] = _copy_file_exclusive(source_template, destination)
            self.input_paths[name] = destination

        expected_fixture = candidate["source_manifest"].get("tools/java/W24CureScienceFixture.java")
        if (not isinstance(expected_fixture, Mapping) or
                sha256(SCIENCE_FIXTURE) != expected_fixture.get("sha256")):
            raise CampaignError("native science Java fixture differs from the reviewed candidate source closure")
        expected_v2_control = candidate["source_manifest"].get("tools/java/W24CureLawV2ControlFixture.java")
        if (not isinstance(expected_v2_control, Mapping) or
                sha256(V2_CONTROL_FIXTURE) != expected_v2_control.get("sha256")):
            raise CampaignError("native V2 capture Java fixture differs from the reviewed candidate source closure")
        expected_coupon = candidate["source_manifest"].get("tools/java/W24CureCouponFixture.java")
        if (not isinstance(expected_coupon, Mapping) or
                sha256(COUPON_FIXTURE) != expected_coupon.get("sha256")):
            raise CampaignError("native coupon readback Java fixture differs from the reviewed candidate source closure")
        self.science_fixture_path = self.project_workspace / "W24CureScienceFixture.java"
        copies["science_java_fixture"] = _copy_file_exclusive(SCIENCE_FIXTURE, self.science_fixture_path)
        self.v2_control_fixture_path = self.project_workspace / V2_CONTROL_FIXTURE.name
        copies["v2_control_java_fixture"] = _copy_file_exclusive(
            V2_CONTROL_FIXTURE, self.v2_control_fixture_path)
        self.coupon_fixture_path = self.project_workspace / COUPON_FIXTURE.name
        copies["coupon_java_fixture"] = _copy_file_exclusive(COUPON_FIXTURE, self.coupon_fixture_path)
        compile_receipts: dict[str, Any] = {}
        for label, source in (("science", self.science_fixture_path),
                              ("v2_control", self.v2_control_fixture_path),
                              ("coupon_readback", self.coupon_fixture_path)):
            compile_output = self.project_workspace / f"offline-{label}-classes"
            receipt = preflight._compile_offline(compile_output, fixture_source=source)
            _write_json_fsynced(evidence / f"offline_{label}_javac.json", receipt)
            if receipt.get("exit_code") != 0 or not receipt.get("output_classes"):
                raise CampaignError(f"COMSOL 6.4 W24 {label} Java fixture did not compile in the registered workspace")
            compile_receipts[label] = receipt
        compile_receipt = {"status": "ALL_CAPTURE_AND_SCIENCE_JAVA_FIXTURES_COMPILED",
                           "sources": compile_receipts,
                           "cure_law_v2_capture_enabled": self.cure_law_v2_enabled}
        _write_json_fsynced(evidence / "offline_science_javac.json", compile_receipt)

        outputs = self.project_workspace / "outputs"
        outputs.mkdir(exist_ok=False)
        ledger = outputs / "study_run_events.jsonl"
        with ledger.open("xb") as stream:
            stream.flush()
            os.fsync(stream.fileno())
        self.solve_ledger_path = ledger
        copy_manifest = {
            "schema": "W24_REGISTERED_PROJECT_INPUTS_V1",
            "status": "STAGED_BEFORE_ENGINE_BIRTH",
            "project_id": self.project_id,
            "project_workspace": str(self.project_workspace),
            "authorized_root": str(self.server.project.resolve(strict=True)),
            "inputs": copies,
            "input_model_names": list(INPUT_MODEL_NAMES),
            "output_ledger": str(ledger), "output_ledger_sha256": sha256(ledger),
            "fixture_compile": compile_receipt,
            "source_template_sha256": setup["template_sha256"],
        }
        _write_json_fsynced(evidence / "registered_project_inputs.json", copy_manifest)
        verify_source_manifest(candidate["source_manifest"])
        return {
            "status": "PREBIRTH_GATES_PASSED",
            "python_runtime_preflight": self.runtime_preflight_receipt,
            "trusted_code_startup_opt_in": startup_opt_in,
            "initial_process_inventory": first_inventory,
            "private_shadow": shadow,
            "project_creation": self.project_creation,
            "project_id": self.project_id,
            "project_workspace": str(self.project_workspace),
            "registered_project_inputs": copy_manifest,
            "science_fixture_compile": compile_receipt,
            "engine_births": 0, "study_run_submissions": 0,
        }

    def start_server(self) -> Mapping[str, Any]:
        if self.server is None:
            raise CampaignError("task-owned server shadow was not prepared before start")
        inventory = self.preflight._process_inventory()
        _write_json_fsynced(self.evidence / "prebirth_inventory.json", inventory)
        self.birth_inventory = inventory
        if inventory.get("quiescent") is not True:
            raise CampaignError("fresh process/listener inventory immediately before birth is not quiescent")
        listener = self.server.start_and_verify_listener()
        identity = self.server.process_identity
        if not isinstance(identity, Mapping) or type(identity.get("pid")) is not int:
            raise CampaignError("owned COMSOL process birth has no exact PID identity")
        birth = identity.get("birth")
        if not isinstance(birth, str) or not birth:
            raise CampaignError("owned COMSOL process birth has no OS lstart identity")
        record = {"birth_observed": True, "process_identity": dict(identity),
                  "pid": identity["pid"], "birth": birth,
                  "command": identity.get("command"), "port": self.server.port,
                  "listener": listener, "birth_epoch_s": self.preflight._mac_birth_epoch(birth)}
        self.server_birth = record
        _write_json_fsynced(self.evidence / "owned_server_process_identity.json", record)
        return record

    def observe_server_birth(self) -> Mapping[str, Any]:
        if self.server is None or self.server.proc is None:
            return {"birth_observed": False, "reason": "no server Popen object or PID exists"}
        pid = self.server.proc.pid
        if type(pid) is not int or pid <= 0:
            return {"birth_observed": False, "reason": "server Popen has no positive PID"}
        from comsol_mcp._g2_isolation import _process_snapshot
        try:
            identity = _process_snapshot(pid)
        except BaseException as exc:
            identity = None
            identity_error = f"{type(exc).__name__}: {exc}"
        else:
            identity_error = None
        if isinstance(identity, Mapping) and identity.get("pid") == pid:
            record = {"pid": pid, "birth": identity.get("birth"),
                      "command": identity.get("command"), "port": self.server.port,
                      "process_identity": dict(identity), "identity_error": identity_error}
            if isinstance(identity.get("birth"), str) and identity.get("birth"):
                try:
                    record["birth_epoch_s"] = self.preflight._mac_birth_epoch(identity["birth"])
                except BaseException as exc:
                    record["birth_epoch_error"] = f"{type(exc).__name__}: {exc}"
            record["birth_observed"] = True
        else:
            record = {"birth_observed": True, "pid": pid, "birth": None,
                      "command": None, "port": self.server.port,
                      "process_identity": None,
                      "identity_error": identity_error or "process_snapshot_did_not_match_popen_pid"}
        self.server_birth = record
        _write_json_fsynced(self.evidence / "owned_server_process_identity.json", record)
        return record

    def start_worker(self, *, birth_budget: BirthBudget) -> Mapping[str, Any]:
        if self.server is None or self.preflight is None:
            raise CampaignError("owned server runtime is not initialized")
        if _current_epoch_s() >= birth_budget.deadline_epoch_s - CLEANUP_RESERVE_S:
            raise CampaignError("campaign birth budget entered cleanup reserve before Worker startup")
        worker_info = self.preflight._guarded_direct_native_call(
            self.direct_native_call_guard, self.evidence,
            "start_worker/connect/version/isolation readback", self.server.start_worker)
        if worker_info.get("status") != "WORKER_CONNECTED_LOOPBACK_PROOF_PASSED":
            raise CampaignError("Worker loopback connection/isolation receipt is not a pass")
        worker_runtime = worker_info.get("worker_runtime")
        worker_pid = worker_runtime.get("pid") if isinstance(worker_runtime, Mapping) else None
        self.worker_port = worker_runtime.get("port") if isinstance(worker_runtime, Mapping) else None
        if type(worker_pid) is not int or type(self.worker_port) is not int:
            raise CampaignError("owned Worker returned no exact PID/port identity")
        from comsol_mcp._g2_isolation import _process_snapshot
        identity = _process_snapshot(worker_pid)
        if (not isinstance(identity, Mapping) or identity.get("pid") != worker_pid or
                not isinstance(identity.get("birth"), str) or
                not isinstance(identity.get("command"), str)):
            raise CampaignError("owned Worker PID/birth/command identity could not be read")
        self.worker_identity = dict(identity)
        worker_info = {**dict(worker_info), "worker_process_identity": self.worker_identity}
        _write_json_fsynced(self.evidence / "worker_connection_receipt.json", worker_info)
        return worker_info

    def create_adapter(self, *, stress_components: Mapping[str, Mapping[str, str]],
                       work: Path, evidence: Path) -> ScienceCampaignAdapter:
        from comsol_mcp._control_daemon import ControlDaemon
        from tools.run_native_resume_smoke import _dispatch

        if (self.server is None or self.project_id is None or self.project_workspace is None or
                self.science_fixture_path is None or self.v2_control_fixture_path is None or
                self.coupon_fixture_path is None or not self.input_paths or self.server.worker is None):
            raise CampaignError("owned server, project authority, fixture, input copies, or Worker is missing")
        os.environ["COMSOL_ROOT"] = str(self.server.shadow_root)
        os.environ["COMSOL_JAVA_HOME"] = str(self.preflight.JAVA_HOME)
        os.environ["COMSOL_PREFS_DIR"] = str(self.server.prefs)
        os.environ["COMSOL_PROJECT_ROOT"] = str(self.server.project)
        if os.environ.get(self.preflight.TRUSTED_CODE_STARTUP_ENV, "").strip().lower() not in {"1", "true", "yes"}:
            raise CampaignError("trusted_code startup opt-in changed before managed Worker construction")
        os.environ[self.preflight.ISOLATION_RECEIPT_ENV] = str(self.server.receipt_path)
        self.daemon = ControlDaemon(work / "control", worker=self.server.worker,
                                    project_root=self.server.project)
        actual_grants = self.preflight._require_trusted_code_host_grant(self.daemon)
        if actual_grants != self.prebirth_host_grants:
            raise CampaignError("managed post-Worker trusted_code grant differs from the prebirth host ceiling")
        authority = getattr(self.daemon, "project_authority", None)
        record = authority.get_project(self.project_id) if authority else None
        if (not isinstance(record, Mapping) or record.get("project_id") != self.project_id or
                Path(str(record.get("workspace", ""))).resolve(strict=True) != self.project_workspace):
            raise CampaignError("registered project identity/workspace changed after Worker birth")

        adapter = NativeScienceCampaignAdapter(
            daemon=self.daemon, server=self.server, project_id=self.project_id,
            project_workspace=self.project_workspace, work=work, evidence=evidence,
            science_fixture=self.science_fixture_path, input_paths=self.input_paths,
            v2_control_fixture=self.v2_control_fixture_path,
            coupon_fixture=self.coupon_fixture_path,
            cure_law_v2_enabled=self.cure_law_v2_enabled,
            source_manifest=self.candidate_source_manifest,
            stress_components=stress_components, setup_runner=self.preflight,
            runtime_worker_limit=MAX_SEQUENTIAL_WORKERS)
        connect = adapter._dispatch("server_connect", {
            "host": "127.0.0.1", "port": self.server.port,
        }, timeout_s=120.0)
        adapter._log_response("server_connect", connect)
        if (connect.get("success") is not True or
                connect.get("data", {}).get("endpoint") != f"127.0.0.1:{self.server.port}"):
            raise CampaignError("managed server_connect did not verify the exact task-owned loopback endpoint")
        self.adapter = adapter
        _write_json_fsynced(evidence / "managed_server_connection.json", connect)
        return adapter

    def run_controller(self, adapter: ScienceCampaignAdapter, *, birth_budget: BirthBudget,
                       stress_components: Mapping[str, Mapping[str, str]],
                       work: Path, evidence: Path) -> Mapping[str, Any]:
        if self.project_workspace is None or self.solve_ledger_path is None:
            raise CampaignError("registered project solve ledger/workspace was not prepared")
        output_path = self.project_workspace / "outputs" / "staged_baseline_after_cool.mph"
        if output_path.exists() or output_path.is_symlink():
            raise CampaignError("staged baseline save path already exists; no overwrite is allowed")
        return execute_solve_plan(
            adapter, ledger_path=self.solve_ledger_path, evidence=evidence,
            birth_budget=birth_budget, stress_components=stress_components,
            staged_baseline_path=output_path)

    def reconcile_for_cleanup(self) -> Mapping[str, Any]:
        """Capture full ledgers and public Worker quiescence before fenced TERM."""
        if self.server is None or self.daemon is None or self.project_id is None:
            return {"status": "RECONCILIATION_INCOMPLETE", "safe_for_owned_cleanup": False,
                    "reason": "owned server, daemon, or project identity is unavailable"}
        from tools.run_native_w24_cure_preflight import (
            _cleanup_reconciliation, _project_job_inventory,
            _worker_request_activity_inventory,
        )
        from comsol_mcp._g2_isolation import _process_snapshot

        jobs = _project_job_inventory(self.daemon, self.project_id)
        worker_requests = _worker_request_activity_inventory(self.daemon.store, self.project_id)
        direct_calls: list[dict[str, Any]] = []
        direct_calls_safe = self.direct_native_call_guard.get("all_returned") is True and all(
            call.get("status") == "RETURNED" for call in self.direct_native_call_guard.get("calls", []))
        if self.adapter is not None:
            for session_index in range(1, self.adapter.worker_sessions + 1):
                direct_calls.append(_read_direct_rpc_journal(
                    self.adapter.direct_rpc_journal, worker_session_index=session_index,
                    project_id=self.project_id))
            direct_calls_safe = direct_calls_safe and bool(direct_calls) and all(
                row.get("complete") is True for row in direct_calls)

        process_identities = {"server_expected": self.server.process_identity,
                              "worker_expected": self.worker_identity}
        exact_identity_safe = bool(
            isinstance(self.server.process_identity, Mapping) and
            self.server.proc is not None and self.server.proc.poll() is None and
            isinstance(self.worker_identity, Mapping) and self.server.worker is not None and
            self.server.worker._process is not None and self.server.worker._process.poll() is None and
            _identity_same(self.worker_identity, _capture_worker_process_identity(self.server)))
        if exact_identity_safe:
            server_current = _process_snapshot(self.server.proc.pid)
            exact_identity_safe = _identity_same(self.server.process_identity, server_current)
            process_identities["server_current"] = server_current

        session_id = self._current_worker_session_id()
        public_snapshot = None
        quiescence_error = None
        try:
            public_snapshot = self.daemon.quiescence_readback(self.project_id, session_id)
            public_quiescent = _worker_quiescence_readback_is_safe(
                public_snapshot, project_id=self.project_id, session_id=session_id,
                worker=self.server.worker,
                worker_identity=getattr(self.daemon.backend, "worker_identity", None))
        except Exception as exc:
            public_quiescent = False
            quiescence_error = f"{type(exc).__name__}: {exc}"
        cleanup = _cleanup_reconciliation(
            jobs, worker_requests, direct_native_calls_safe=direct_calls_safe)
        checks = dict(cleanup.get("checks", {}))
        checks.update({"exact_owned_process_identities_stable": exact_identity_safe,
                       "public_global_worker_quiescence": public_quiescent})
        safe = all(checks.values())
        receipt = {
            "schema": "W24_CAMPAIGN_OWNED_CLEANUP_RECONCILIATION_V1",
            "status": "SAFE_FOR_EXACT_OWNED_CLEANUP" if safe else "ACTIVE_OR_UNKNOWN",
            "safe_for_owned_cleanup": safe,
            "project_id": self.project_id,
            "project_workspace": str(self.project_workspace),
            "project_jobs": jobs,
            "worker_requests": worker_requests,
            "direct_native_call_guard": self.direct_native_call_guard,
            "direct_rpc_sessions": direct_calls,
            "public_worker_quiescence": dict(public_snapshot) if isinstance(public_snapshot, Mapping) else None,
            "public_worker_quiescence_error": quiescence_error,
            "worker_session_id": session_id,
            "process_identities": process_identities,
            "checks": checks,
            "durable_unknown_preserved": True,
            "study_run_submission_count": len(read_solve_ledger(self.solve_ledger_path)) if self.solve_ledger_path else None,
        }
        _write_json_fsynced(self.evidence / "campaign_cleanup_reconciliation.json", receipt)
        return receipt

    def cleanup_exact_owned(self, reconciliation: Mapping[str, Any]) -> Mapping[str, Any]:
        if (self.server is None or self.server.proc is None or
                not isinstance(self.server.process_identity, Mapping) or
                not isinstance(self.worker_identity, Mapping) or
                type(self.worker_port) is not int or
                reconciliation.get("safe_for_owned_cleanup") is not True):
            return {"status": "CLEANUP_BLOCKED", "reason": "exact identity or terminal reconciliation incomplete"}
        from tools.run_native_w23_te_managed_preflight import _exact_owned_cleanup
        from comsol_mcp._g2_isolation import _process_snapshot
        from comsol_mcp._control_daemon import WorkerRetirementRefused
        from tools.run_native_w24_cure_preflight import (
            _cleanup_reconciliation, _process_inventory,
            _project_job_inventory, _worker_request_activity_inventory,
        )

        retirement_guard = getattr(self.daemon, "worker_retirement_guard", None)
        if not callable(retirement_guard):
            return {"status": "CLEANUP_BLOCKED", "reason": "ControlDaemon has no admission-fenced Worker retirement guard"}
        try:
            session_id = self._current_worker_session_id()
        except CampaignError as exc:
            return {"status": "CLEANUP_BLOCKED", "reason": str(exc)}

        jobs = _project_job_inventory(self.daemon, self.project_id)
        worker_requests = _worker_request_activity_inventory(self.daemon.store, self.project_id)
        direct_calls_safe = self.direct_native_call_guard.get("all_returned") is True
        if self.adapter is not None:
            direct_calls_safe = direct_calls_safe and all(
                _read_direct_rpc_journal(self.adapter.direct_rpc_journal,
                                         worker_session_index=index,
                                         project_id=self.project_id).get("complete") is True
                for index in range(1, self.adapter.worker_sessions + 1))
        cleanup_reconciliation = _cleanup_reconciliation(
            jobs, worker_requests, direct_native_calls_safe=direct_calls_safe)
        if cleanup_reconciliation.get("safe_for_owned_cleanup") is not True:
            return {"status": "CLEANUP_BLOCKED", "reconciliation": cleanup_reconciliation}

        # The public snapshot is useful evidence but is not a lease. Acquire
        # the shared admission fence before repeating all checks and keep it
        # held through exact Worker/server TERM and postcondition verification.
        try:
            with retirement_guard(self.project_id, session_id) as lease:
                lease_readback = lease.readback
                if not _worker_quiescence_readback_is_safe(
                        lease_readback, project_id=self.project_id, session_id=session_id,
                        worker=self.server.worker,
                        worker_identity=getattr(self.daemon.backend, "worker_identity", None)):
                    return {"status": "CLEANUP_BLOCKED",
                            "reason": "admission-fenced Worker readback is not exact and quiescent",
                            "worker_quiescence": lease_readback}

                # Re-read the project ledger and direct journals while the
                # admission fence is held. Durable UNKNOWN remains untouched.
                fenced_jobs = _project_job_inventory(self.daemon, self.project_id)
                fenced_requests = _worker_request_activity_inventory(self.daemon.store, self.project_id)
                fenced_direct_safe = self.direct_native_call_guard.get("all_returned") is True and all(
                    call.get("status") == "RETURNED"
                    for call in self.direct_native_call_guard.get("calls", []))
                fenced_direct_calls: list[dict[str, Any]] = []
                if self.adapter is not None:
                    for index in range(1, self.adapter.worker_sessions + 1):
                        row = _read_direct_rpc_journal(
                            self.adapter.direct_rpc_journal, worker_session_index=index,
                            project_id=self.project_id)
                        fenced_direct_calls.append(row)
                    fenced_direct_safe = fenced_direct_safe and bool(fenced_direct_calls) and all(
                        row.get("complete") is True for row in fenced_direct_calls)
                fenced_cleanup = _cleanup_reconciliation(
                    fenced_jobs, fenced_requests, direct_native_calls_safe=fenced_direct_safe)
                if fenced_cleanup.get("safe_for_owned_cleanup") is not True:
                    return {"status": "CLEANUP_BLOCKED",
                            "reason": "fenced project ledgers or direct RPC journals are incomplete",
                            "reconciliation": fenced_cleanup,
                            "worker_quiescence": lease_readback}

                lease.mark_cleanup_started()
                receipt = _exact_owned_cleanup(
                    self.server, server_identity=dict(self.server.process_identity),
                    worker_identity=dict(self.worker_identity), worker_port=self.worker_port,
                    process_snapshot=_process_snapshot, reconciliation=fenced_cleanup,
                    evidence=self.evidence, require_fixture_terminal=False)
                worker_process = getattr(self.server.worker, "_process", None)
                if worker_process is None or worker_process.poll() is None:
                    receipt["worker_retirement_confirmation"] = "EXACT_WORKER_STILL_LIVE_OR_UNOBSERVED"
                    _write_json_fsynced(self.evidence / "exact_owned_cleanup_receipt.json", receipt)
                    return receipt
                lease.confirm_worker_stopped()
                receipt["worker_retirement_confirmation"] = "EXACT_POPEN_EXIT_CONFIRMED_UNDER_ADMISSION_FENCE"
                receipt["worker_quiescence_readback"] = lease_readback
        except WorkerRetirementRefused as exc:
            return {"status": "CLEANUP_BLOCKED",
                    "reason": f"admission-fenced Worker retirement refused: {exc}",
                    "durable_unknown_preserved": True}
        except Exception as exc:
            return {"status": "CLEANUP_UNVERIFIED",
                    "reason": f"{type(exc).__name__}: {exc}",
                    "durable_unknown_preserved": True}

        if receipt.get("status") == "CLEANUP_VERIFIED_EXACT_OWNED_PIDS_AND_PORTS_ABSENT":
            post = _process_inventory()
            receipt["global_postcleanup_inventory"] = post
            if post.get("quiescent") is not True:
                receipt["status"] = "CLEANUP_UNVERIFIED"
                receipt["postcleanup_failure"] = "global COMSOL/Worker/listener inventory is not quiescent"
            _write_json_fsynced(self.evidence / "exact_owned_cleanup_receipt.json", receipt)
        if getattr(self, "daemon", None) is not None:
            try:
                self.daemon.close()
                self.daemon = None
            except Exception as exc:
                receipt["daemon_close_error"] = f"{type(exc).__name__}: {exc}"
                receipt["status"] = "CLEANUP_UNVERIFIED"
                _write_json_fsynced(self.evidence / "exact_owned_cleanup_receipt.json", receipt)
        return receipt

    def _current_worker_session_id(self) -> str:
        backend = getattr(self.daemon, "backend", None)
        service = getattr(backend, "service", None)
        ledger = getattr(service, "ledger", None)
        session_id = getattr(ledger, "session_id", None)
        if not isinstance(session_id, str) or not session_id:
            raise CampaignError("live ControlDaemon has no exact Worker session binding")
        bindings = []
        if self.adapter is not None:
            bindings = list(self.adapter.models.values()) + list(self.adapter.mechanics_models.values())
        if bindings and any(binding.session_id != session_id for binding in bindings):
            raise CampaignError("managed ModelRefs disagree with the live Worker session binding")
        return session_id


def _project_path(workspace: Path, path: Path | str, *, must_exist: bool) -> Path:
    """Confine Java input/output paths to the exact registered project workspace."""
    root = workspace.resolve(strict=True)
    if not root.is_dir():
        raise CampaignError("registered science workspace is not a directory")
    supplied = Path(path)
    if ".." in supplied.parts:
        raise CampaignError("project path contains parent traversal")
    candidate = supplied if supplied.is_absolute() else root / supplied
    candidate = Path(os.path.abspath(candidate))
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise CampaignError("native input/output path is outside the registered science workspace") from exc
    if not relative.parts:
        raise CampaignError("native input/output path must name a child of the registered workspace")
    cursor = root
    try:
        for part in relative.parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise CampaignError("native input/output path cannot traverse a symlink")
        if must_exist:
            resolved = candidate.resolve(strict=True)
            if resolved != candidate or not resolved.is_file():
                raise CampaignError("native project input is missing, aliased, or not a regular file")
        else:
            if candidate.exists():
                raise CampaignError("native project output already exists; refusing to overwrite it")
            if not candidate.parent.is_dir():
                raise CampaignError("native project output parent directory does not exist")
            resolved_parent = candidate.parent.resolve(strict=True)
            if resolved_parent != candidate.parent or not resolved_parent.is_relative_to(root):
                raise CampaignError("native project output parent is aliased or outside the workspace")
    except OSError as exc:
        raise CampaignError("native project path could not be inspected") from exc
    return candidate


def _evidence_copy(project_path: Path, evidence_path: Path, *, status: str) -> dict[str, Any]:
    """Copy a Worker-written project artifact into immutable task evidence and hash both."""
    project_path = project_path.resolve(strict=True)
    if not project_path.is_file() or project_path.is_symlink():
        raise CampaignError("Worker artifact is not a regular project file")
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    payload = project_path.read_bytes()
    with evidence_path.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    project_hash = sha256(project_path)
    evidence_hash = sha256(evidence_path)
    if (len(payload) == 0 or project_hash != evidence_hash or
            project_path.stat().st_size != evidence_path.stat().st_size):
        raise CampaignError("project artifact and evidence copy differ")
    return {"status": status, "project_path": str(project_path),
            "project_size_bytes": project_path.stat().st_size,
            "project_sha256": project_hash, "path": str(evidence_path.resolve(strict=True)),
            "size_bytes": evidence_path.stat().st_size, "sha256": evidence_hash}


class NativeScienceCampaignAdapter:
    """Production managed adapter for the frozen two-mechanics/eight-cure plan."""

    def __init__(self, *, daemon: Any, server: Any, project_id: str,
                 project_workspace: Path, work: Path, evidence: Path,
                 science_fixture: Path, input_paths: Mapping[str, Path],
                 v2_control_fixture: Path, coupon_fixture: Path,
                 cure_law_v2_enabled: bool,
                 source_manifest: Mapping[str, Mapping[str, Any]],
                 stress_components: Mapping[str, Mapping[str, str]],
                 setup_runner: Any, runtime_worker_limit: int = MAX_SEQUENTIAL_WORKERS):
        self.daemon = daemon
        self.server = server
        self.project_id = project_id
        self.workspace = project_workspace.resolve(strict=True)
        self.work = work
        self.evidence = evidence
        self.fixture = _project_path(self.workspace, science_fixture, must_exist=True)
        self.v2_control_fixture = _project_path(self.workspace, v2_control_fixture, must_exist=True)
        self.coupon_fixture = _project_path(self.workspace, coupon_fixture, must_exist=True)
        if not isinstance(cure_law_v2_enabled, bool):
            raise CampaignError("adapter v1/v2 mode must come from a validated setup readback")
        self.cure_law_v2_enabled = cure_law_v2_enabled
        self.source_manifest = {str(name): dict(row) for name, row in source_manifest.items()
                                if isinstance(row, Mapping)}
        self.fixture_source_hashes = {
            "science": self._pinned_source_hash("tools/java/W24CureScienceFixture.java", self.fixture),
            "v2_control": self._pinned_source_hash("tools/java/W24CureLawV2ControlFixture.java", self.v2_control_fixture),
            "coupon": self._pinned_source_hash("tools/java/W24CureCouponFixture.java", self.coupon_fixture),
        }
        self.input_paths = {key: _project_path(self.workspace, value, must_exist=True)
                            for key, value in input_paths.items()}
        self.stress_components = {role: dict(value) for role, value in stress_components.items()}
        self.setup_runner = setup_runner
        if runtime_worker_limit != MAX_SEQUENTIAL_WORKERS:
            raise CampaignError("adapter worker limit differs from the frozen two-sequential-worker cap")
        self.models: dict[str, ManagedModelBinding] = {}
        self.model_load_receipts: dict[str, dict[str, Any]] = {}
        self.mechanics_builds: dict[str, dict[str, Any]] = {}
        self.mechanics_models: dict[str, ManagedModelBinding] = {}
        self.slot_solver_tags: dict[tuple[str, str], str] = {}
        self.captures: dict[str, Mapping[str, Any]] = {}
        self._authenticated_v2_frame_receipts: dict[str, dict[str, Any]] = {}
        self.v2_contract_readbacks: dict[str, dict[str, Any]] = {}
        self.slot_native_setup_readbacks: dict[str, dict[str, Any]] = {}
        self.slot_study_run_actions: dict[str, dict[str, Any]] = {}
        self.staged_baseline_saved_model: dict[str, Any] | None = None
        self.reopened_stage_captures: dict[str, Mapping[str, Any]] = {}
        self.project_ledger: Path | None = None
        self.worker_sessions = 1
        self.worker_connection_receipts: list[dict[str, Any]] = []
        self.worker_births: list[dict[str, Any]] = []
        self.worker_start_attempt_count = 1
        self.worker2_start_intent_written = False
        self._immutable_project_id = project_id
        self._retired_model_ref_epochs: set[tuple[str, str, int]] = set()
        self._retired_worker_server_instance_ids: set[str] = set()
        self._worker_object = getattr(server, "worker", None)
        self._worker_process_identity = _capture_worker_process_identity(server)
        self.staged_baseline_configuration_readback: dict[str, Any] | None = None
        self.action_log = self.evidence / "managed_native_actions.jsonl"
        self.direct_rpc_journal = self.evidence / "direct_rpc_journal.jsonl"
        self.worker_lifecycle_log = self.evidence / "worker_lifecycle.jsonl"
        initial_birth = _observe_worker_birth(server, attempt_index=1)
        if (initial_birth.get("birth_observed") is not True or
                not isinstance(initial_birth.get("process_identity"), Mapping) or
                not _identity_same(self._worker_process_identity, initial_birth["process_identity"])):
            raise CampaignError("initial connected Worker birth lacks matching Popen and OS identity evidence")
        initial_birth.update({"event": "worker_birth_observed", "worker_session_index": 1,
                              "connection_status": "CONNECTED_BY_SETUP_PREFLIGHT",
                              "at_epoch_s": _current_epoch_s()})
        self.worker_births.append(initial_birth)
        _append_fsynced_jsonl(self.worker_lifecycle_log, initial_birth)
        _append_fsynced_jsonl(self.worker_lifecycle_log, {
            "event": "worker_connection_succeeded", "attempt_index": 1,
            "worker_session_index": 1, "worker_pid": self._worker_process_identity["pid"],
            "worker_birth_count": len(self.worker_births),
            "successful_connected_worker_sessions": self.worker_sessions,
            "connection_status": "CONNECTED_BY_SETUP_PREFLIGHT",
            "at_epoch_s": _current_epoch_s()})

    def _pinned_source_hash(self, manifest_key: str, project_path: Path) -> str:
        row = self.source_manifest.get(manifest_key)
        expected = row.get("sha256") if isinstance(row, Mapping) else None
        observed = sha256(project_path)
        if not isinstance(expected, str) or not SHA256_RE.fullmatch(expected) or expected != observed:
            raise CampaignError(f"registered project Java source differs from frozen manifest: {manifest_key}")
        return expected

    def _journaled_rpc(self, operation: str, call: Any, *, worker_required: bool) -> dict[str, Any]:
        """Persist request start/terminal response around every managed RPC.

        The durable job ledger remains the source for Worker submission state;
        this second journal proves that the Python caller observed a complete
        response before a Worker transition.
        """
        if self.project_id != self._immutable_project_id:
            raise CampaignError("registered project identity changed during the science campaign")
        call_id = str(uuid4())
        base = {"call_id": call_id, "operation": operation,
                "project_id": self.project_id, "worker_session_index": self.worker_sessions}
        _append_fsynced_jsonl(self.direct_rpc_journal, {
            "event": "direct_rpc_started", **base, "at_epoch_s": _current_epoch_s()})
        try:
            response = call()
        except BaseException as exc:
            _append_fsynced_jsonl(self.direct_rpc_journal, {
                "event": "direct_rpc_finished", **base, "at_epoch_s": _current_epoch_s(),
                "rpc_returned": False, "terminal": False,
                "error": f"{type(exc).__name__}: {exc}"})
            raise
        worker_data = response.get("data", {}).get("worker") if isinstance(response, Mapping) else None
        worker_observed = isinstance(worker_data, Mapping)
        worker_terminal = self.setup_runner._worker_request_terminal(response)
        terminal = (isinstance(response, Mapping) and response.get("success") is True and
                    ((worker_terminal if worker_required else True) if worker_observed else not worker_required))
        worker_status = worker_data.get("status") if worker_observed else None
        _append_fsynced_jsonl(self.direct_rpc_journal, {
            "event": "direct_rpc_finished", **base, "at_epoch_s": _current_epoch_s(),
            "rpc_returned": True, "terminal": bool(terminal),
            "worker_observed": worker_observed, "worker_status": worker_status,
            "success": response.get("success") is True if isinstance(response, Mapping) else False,
            "request_id": (response.get("execution", {}).get("request_id")
                           if isinstance(response, Mapping) and
                          isinstance(response.get("execution"), Mapping) else None)})
        return dict(response) if isinstance(response, Mapping) else response

    def _journaled_worker2_start(self, call: Any) -> Any:
        """Journal synchronous Worker 2 startup outside the managed job queue."""
        call_id = str(uuid4())
        base = {"call_id": call_id, "operation": "worker.start_connect_and_version_readback",
                "project_id": self.project_id, "worker_session_index": 2}
        _append_fsynced_jsonl(self.direct_rpc_journal, {
            "event": "direct_rpc_started", **base, "at_epoch_s": _current_epoch_s()})
        try:
            result = call()
        except BaseException as exc:
            _append_fsynced_jsonl(self.direct_rpc_journal, {
                "event": "direct_rpc_finished", **base, "at_epoch_s": _current_epoch_s(),
                "rpc_returned": False, "terminal": False,
                "error": f"{type(exc).__name__}: {exc}"})
            raise
        terminal = (isinstance(result, Mapping) and
                    result.get("status") == "WORKER_CONNECTED_LOOPBACK_PROOF_PASSED")
        _append_fsynced_jsonl(self.direct_rpc_journal, {
            "event": "direct_rpc_finished", **base, "at_epoch_s": _current_epoch_s(),
            "rpc_returned": True, "terminal": terminal,
            "worker_observed": True,
            "worker_status": result.get("status") if isinstance(result, Mapping) else None,
            "success": terminal,
        })
        return result

    def _require_worker_transition_safe(self, worker_quiescence: Mapping[str, Any]) -> dict[str, Any]:
        bindings = list(self.models.values()) + list(self.mechanics_models.values())
        logical_sessions = {binding.session_id for binding in bindings}
        if not bindings or len(logical_sessions) != 1:
            raise CampaignError("Worker transition requires current project-bound ModelRefs from one logical session")
        worker_session_id = next(iter(logical_sessions))
        receipt = _worker_transition_reconciliation(
            daemon=self.daemon, project_id=self.project_id,
            immutable_project_id=self._immutable_project_id,
            project_workspace=self.workspace, server=self.server,
            expected_worker_object=self._worker_object,
            expected_worker_identity=self._worker_process_identity,
            direct_rpc_journal=self.direct_rpc_journal,
            worker_session_index=self.worker_sessions,
            worker_session_id=worker_session_id,
            worker_quiescence=worker_quiescence)
        # Logical session IDs are endpoint-scoped and intentionally survive a
        # Worker replacement. ModelRefs are bound to a specific Worker epoch
        # through server_instance_id; compare that identity instead.
        session_ids = sorted(logical_sessions)
        backend_identity = getattr(getattr(self.daemon, "backend", None), "worker_identity", None)
        ref_epochs: list[tuple[str, str, int]] = []
        epoch_checks: list[bool] = []
        for binding in bindings:
            try:
                ref_epoch = _model_ref_epoch(binding)
            except CampaignError:
                epoch_checks.append(False)
                continue
            ref_epochs.append(ref_epoch)
            epoch_checks.append(
                ref_epoch not in self._retired_model_ref_epochs and
                ref_epoch[1] not in self._retired_worker_server_instance_ids and
                _model_ref_matches_worker_epoch(binding, backend_identity))
        binding_safe = bool(bindings) and all(
            binding.project_id == self._immutable_project_id and
            binding.model_ref.get("session_id") == binding.session_id
            for binding in bindings) and len(epoch_checks) == len(bindings) and all(epoch_checks)
        receipt["checks"]["all_current_model_refs_match_project_and_worker_epoch"] = binding_safe
        receipt["current_model_ref_count"] = len(bindings)
        receipt["current_model_session_ids"] = session_ids
        receipt["current_model_ref_epochs"] = [list(epoch) for epoch in sorted(ref_epochs)]
        receipt["current_worker_identity"] = dict(backend_identity) if isinstance(backend_identity, Mapping) else None
        receipt["safe_to_close_first_worker"] = all(receipt["checks"].values())
        receipt["status"] = ("SAFE_TO_CLOSE_FIRST_WORKER" if receipt["safe_to_close_first_worker"]
                             else "TRANSITION_BLOCKED_ACTIVE_OR_UNKNOWN")
        _write_json_fsynced(self.evidence / "worker_transition_reconciliation.json", receipt)
        _append_fsynced_jsonl(self.evidence / "events.jsonl", {
            "event": "worker_transition_reconciled", "at_epoch_s": _current_epoch_s(),
            "status": receipt["status"], "checks": receipt["checks"]})
        if receipt["safe_to_close_first_worker"] is not True:
            raise CampaignError("first Worker transition is blocked by incomplete or unknown project/ledger/RPC/idle/identity proof")
        return receipt

    def _close_first_worker_term_only(self, *, retirement_lease: Any) -> dict[str, Any]:
        worker = self._worker_object
        process = getattr(worker, "_process", None)
        if worker is None or process is None or self.server.worker is not worker:
            raise CampaignError("exact first Worker object/process changed before orderly close")
        mark_cleanup_started = getattr(retirement_lease, "mark_cleanup_started", None)
        if not callable(mark_cleanup_started):
            raise CampaignError("Worker close requires the active ControlDaemon retirement lease")
        mark_cleanup_started()
        record: dict[str, Any] = {
            "status": "CLOSING_EXACT_WORKER_TERM_ONLY",
            "expected_identity": dict(self._worker_process_identity),
            "worker_session_index": self.worker_sessions,
            "actions": [],
        }
        call_id = str(uuid4())
        base = {"call_id": call_id, "operation": "worker.disconnect",
                "project_id": self.project_id, "worker_session_index": self.worker_sessions}
        _append_fsynced_jsonl(self.direct_rpc_journal, {
            "event": "direct_rpc_started", **base, "at_epoch_s": _current_epoch_s()})
        try:
            # This direct Worker RPC is outside ControlDaemon's job queue. It is
            # therefore journaled separately after the full managed-idle gate.
            worker.client().disconnect()
        except BaseException as exc:
            _append_fsynced_jsonl(self.direct_rpc_journal, {
                "event": "direct_rpc_finished", **base, "at_epoch_s": _current_epoch_s(),
                "rpc_returned": False, "terminal": False,
                "error": f"{type(exc).__name__}: {exc}"})
            record.update({"status": "WORKER_DISCONNECT_UNKNOWN",
                           "error": f"{type(exc).__name__}: {exc}"})
            _write_json_fsynced(self.evidence / "worker_transition_close.json", record)
            raise CampaignError("direct Worker disconnect did not return; preserving Worker process") from exc
        _append_fsynced_jsonl(self.direct_rpc_journal, {
            "event": "direct_rpc_finished", **base, "at_epoch_s": _current_epoch_s(),
            "rpc_returned": True, "terminal": True, "worker_observed": True,
            "worker_status": "DISCONNECTED", "success": True})
        record["actions"].append({"action": "worker_disconnect", "status": "RETURNED"})

        # The existing Worker.close() contains an internal SIGKILL fallback.
        # Terminate only this verified child, wait without escalation, and call
        # close() only after Popen proves the child has already exited.
        if process.poll() is None:
            current = _capture_worker_process_identity(self.server)
            if not _identity_same(self._worker_process_identity, current):
                record.update({"status": "WORKER_IDENTITY_CHANGED_NO_SIGNAL",
                               "current_identity": current})
                _write_json_fsynced(self.evidence / "worker_transition_close.json", record)
                raise CampaignError("Worker PID/birth/command changed after disconnect; no signal sent")
            process.terminate()
            try:
                process.wait(timeout=8.0)
            except Exception as exc:
                record.update({"status": "WORKER_TERM_TIMEOUT_NO_ESCALATION",
                               "error": f"{type(exc).__name__}: {exc}"})
                _write_json_fsynced(self.evidence / "worker_transition_close.json", record)
                raise CampaignError("exact Worker did not exit after TERM; no hard-kill fallback was used") from exc
        if process.poll() is None:
            record["status"] = "WORKER_TERM_UNVERIFIED_NO_ESCALATION"
            _write_json_fsynced(self.evidence / "worker_transition_close.json", record)
            raise CampaignError("exact Worker remains alive after TERM; no hard-kill fallback was used")
        worker.close()  # Child is already exited, so this cannot enter its kill fallback.
        self.server.worker = None
        record["actions"].append({"action": "exact_worker_term", "status": "CHILD_EXITED",
                                   "return_code": process.returncode})
        record["status"] = "EXACT_WORKER_CLOSED_TERM_ONLY"
        _write_json_fsynced(self.evidence / "worker_transition_close.json", record)
        return record

    def _preserve_worker_connection_receipt(self, *, worker_index: int,
                                           expected_pid: int) -> dict[str, Any]:
        """Preserve an immutable Worker connection receipt before it is overwritten."""
        source = Path(getattr(self.server, "evidence", self.evidence)) / "worker_connection.json"
        destination = self.evidence / f"worker_connection_worker{worker_index}.json"
        if not source.is_file() or source.is_symlink():
            raise CampaignError("Worker 1 connection receipt is missing before the sequential transition")
        if destination.exists():
            if destination.is_symlink() or not destination.is_file() or sha256(destination) != sha256(source):
                raise CampaignError("preserved Worker 1 connection receipt conflicts with the original")
        else:
            payload = source.read_bytes()
            with destination.open("xb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            dir_fd = os.open(destination.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        try:
            receipt = json.loads(destination.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CampaignError("preserved Worker 1 connection receipt is unreadable") from exc
        runtime = receipt.get("worker_runtime") if isinstance(receipt, Mapping) else None
        if (not isinstance(runtime, Mapping) or runtime.get("pid") != expected_pid or
                receipt.get("status") != "WORKER_CONNECTED_LOOPBACK_PROOF_PASSED"):
            raise CampaignError(f"Worker {worker_index} connection receipt does not match the exact connected Worker PID")
        return {"path": str(destination), "sha256": sha256(destination),
                "size_bytes": destination.stat().st_size,
                "worker_pid": runtime.get("pid"), "status": receipt.get("status")}

    def _record_worker_start_attempt(self, *, attempt_index: int, outcome: str,
                                     error: str | None = None) -> dict[str, Any]:
        observation = _observe_worker_birth(self.server, attempt_index=attempt_index)
        if observation.get("birth_observed") is True:
            already_recorded = any(row.get("attempt_index") == attempt_index
                                   for row in self.worker_births)
            if not already_recorded:
                observation.update({"event": "worker_birth_observed",
                                   "worker_session_index": attempt_index,
                                   "at_epoch_s": _current_epoch_s()})
                self.worker_births.append(observation)
                _append_fsynced_jsonl(self.worker_lifecycle_log, observation)
        else:
            observation.update({"event": "worker_start_attempt_terminal_without_birth",
                                "worker_session_index": attempt_index,
                                "at_epoch_s": _current_epoch_s()})
            _append_fsynced_jsonl(self.worker_lifecycle_log, observation)
        terminal = {"event": "worker_start_attempt_terminal", "attempt_index": attempt_index,
                    "outcome": outcome, "error": error, "birth_observation": observation,
                    "worker_birth_count": len(self.worker_births),
                    "successful_connected_worker_sessions": self.worker_sessions,
                    "at_epoch_s": _current_epoch_s()}
        _append_fsynced_jsonl(self.worker_lifecycle_log, terminal)
        return observation

    def _validate_current_worker_binding(self, binding: ManagedModelBinding,
                                         *, name: str) -> tuple[str, str, int]:
        epoch = _model_ref_epoch(binding)
        if binding.project_id != self._immutable_project_id:
            raise CampaignError(f"managed model_load for {name} returned a different registered project")
        if (epoch in self._retired_model_ref_epochs or
                epoch[1] in self._retired_worker_server_instance_ids):
            raise CampaignError(f"managed model_load for {name} returned a retired Worker epoch")
        backend_identity = getattr(getattr(self.daemon, "backend", None), "worker_identity", None)
        if not _model_ref_matches_worker_epoch(binding, backend_identity):
            raise CampaignError(f"managed model_load for {name} does not match the active Worker server_instance_id")
        return epoch

    def _dispatch(self, operation: str, arguments: Mapping[str, Any], *,
                  binding: ManagedModelBinding | None = None, timeout_s: float = 120.0,
                  request_id: str | None = None,
                  idempotency_key: str | None = None) -> dict[str, Any]:
        from tools.run_native_resume_smoke import _dispatch

        if self.daemon is None or self.project_id != self._immutable_project_id:
            raise CampaignError("managed dispatch lacks the immutable registered project or active daemon")
        return self._journaled_rpc(
            operation,
            lambda: _dispatch(
                self.daemon, operation, dict(arguments), project_id=self.project_id,
                ref=dict(binding.model_ref) if binding is not None else None,
                revision=binding.revision if binding is not None else None,
                idempotency_key=(idempotency_key if idempotency_key is not None
                                 else f"w24-science-{uuid4()}"),
                request_id=(request_id if request_id is not None
                            else f"w24-science-request-{uuid4()}"), rpc_timeout_s=timeout_s),
            worker_required=operation in {"operation_call", "registry_call", "model_load",
                                          "model.inspect", "study.run"})

    def _log_response(self, action: str, response: Mapping[str, Any]) -> None:
        _append_fsynced_jsonl(self.action_log, {
            "event": "managed_native_action_returned", "at_epoch_s": _current_epoch_s(),
            "action": action, "success": response.get("success"),
            "worker_status": (response.get("data", {}).get("worker", {}).get("status")
                              if isinstance(response.get("data"), Mapping) and
                              isinstance(response.get("data", {}).get("worker"), Mapping) else None),
            "request_id": response.get("execution", {}).get("request_id")
                          if isinstance(response.get("execution"), Mapping) else None,
            "response": dict(response),
        })

    def _update_binding(self, prior: ManagedModelBinding,
                        response: Mapping[str, Any]) -> ManagedModelBinding:
        execution = response.get("execution")
        if not isinstance(execution, Mapping):
            raise CampaignError("managed native action omitted execution identity")
        echoed_project = execution.get("project_id")
        ref = execution.get("model_ref")
        revision = execution.get("revision")
        session_id = execution.get("session_id", prior.session_id)
        if (echoed_project is not None and echoed_project != self.project_id or
                session_id != prior.session_id or not isinstance(ref, Mapping) or
                dict(ref) != dict(prior.model_ref) or isinstance(revision, bool) or
                not isinstance(revision, int) or revision < prior.revision):
            raise CampaignError("managed native action changed or omitted the exact project ModelRef/revision")
        current = ManagedModelBinding(self.project_id, prior.session_id, dict(ref), revision)
        stored = self.daemon.backend.model_project_binding(dict(ref))
        if (not isinstance(stored, Mapping) or stored.get("attribution") != "PROJECT_BOUND" or
                stored.get("project_id") != self.project_id):
            raise CampaignError("native action ModelRef lost its persisted project association")
        if prior.model_tag in self.models:
            self.models[prior.model_tag] = current
        for key, binding in list(self.mechanics_models.items()):
            if binding.model_tag == prior.model_tag:
                self.mechanics_models[key] = current
        return current

    def _managed_model_inspect(self, binding: ManagedModelBinding,
                               *, timeout_s: float) -> dict[str, Any]:
        response = self._dispatch("model.inspect", {"detail": "summary"},
                                  binding=binding, timeout_s=timeout_s)
        self._log_response("model.inspect", response)
        if response.get("success") is not True:
            raise CampaignError("managed native model.inspect failed: " +
                                json.dumps(response, ensure_ascii=False, default=str)[:3000])
        execution = response.get("execution")
        data = response.get("data")
        if not isinstance(execution, Mapping) or not isinstance(data, Mapping):
            raise CampaignError("managed model.inspect omitted its execution/data readback")
        echo = execution.get("model_ref")
        identity = data.get("model_identity")
        if (not isinstance(echo, Mapping) or dict(echo) != dict(binding.model_ref) or
                not isinstance(identity, Mapping) or dict(identity) != dict(binding.model_ref)):
            raise CampaignError("managed model.inspect did not read back the exact native model identity")
        if execution.get("session_id", binding.session_id) != binding.session_id:
            raise CampaignError("managed model.inspect returned a different Worker session")
        project_echo = execution.get("project_id")
        if project_echo is not None and project_echo != self.project_id:
            raise CampaignError("managed model.inspect echoed a foreign project id")
        stored = self.daemon.backend.model_project_binding(dict(binding.model_ref))
        if (not isinstance(stored, Mapping) or stored.get("attribution") != "PROJECT_BOUND" or
                stored.get("project_id") != self.project_id):
            raise CampaignError("model.inspect ModelRef is not persistently bound to this project")
        return {"response": response, "data": dict(data),
                "model_binding": binding.as_record(), "persisted_binding": dict(stored)}

    def _fixture_action(self, binding: ManagedModelBinding, action: str,
                        arguments: Mapping[str, Any], *, timeout_s: float,
                        source_fixture: Path | None = None,
                        entrypoint: str | None = None,
                        request_id: str | None = None,
                        idempotency_key: str | None = None) -> tuple[ManagedModelBinding, dict[str, Any], dict[str, Any]]:
        native_arguments = dict(arguments)
        for key in ("path", "output_path", "ledger_path", "save_after_success_path",
                    "equation_view_path"):
            value = native_arguments.get(key)
            if isinstance(value, str):
                candidate = Path(value)
                if key == "ledger_path" and candidate.is_file():
                    _project_path(self.workspace, candidate, must_exist=True)
                elif key == "path" and candidate.exists():
                    _project_path(self.workspace, candidate, must_exist=True)
                else:
                    _project_path(self.workspace, candidate, must_exist=False)
        source_path = self.fixture if source_fixture is None else _project_path(
            self.workspace, source_fixture, must_exist=True)
        source_name = source_path.name
        if entrypoint is None:
            entrypoint = {
                self.fixture.name: "W24CureScienceFixture#run",
                self.v2_control_fixture.name: "W24CureLawV2ControlFixture#run",
                self.coupon_fixture.name: "W24CureCouponFixture#run",
            }.get(source_name)
        if not isinstance(entrypoint, str) or not entrypoint:
            raise CampaignError("managed Java action lacks its exact frozen fixture entrypoint")
        request_arguments = {
            "operation_id": "code.execute_java",
            "arguments": {"source_artifact": str(source_path.relative_to(self.workspace)),
                          "entrypoint": entrypoint,
                          "arguments": {"action": action, **native_arguments},
                          "mode": "trusted"},
        }
        dispatch_options: dict[str, Any] = {"binding": binding, "timeout_s": timeout_s}
        if request_id is not None:
            dispatch_options["request_id"] = request_id
        if idempotency_key is not None:
            dispatch_options["idempotency_key"] = idempotency_key
        response = self._dispatch("operation_call", request_arguments, **dispatch_options)
        self._log_response(f"java.{action}", response)
        if not self.setup_runner._worker_request_terminal(response):
            raise CampaignError(f"Java action {action} has no observed terminal Worker result; no retry")
        result = self.setup_runner._java_action_readback(response, f"W24 Java {action}")
        updated = self._update_binding(binding, response)
        return updated, response, result

    def _record_public_java_action(self, *, action: str, response: Mapping[str, Any],
                                   binding_before: ManagedModelBinding,
                                   binding_after: ManagedModelBinding,
                                   source_fixture: Path, response_dir: Path,
                                   response_label: str) -> dict[str, Any]:
        from tools.w24_cure_v2_capture import CaptureError, verify_public_capture

        execution = response.get("execution")
        operation_id = execution.get("operation_id") if isinstance(execution, Mapping) else None
        if not isinstance(operation_id, str) or not operation_id:
            raise CampaignError(f"public Java {action} response lacks durable operation identity")
        source_path = _project_path(self.workspace, source_fixture, must_exist=True)
        source_hash = sha256(source_path)
        pinned_by_name = {
            self.fixture.name: self.fixture_source_hashes["science"],
            self.v2_control_fixture.name: self.fixture_source_hashes["v2_control"],
            self.coupon_fixture.name: self.fixture_source_hashes["coupon"],
        }
        if pinned_by_name.get(source_path.name) != source_hash:
            raise CampaignError(f"public Java {action} source no longer matches its frozen source manifest")
        try:
            verified = verify_public_capture(
                self.daemon, response, operation_id=operation_id,
                project_root=self.workspace, source_artifact_path=source_path,
                expected_source_sha256=pinned_by_name[source_path.name],
                expected_action=action, expected_project_id=self.project_id,
                expected_session_id=binding_before.session_id,
                expected_model_ref=binding_before.model_ref,
                expected_revision=binding_before.revision)
        except CaptureError as exc:
            raise CampaignError(f"public Java {action} failed private OperationStore authentication: {exc}") from exc
        if (verified.get("revision_after") != binding_after.revision or
                verified.get("model_ref") != dict(binding_after.model_ref) or
                verified.get("session_id") != binding_after.session_id):
            raise CampaignError(f"public Java {action} revision/model binding differs from the adapter transition")
        response_dir.mkdir(parents=True, exist_ok=True)
        safe_operation_id = re.sub(r"[^A-Za-z0-9_-]", "_", operation_id)
        response_path = response_dir / f"{response_label}_{safe_operation_id}.json"
        if response_path.exists() or response_path.is_symlink():
            raise CampaignError("public Java response evidence path already exists; no overwrite is allowed")
        _write_json_fsynced(response_path, dict(response))
        ref_identity_keys = ("request_id", "operation_id", "idempotency_key", "request_hash",
                             "job_id", "project_id", "session_id", "model_ref",
                             "revision_before", "revision_after", "source_path", "source_sha256")
        return {
            "status": verified["status"], "action": action,
            "source_path": str(source_path.relative_to(self.workspace)),
            "source_sha256": pinned_by_name[source_path.name],
            "binding_before": binding_before.as_record(),
            "binding_after": binding_after.as_record(),
            "public_identity": {key: verified.get(key) for key in ref_identity_keys},
            "artifact": verified.get("artifact"),
            "artifact_receipt": verified.get("artifact_receipt"),
            "capture_validation": verified.get("capture_validation"),
            "readback_data": verified.get("readback_data"),
            "response_evidence": {
                "path": str(response_path), "size_bytes": response_path.stat().st_size,
                "sha256": sha256(response_path),
            },
            "native_acceptance": "NOT_RUN",
        }

    def _reauthenticate_public_java_action(self, action_ref: Mapping[str, Any]) -> dict[str, Any]:
        from tools.w24_cure_v2_capture import CaptureError, verify_public_capture

        if not isinstance(action_ref, Mapping):
            raise CampaignError("V2 capture chain contains a non-object public action reference")
        action = action_ref.get("action")
        source_relative = action_ref.get("source_path")
        response_record = action_ref.get("response_evidence")
        binding_before = action_ref.get("binding_before")
        if (not isinstance(action, str) or not isinstance(source_relative, str) or
                not isinstance(response_record, Mapping) or not isinstance(binding_before, Mapping)):
            raise CampaignError("V2 public action reference lacks exact source, response, or binding data")
        source_path = _project_path(self.workspace, self.workspace / source_relative, must_exist=True)
        source_hash = action_ref.get("source_sha256")
        pinned_by_name = {
            self.fixture.name: self.fixture_source_hashes["science"],
            self.v2_control_fixture.name: self.fixture_source_hashes["v2_control"],
            self.coupon_fixture.name: self.fixture_source_hashes["coupon"],
        }
        if (source_path.name not in pinned_by_name or source_hash != pinned_by_name[source_path.name] or
                sha256(source_path) != source_hash):
            raise CampaignError("V2 public action source changed from its exact candidate manifest")
        response_path = Path(str(response_record.get("path", "")))
        expected_response_hash = response_record.get("sha256")
        if (not response_path.is_file() or response_path.is_symlink() or
                not isinstance(expected_response_hash, str) or sha256(response_path) != expected_response_hash or
                response_record.get("size_bytes") != response_path.stat().st_size):
            raise CampaignError("V2 original public response evidence is missing or hash-mismatched")
        response = _read_json(response_path, f"public {action} response")
        ref_id = action_ref.get("public_identity")
        if not isinstance(ref_id, Mapping) or not isinstance(ref_id.get("operation_id"), str):
            raise CampaignError("V2 original public response reference omits its OperationStore identity")
        expected_model_ref = binding_before.get("model_ref")
        if not isinstance(expected_model_ref, Mapping):
            raise CampaignError("V2 public action binding lacks the exact ModelRef")
        try:
            verified = verify_public_capture(
                self.daemon, response, operation_id=ref_id["operation_id"],
                project_root=self.workspace, source_artifact_path=source_path,
                expected_source_sha256=source_hash, expected_action=action,
                expected_project_id=self.project_id,
                expected_session_id=str(binding_before.get("session_id")),
                expected_model_ref=expected_model_ref,
                expected_revision=binding_before.get("revision"))
        except CaptureError as exc:
            raise CampaignError(f"V2 stored {action} response no longer matches the original private OperationStore: {exc}") from exc
        identity_keys = ("request_id", "operation_id", "idempotency_key", "request_hash",
                         "job_id", "project_id", "session_id", "model_ref",
                         "revision_before", "revision_after", "source_path", "source_sha256")
        observed = {key: verified.get(key) for key in identity_keys}
        if observed != dict(ref_id):
            raise CampaignError("V2 public action identity differs from its frozen capture reference chain")
        if (action_ref.get("status") != verified.get("status") or
                action_ref.get("artifact") != verified.get("artifact") or
                action_ref.get("artifact_receipt") != verified.get("artifact_receipt") or
                action_ref.get("capture_validation") != verified.get("capture_validation") or
                action_ref.get("readback_data") != verified.get("readback_data")):
            raise CampaignError("V2 public action reference differs from its authenticated original response and artifact")
        if action == "study_run":
            validation = verified.get("capture_validation", {})
            save_request_path = validation.get("save_request_path") if isinstance(validation, Mapping) else None
            save_provenance = action_ref.get("saved_artifact_provenance")
            if save_request_path is None:
                if save_provenance is not None or verified.get("artifact") is not None:
                    raise CampaignError("Study.run save provenance exists without an original save request")
            else:
                expected_save_provenance = {
                    "status": "STUDY_RUN_SAVED_ARTIFACT_PROVENANCE_AUTHENTICATED",
                    "save_request_path": save_request_path,
                    "artifact": verified.get("artifact"),
                    "artifact_receipt": verified.get("artifact_receipt"),
                    "producer_public_identity": dict(ref_id),
                    "model_binding_after_solve": action_ref.get("binding_after"),
                }
                if save_provenance != expected_save_provenance:
                    raise CampaignError("Study.run save receipt is not bound to its exact operation, request, and ModelRef revision")
        return verified

    def _validate_staged_baseline_save(self, producer_ref: Mapping[str, Any], *,
                                       saved_summary: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Reauthenticate the terminal Study.run's requested save and current MPH bytes."""
        if (producer_ref.get("action") != "study_run" or
                not isinstance(producer_ref.get("public_identity"), Mapping) or
                producer_ref["public_identity"].get("project_id") != self.project_id):
            raise CampaignError("staged baseline saved artifact lacks its exact project-bound Study.run producer")
        verified = self._reauthenticate_public_java_action(producer_ref)
        validation = verified.get("capture_validation")
        artifact = verified.get("artifact")
        receipt = verified.get("artifact_receipt")
        provenance = producer_ref.get("saved_artifact_provenance")
        readback = verified.get("readback_data")
        if (not isinstance(readback, Mapping) or
                readback.get("case_id") != "staged_baseline" or
                readback.get("study_tag") != "stdCool" or
                readback.get("study_run_calls_from_this_action") != 1):
            raise CampaignError("saved MPH producer is not the exact terminal staged-baseline stdCool Study.run")
        if (not isinstance(validation, Mapping) or
                validation.get("status") != "PUBLIC_STUDY_RUN_SAVE_RECEIPT_MATCHED_REQUEST_AND_BYTES" or
                not isinstance(artifact, Mapping) or not isinstance(receipt, Mapping) or
                not isinstance(provenance, Mapping) or
                provenance.get("save_request_path") != validation.get("save_request_path") or
                provenance.get("artifact") != artifact or
                provenance.get("artifact_receipt") != receipt or
                provenance.get("producer_public_identity") != producer_ref.get("public_identity") or
                provenance.get("model_binding_after_solve") != producer_ref.get("binding_after")):
            raise CampaignError("terminal stdCool Study.run has no authenticated immediate-save byte receipt")
        if saved_summary is not None:
            if (saved_summary.get("path") != artifact.get("path") or
                    saved_summary.get("size_bytes") != artifact.get("size_bytes") or
                    saved_summary.get("sha256") != artifact.get("sha256") or
                    saved_summary.get("save_receipt") != provenance or
                    saved_summary.get("producer_study_run") != producer_ref or
                    saved_summary.get("model_binding_after_solve") != producer_ref.get("binding_after")):
                raise CampaignError("staged baseline save summary differs from its exact terminal Study.run bytes and binding")
        return {"artifact": dict(artifact), "save_provenance": dict(provenance),
                "verified_producer": verified}

    def _ensure_v2_contract_readback(self, slot: SolveSlot, binding: ManagedModelBinding,
                                     *, timeout_s: float,
                                     state_key: str | None = None) -> tuple[ManagedModelBinding, dict[str, Any] | None]:
        if not self.cure_law_v2_enabled:
            return binding, None
        from tools.w24_cure_v2_capture import CaptureError, validate_cure_law_v2_contract_readback

        updated, response, direct_readback = self._fixture_action(
            binding, "cure_v2_contract_readback", {"phase": "readback_v2"},
            timeout_s=timeout_s, source_fixture=self.coupon_fixture,
            entrypoint="W24CureCouponFixture#run")
        reference = self._record_public_java_action(
            action="cure_v2_contract_readback", response=response,
            binding_before=binding, binding_after=updated,
            source_fixture=self.coupon_fixture, response_dir=self.evidence / "v2_model_readbacks",
            response_label=f"{slot.case_id}_{slot.study_tag}_contract")
        verified = self._reauthenticate_public_java_action(reference)
        readback = verified.get("readback_data")
        if direct_readback != readback:
            raise CampaignError("actual v2 setup readback differs between Worker result and durable public response")
        try:
            validation = validate_cure_law_v2_contract_readback(readback)
        except CaptureError as exc:
            raise CampaignError(f"loaded {slot.case_id}/{slot.study_tag} model failed the declared v2 contract: {exc}") from exc
        if (reference.get("binding_after") != updated.as_record() or
                validation.get("cure_law_version") != "W24_CURE_LAW_V2"):
            raise CampaignError("loaded model v2 readback is not bound to this exact current ModelRef")
        contract = {
            "status": "V2_CONTRACT_AUTHENTICATED_FOR_CURRENT_MODEL_REF",
            "case_id": slot.case_id, "study_tag": slot.study_tag,
            "setup_readback": self.slot_native_setup_readbacks.get(state_key or f"{slot.case_id}:{slot.study_tag}"),
            "reference": reference,
            "contract_validation": validation,
            "native_activation_semantics": "UNVERIFIED",
            "maxwell_branch_reference_state": "UNVERIFIED",
        }
        self.v2_contract_readbacks[state_key or f"{slot.case_id}:{slot.study_tag}"] = contract
        return updated, contract

    def _reauthenticate_public_model_load(self, receipt: Mapping[str, Any]) -> dict[str, Any]:
        from tools.w24_cure_v2_capture import CaptureError, verify_public_model_load

        response_record = receipt.get("response_evidence")
        binding_record = receipt.get("binding")
        if not isinstance(response_record, Mapping) or not isinstance(binding_record, Mapping):
            raise CampaignError("v2 loaded model lacks its durable public model_load response or exact binding")
        response_path = Path(str(response_record.get("path", "")))
        expected_response_hash = response_record.get("sha256")
        if (not response_path.is_file() or response_path.is_symlink() or
                not isinstance(expected_response_hash, str) or sha256(response_path) != expected_response_hash or
                response_record.get("size_bytes") != response_path.stat().st_size):
            raise CampaignError("v2 model_load response evidence is missing or hash-mismatched")
        response = _read_json(response_path, "v2 model_load response")
        path = Path(str(receipt.get("input_path", "")))
        input_hash = receipt.get("input_sha256")
        ref = binding_record.get("model_ref")
        try:
            verified = verify_public_model_load(
                self.daemon, response, project_root=self.workspace, requested_path=path,
                expected_file_sha256=input_hash, expected_project_id=self.project_id,
                expected_session_id=str(binding_record.get("session_id")),
                expected_model_ref=ref, expected_revision=binding_record.get("revision"))
        except CaptureError as exc:
            raise CampaignError(f"loaded v2 model failed private OperationStore authentication: {exc}") from exc
        identity = receipt.get("public_identity")
        if not isinstance(identity, Mapping):
            raise CampaignError("v2 model_load reference omitted its durable public identity")
        identity_keys = ("request_id", "operation_id", "idempotency_key", "request_hash",
                         "job_id", "project_id", "session_id", "model_ref", "revision",
                         "path", "sha256")
        if {key: verified.get(key) for key in identity_keys} != dict(identity):
            raise CampaignError("v2 model_load identity differs from its immutable capture reference")
        return verified

    def _record_public_model_load(self, *, name: str, path: Path,
                                  binding: ManagedModelBinding,
                                  response: Mapping[str, Any]) -> dict[str, Any]:
        from tools.w24_cure_v2_capture import CaptureError, verify_public_model_load

        try:
            verified = verify_public_model_load(
                self.daemon, response, project_root=self.workspace, requested_path=path,
                expected_file_sha256=sha256(path), expected_project_id=self.project_id,
                expected_session_id=binding.session_id,
                expected_model_ref=binding.model_ref, expected_revision=binding.revision)
        except CaptureError as exc:
            raise CampaignError(f"v2 {name} load did not authenticate through the exact public model_load route: {exc}") from exc
        operation_id = verified.get("operation_id")
        safe_operation_id = re.sub(r"[^A-Za-z0-9_-]", "_", str(operation_id))
        response_dir = self.evidence / "v2_model_load_responses"
        response_dir.mkdir(parents=True, exist_ok=True)
        response_path = response_dir / f"{name}_worker{self.worker_sessions}_{safe_operation_id}.json"
        if response_path.exists() or response_path.is_symlink():
            raise CampaignError("model_load response evidence path already exists; no overwrite is allowed")
        _write_json_fsynced(response_path, dict(response))
        identity_keys = ("request_id", "operation_id", "idempotency_key", "request_hash",
                         "job_id", "project_id", "session_id", "model_ref", "revision",
                         "path", "sha256")
        return {
            "status": verified["status"],
            "public_identity": {key: verified.get(key) for key in identity_keys},
            "response_evidence": {"path": str(response_path),
                                  "size_bytes": response_path.stat().st_size,
                                  "sha256": sha256(response_path)},
            "native_acceptance": "NOT_RUN",
        }

    def _authenticated_v2_capture_frames(self, capture: Mapping[str, Any], *,
                                         expected_case: str,
                                         expected_study: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Re-read the saved public response chain and return its real artifacts."""
        from tools.w24_cure_v2_capture import CaptureError
        from tools.w24_maxwell_branch_state import (
            MaxwellStateError, compare_persisted_full_xmesh_state,
            v2_physics_configuration_sha256,
        )

        report = capture.get("v2_capture")
        if (capture.get("cure_law_capture_mode") != "V2_PUBLIC_AUTHENTICATED" or
                not isinstance(report, Mapping) or
                report.get("status") not in {
                    "V2_PUBLIC_CAPTURE_CHAIN_VERIFIED_NATIVE_REVIEW_REQUIRED",
                    "V2_REOPEN_PUBLIC_CAPTURE_CHAIN_VERIFIED_NATIVE_REVIEW_REQUIRED",
                } or report.get("native_acceptance") != "NOT_RUN"):
            raise CampaignError("explicit v2 capture chain is missing, incomplete, or mislabeled")
        if report.get("case_id") != expected_case or report.get("study_tag") != expected_study:
            raise CampaignError("v2 capture report case/study identity differs from the requested comparison slot")

        contract = report.get("model_contract")
        contract_ref = contract.get("reference") if isinstance(contract, Mapping) else None
        setup_ref = report.get("setup_readback")
        study_ref = report.get("study_run")
        capture_status = report.get("status")
        equation_view_row = report.get("equation_view_capture")
        equation_view_ref = equation_view_row.get("operation") if isinstance(equation_view_row, Mapping) else None
        snapshot_row = report.get("solution_snapshot")
        history_row = report.get("history_capture")
        snapshot_ref = snapshot_row.get("operation") if isinstance(snapshot_row, Mapping) else None
        history_ref = history_row.get("operation") if isinstance(history_row, Mapping) else None
        if not all(isinstance(value, Mapping) for value in
                   (setup_ref, contract_ref, equation_view_ref, snapshot_ref, history_ref)):
            raise CampaignError("v2 capture chain omitted a setup, contract, Equation View, snapshot, or history operation reference")
        if capture_status == "V2_PUBLIC_CAPTURE_CHAIN_VERIFIED_NATIVE_REVIEW_REQUIRED" and not isinstance(study_ref, Mapping):
            raise CampaignError("solved v2 capture chain omitted its exact Study.run public operation reference")
        if capture_status == "V2_REOPEN_PUBLIC_CAPTURE_CHAIN_VERIFIED_NATIVE_REVIEW_REQUIRED" and study_ref is not None:
            raise CampaignError("fresh-Worker v2 reopen must reuse the original solve provenance without another Study.run")
        setup_verified = self._reauthenticate_public_java_action(setup_ref)
        contract_verified = self._reauthenticate_public_java_action(contract_ref)
        equation_view_verified = self._reauthenticate_public_java_action(equation_view_ref)
        snapshot_verified = self._reauthenticate_public_java_action(snapshot_ref)
        history_verified = self._reauthenticate_public_java_action(history_ref)
        readback = setup_verified.get("readback_data", {})
        contract_readback = contract_verified.get("readback_data")
        if not isinstance(contract_readback, Mapping):
            raise CampaignError("v2 authenticated contract response omitted its exact physics configuration readback")
        configuration_sha256 = v2_physics_configuration_sha256(contract_readback)
        if (contract_verified.get("capture_validation", {}).get("cure_law_version") != "W24_CURE_LAW_V2" or
                readback.get("status") != "SCIENCE_ACTIONS_READY_NOT_SOLVED" or
                readback.get("quasistatic_readback") != "Quasistatic" or
                self._check_solver_readback(
                    readback,
                    SolveSlot(expected_case, expected_study, "authenticated capture verification"),
                    max_step_s=0.5 if expected_case == "tight_time" else 1.0) != report.get("solver_tag")):
            raise CampaignError("v2 current-model setup or cure-law readback no longer authenticates the exact slot")
        lineage_study_ref: Mapping[str, Any] | None = study_ref
        reopened_source_frames: list[dict[str, Any]] | None = None
        source_staged_lineages: list[dict[str, Any]] | None = None
        lineage_binding_start: Mapping[str, Any] = setup_ref.get("binding_before", {})
        if capture_status == "V2_PUBLIC_CAPTURE_CHAIN_VERIFIED_NATIVE_REVIEW_REQUIRED":
            study_verified = self._reauthenticate_public_java_action(study_ref)
            if (study_verified.get("readback_data", {}).get("case_id") != expected_case or
                    study_verified.get("readback_data", {}).get("study_tag") != expected_study or
                    study_verified.get("readback_data", {}).get("study_run_calls_from_this_action") != 1 or
                    study_verified.get("readback_data", {}).get("solver_sequence") != report.get("solver_tag")):
                raise CampaignError("v2 Study.run response no longer authenticates the exact solved slot")
            if (setup_ref.get("binding_after") != contract_ref.get("binding_before") or
                    contract_ref.get("binding_after") != study_ref.get("binding_before") or
                    study_ref.get("binding_after") != equation_view_ref.get("binding_before") or
                    equation_view_ref.get("binding_after") != snapshot_ref.get("binding_before")):
                raise CampaignError("v2 setup→contract→Study.run→Equation View→snapshot revision chain is discontinuous")
            if expected_case == "staged_baseline" and expected_study == "stdCool":
                self._validate_staged_baseline_save(
                    study_ref, saved_summary=self.staged_baseline_saved_model)
        elif capture_status == "V2_REOPEN_PUBLIC_CAPTURE_CHAIN_VERIFIED_NATIVE_REVIEW_REQUIRED":
            origin_v2 = report.get("reopened_from")
            if not isinstance(origin_v2, Mapping):
                raise CampaignError("reopened v2 capture omitted its original solved public capture")
            origin_capture = {"cure_law_capture_mode": "V2_PUBLIC_AUTHENTICATED",
                              "v2_capture": origin_v2}
            reopened_source_frames, old_lineage = self._authenticated_v2_capture_frames(
                origin_capture, expected_case=expected_case, expected_study=expected_study)
            model_load = report.get("model_load")
            if not isinstance(model_load, Mapping):
                raise CampaignError("reopened v2 capture omitted its saved MPH→Worker2 load reference")
            load_ref = model_load.get("current_worker_model_load")
            if not isinstance(load_ref, Mapping):
                raise CampaignError("reopened v2 capture omitted the exact public model_load response reference")
            loaded = self._reauthenticate_public_model_load(load_ref)
            saved = model_load.get("saved_model")
            lineage_study_ref = origin_v2.get("study_run")
            saved_producer = saved.get("producer_study_run") if isinstance(saved, Mapping) else None
            source_staged_captures = model_load.get("source_staged_captures")
            if not isinstance(source_staged_captures, Mapping):
                raise CampaignError("reopened v2 capture omitted its complete authenticated source stage chain")
            _source_frames, source_staged_lineages = self._authenticated_staged_v2_schedule(
                source_staged_captures)
            expected_source = source_staged_captures.get(f"staged_baseline:{expected_study}")
            terminal_source = source_staged_captures.get("staged_baseline:stdCool")
            terminal_report = terminal_source.get("v2_capture") if isinstance(terminal_source, Mapping) else None
            source_terminal_study_ref = (terminal_report.get("study_run")
                                         if isinstance(terminal_report, Mapping) else None)
            source_terminal_equation_row = (terminal_report.get("equation_view_capture")
                                            if isinstance(terminal_report, Mapping) else None)
            source_terminal_equation_ref = (
                source_terminal_equation_row.get("operation")
                if isinstance(source_terminal_equation_row, Mapping) else None)
            if isinstance(source_terminal_study_ref, Mapping):
                producer_save_link = self._validate_staged_baseline_save(
                    source_terminal_study_ref, saved_summary=self.staged_baseline_saved_model)
                producer_verified = producer_save_link["verified_producer"]
            else:
                producer_verified = {}
            terminal_snapshot = (terminal_report.get("solution_snapshot", {}).get("operation")
                                 if isinstance(terminal_report, Mapping) else None)
            if (old_lineage.get("source_identity_authenticated") is not True or
                    old_lineage.get("case_id") != expected_case or
                    old_lineage.get("study_tag") != expected_study or
                    old_lineage.get("solver_tag") != report.get("solver_tag") or
                    not isinstance(lineage_study_ref, Mapping) or
                    model_load.get("status") != "SAVED_MPH_TO_CURRENT_WORKER_MODEL_LOAD_AUTHENTICATED" or
                    not isinstance(saved, Mapping) or
                    not isinstance(saved_producer, Mapping) or
                    not isinstance(expected_source, Mapping) or
                    expected_source.get("v2_capture") != origin_v2 or
                    not isinstance(source_terminal_study_ref, Mapping) or
                    saved_producer != source_terminal_study_ref or
                    saved.get("model_binding_after_solve") != source_terminal_study_ref.get("binding_after") or
                    saved.get("save_receipt") != producer_save_link.get("save_provenance") or
                    saved.get("path") != producer_save_link.get("artifact", {}).get("path") or
                    saved.get("size_bytes") != producer_save_link.get("artifact", {}).get("size_bytes") or
                    saved.get("sha256") != producer_save_link.get("artifact", {}).get("sha256") or
                    not isinstance(source_terminal_equation_ref, Mapping) or
                    not isinstance(terminal_snapshot, Mapping) or
                    source_terminal_study_ref.get("binding_after") != source_terminal_equation_ref.get("binding_before") or
                    source_terminal_equation_ref.get("binding_after") != terminal_snapshot.get("binding_before") or
                    producer_verified.get("readback_data", {}).get("case_id") != expected_case or
                    producer_verified.get("readback_data", {}).get("study_tag") != "stdCool" or
                    producer_verified.get("readback_data", {}).get("study_run_calls_from_this_action") != 1 or
                    loaded.get("path") != saved.get("path") or
                    loaded.get("sha256") != saved.get("sha256") or
                    setup_ref.get("binding_after") != contract_ref.get("binding_before") or
                    contract_ref.get("binding_after") != equation_view_ref.get("binding_before") or
                    equation_view_ref.get("binding_after") != snapshot_ref.get("binding_before")):
                raise CampaignError("reopened v2 saved model, terminal staged source chain, Worker load, and Equation View capture lineage is discontinuous")
            self._validate_worker2_reopen_prefix(
                expected_case=expected_case, expected_study=expected_study,
                prior_captures=model_load.get("worker2_prior_captures"),
                current_start=setup_ref.get("binding_before"), current_load=load_ref,
                saved_model=saved, source_staged_captures=source_staged_captures)
            lineage_binding_start = setup_ref.get("binding_before", {})
        else:
            raise CampaignError("v2 capture chain has an unknown capture mode")
        if (snapshot_ref.get("binding_after") != history_ref.get("binding_before") or
                report.get("model_binding_after_history") != history_ref.get("binding_after")):
            raise CampaignError("v2 snapshot→history revision chain is discontinuous")

        def verify_evidence(row: Any, ref: Mapping[str, Any], verified: Mapping[str, Any], label: str) -> Path:
            if not isinstance(row, Mapping):
                raise CampaignError(f"v2 {label} receipt omitted its evidence copy")
            evidence_record = row.get("evidence")
            artifact_receipt = row.get("artifact_receipt")
            public_receipt = verified.get("artifact_receipt")
            public_artifact = verified.get("artifact")
            if (not isinstance(evidence_record, Mapping) or
                    not isinstance(artifact_receipt, Mapping) or
                    artifact_receipt != public_receipt or
                    not isinstance(public_artifact, Mapping)):
                raise CampaignError(f"v2 {label} evidence is not bound to the authenticated public artifact receipt")
            path = Path(str(evidence_record.get("path", "")))
            digest = evidence_record.get("sha256")
            size = evidence_record.get("size_bytes")
            if (not path.is_file() or path.is_symlink() or
                    not isinstance(digest, str) or sha256(path) != digest or
                    size != path.stat().st_size or
                    digest != public_artifact.get("sha256") or
                    size != public_artifact.get("size_bytes")):
                raise CampaignError(f"v2 {label} evidence copy differs from the authenticated source artifact bytes")
            return path

        equation_view_path = verify_evidence(
            equation_view_row, equation_view_ref, equation_view_verified, "Equation View raw tables")
        equation_view_artifact = equation_view_verified.get("artifact_data")
        if not isinstance(equation_view_artifact, Mapping):
            raise CampaignError("authenticated Equation View response omitted its parsed raw source artifact")
        snapshot_path = verify_evidence(snapshot_row, snapshot_ref, snapshot_verified, "Xmesh snapshot")
        history_path = verify_evidence(history_row, history_ref, history_verified, "history capture")
        snapshot_frames = list(iter_solution_snapshot(snapshot_path))
        history_artifact = history_verified.get("artifact_data")
        if not isinstance(history_artifact, Mapping):
            raise CampaignError("authenticated v2 history response omitted its Java-produced data artifact")
        times = [float(frame["time_s"]) for frame in snapshot_frames]
        validation = snapshot_verified.get("capture_validation")
        if (not snapshot_frames or not isinstance(validation, Mapping) or
                validation.get("snapshot_schema") not in {
                    "W24-DOF-SNAPSHOT-2", "W24-DOF-SNAPSHOT-3"} or
                validation.get("stored_times_s") != times or
                history_artifact.get("stored_times_s") != times or
                history_artifact.get("study_tag") != expected_study or
                history_artifact.get("solver_tag") != report.get("solver_tag") or
                history_artifact.get("dataset_solution_readback") != report.get("solver_tag") or
                history_artifact.get("dataset_type_requested") != "Solution"):
            raise CampaignError("authenticated v2 snapshot and history disagree on solver, solution dataset, or stored times")
        equation_view_validation = equation_view_verified.get("capture_validation")
        if (not isinstance(equation_view_validation, Mapping) or
                equation_view_validation.get("status") != "RAW_EQUATION_VIEW_TABLES_AUTHENTICATED_NATIVE_SEMANTICS_UNVERIFIED" or
                equation_view_artifact.get("study_tag") != expected_study or
                equation_view_artifact.get("solver_tag") != report.get("solver_tag") or
                equation_view_validation.get("stored_times_s") != times):
            raise CampaignError("Equation View table capture is not bound to the exact native study/solver/stored-time series")
        snapshot_schema = validation.get("snapshot_schema")
        if capture_status == "V2_REOPEN_PUBLIC_CAPTURE_CHAIN_VERIFIED_NATIVE_REVIEW_REQUIRED":
            if (old_lineage.get("study_tag") != expected_study or
                    old_lineage.get("solver_tag") != report.get("solver_tag")):
                raise CampaignError("saved/reopened exact study or solver sequence identity differs")
        if snapshot_schema == "W24-DOF-SNAPSHOT-3":
            state_metadata = snapshot_frames[0]["dofs"]
            if (validation.get("complete_internal_dof_capture") is not True or
                    state_metadata.get("complete_xmesh_internal_dof_capture") is not True):
                raise CampaignError("authenticated V3 capture does not cover all mapped internal Xmesh solution entries")
            from tools.w24_cure_v2_capture import associate_equation_view_with_xmesh_v1

            observed_association = associate_equation_view_with_xmesh_v1(
                equation_view_artifact, state_metadata)
            if (not isinstance(equation_view_row, Mapping) or
                    equation_view_row.get("association") != observed_association or
                    observed_association.get("layout_sha256") != state_metadata.get("layout_sha256")):
                raise CampaignError("Equation View-to-Xmesh exact-cell diagnostics differ from the current authenticated layout")
        elif (not isinstance(equation_view_row, Mapping) or
              equation_view_row.get("association", {}).get("status") !=
              "UNVERIFIED_LEGACY_SNAPSHOT_HAS_NO_XMESH_LAYOUT"):
            raise CampaignError("legacy snapshot is not explicitly marked as lacking V3 Equation View association")
        persistence_comparison: dict[str, Any] | None = None
        if capture_status == "V2_REOPEN_PUBLIC_CAPTURE_CHAIN_VERIFIED_NATIVE_REVIEW_REQUIRED":
            if reopened_source_frames is None:
                raise CampaignError("reopened v2 capture lost the exact original source stored vectors")
            source_schema = reopened_source_frames[0]["dofs"].get("snapshot_schema")
            if source_schema == snapshot_schema == "W24-DOF-SNAPSHOT-3":
                try:
                    persistence_comparison = compare_persisted_full_xmesh_state(
                        reopened_source_frames, snapshot_frames,
                        source_configuration_sha256=old_lineage.get("configuration_sha256"),
                        reopened_configuration_sha256=configuration_sha256)
                except MaxwellStateError as exc:
                    raise CampaignError(f"saved/reopened full-Xmesh state comparison failed: {exc}") from exc
            else:
                persistence_comparison = {
                    "status": "UNVERIFIED_LEGACY_V2_SNAPSHOT_HAS_NO_FULL_XMESH_LAYOUT_HASH",
                    "native_acceptance": "NOT_RUN",
                    "maxwell_branch_field_identity": "UNVERIFIED_NOT_INFERRED_FROM_FIELD_NAME",
                }
        if report.get("snapshot_stored_times_s") != times:
            raise CampaignError("v2 capture summary stored times differ from the authenticated native snapshot")
        frames: list[dict[str, Any]] = []
        for frame in snapshot_frames:
            row = dict(frame)
            row["history_capture"] = dict(history_artifact)
            frames.append(row)

        v2_end_binding = history_ref.get("binding_after")
        capture_end_binding = capture.get("model_binding")
        if not isinstance(capture_end_binding, Mapping):
            capture_end_binding = report.get("model_binding_after_history")
        post_v2_operations = capture.get("post_v2_operations")
        if post_v2_operations is None:
            if capture_end_binding != v2_end_binding:
                raise CampaignError("v2 staged capture advanced after history without retained public operation references")
        else:
            if not isinstance(post_v2_operations, list) or len(post_v2_operations) != 2:
                raise CampaignError("v2 staged capture must retain its ordered solution and metrics post-capture operations")
            expected_actions = ("solution_snapshot", "cure_metrics_capture")
            current_binding = v2_end_binding
            for operation, expected_action in zip(post_v2_operations, expected_actions):
                if not isinstance(operation, Mapping) or operation.get("action") != expected_action:
                    raise CampaignError("v2 staged post-capture operation order is invalid")
                operation_verified = self._reauthenticate_public_java_action(operation)
                readback = operation_verified.get("readback_data", {})
                if (operation.get("binding_before") != current_binding or
                        readback.get("solver_tag") != report.get("solver_tag")):
                    raise CampaignError("v2 staged post-capture operation differs from its exact preceding revision or solver")
                if expected_action == "solution_snapshot":
                    if (readback.get("status") != "SOLUTION_SNAPSHOT_WRITTEN" or
                            readback.get("real_solution") is not True):
                        raise CampaignError("v2 trailing solution snapshot readback is incomplete")
                elif readback.get("status") != "NATIVE_CURE_METRICS_CAPTURED":
                    raise CampaignError("v2 trailing cure metrics readback is incomplete")
                current_binding = operation.get("binding_after")
            if capture_end_binding != current_binding:
                raise CampaignError("v2 staged capture final binding differs from its authenticated post-capture operations")

        def _binding_epoch_key(value: Any, label: str) -> tuple[Any, ...]:
            if (not isinstance(value, Mapping) or value.get("project_id") != self.project_id or
                    not isinstance(value.get("session_id"), str) or not value.get("session_id") or
                    not isinstance(value.get("model_ref"), Mapping) or
                    isinstance(value.get("revision"), bool) or not isinstance(value.get("revision"), int) or
                    value.get("revision") < 0):
                raise CampaignError(f"v2 {label} lacks its exact model binding identity")
            model_ref = value["model_ref"]
            if (model_ref.get("session_id") != value.get("session_id") or
                    not isinstance(model_ref.get("server_instance_id"), str) or
                    not model_ref.get("server_instance_id") or
                    isinstance(model_ref.get("generation"), bool) or
                    not isinstance(model_ref.get("generation"), int) or model_ref.get("generation") < 1):
                raise CampaignError(f"v2 {label} lacks its exact Worker epoch")
            return (value.get("project_id"), value.get("session_id"),
                    json.dumps(dict(model_ref), sort_keys=True, separators=(",", ":")))

        start_revision = lineage_binding_start.get("revision") if isinstance(lineage_binding_start, Mapping) else None
        end_revision = capture_end_binding.get("revision") if isinstance(capture_end_binding, Mapping) else None
        if (_binding_epoch_key(lineage_binding_start, "capture start") !=
                _binding_epoch_key(v2_end_binding, "history end") or
                _binding_epoch_key(capture_end_binding, "capture end") !=
                _binding_epoch_key(v2_end_binding, "history end") or
                isinstance(start_revision, bool) or not isinstance(start_revision, int) or
                isinstance(end_revision, bool) or not isinstance(end_revision, int) or
                start_revision >= end_revision):
            raise CampaignError("v2 staged capture changes Worker epoch or has a non-increasing revision chain")
        lineage_refs = [contract_ref, equation_view_ref, snapshot_ref, history_ref]
        if isinstance(lineage_study_ref, Mapping):
            lineage_refs.insert(1, lineage_study_ref)
        if capture_status == "V2_REOPEN_PUBLIC_CAPTURE_CHAIN_VERIFIED_NATIVE_REVIEW_REQUIRED":
            lineage_refs.insert(0, report["model_load"]["current_worker_model_load"])
        lineage = {
            "status": ("V3_FULL_XMESH_CAPTURE_CHAIN_REAUTHENTICATED"
                       if snapshot_schema == "W24-DOF-SNAPSHOT-3" else
                       "V2_PUBLIC_CAPTURE_CHAIN_REAUTHENTICATED"),
            "project_id": self.project_id,
            "model_ref": dict(snapshot_ref["binding_before"]["model_ref"]),
            "binding_before": dict(lineage_binding_start),
            "binding_after": dict(capture_end_binding),
            "case_id": expected_case, "study_tag": expected_study,
            "solver_tag": report.get("solver_tag"),
            "full_dof_absolute_tolerances": dict(
                contract_verified.get("capture_validation", {}).get("full_dof_absolute_tolerances", {})),
            "configuration_sha256": configuration_sha256,
            "snapshot_schema": snapshot_schema,
            "layout_sha256": (snapshot_frames[0]["dofs"].get("layout_sha256")
                              if snapshot_schema == "W24-DOF-SNAPSHOT-3" else None),
            "full_xmesh_internal_dof_capture": (
                "COMPLETE_MAPPING_CAPTURED_NATIVE_BRANCH_IDENTITY_UNVERIFIED"
                if snapshot_schema == "W24-DOF-SNAPSHOT-3" else "LEGACY_V2_NO_FIELD_LAYOUT_CAPTURE"),
            "persisted_state_comparison": persistence_comparison,
            "equation_view_artifact_sha256": equation_view_verified.get("artifact", {}).get("sha256"),
            "equation_view_association": dict(equation_view_row.get("association", {})),
            "maxwell_branch_field_identity": "UNVERIFIED_NOT_INFERRED_FROM_FIELD_NAME",
            "maxwell_branch_reference_state": "UNVERIFIED",
            "operation_ids": [ref["public_identity"]["operation_id"]
                              for ref in lineage_refs if isinstance(ref.get("public_identity"), Mapping)],
            "job_ids": [ref["public_identity"]["job_id"]
                        for ref in lineage_refs if isinstance(ref.get("public_identity"), Mapping)],
            "source_hashes": [ref["source_sha256"] for ref in lineage_refs
                              if isinstance(ref.get("source_sha256"), str)],
            "stored_times_s": times,
            "source_staged_lineages": ([dict(row) for row in source_staged_lineages]
                                        if source_staged_lineages is not None else []),
            "snapshot_sha256": sha256(snapshot_path),
            "history_sha256": sha256(history_path),
            "source_identity_authenticated": True,
            "maxwell_branch_reference_state": "UNVERIFIED_NO_PUBLIC_REFERENCE_STATE_CAPTURE",
            "native_acceptance": "NOT_RUN",
        }
        authentication_token = uuid4().hex
        lineage["capture_authentication_token"] = authentication_token
        registry = getattr(self, "_authenticated_v2_frame_receipts", None)
        if not isinstance(registry, dict):
            registry = {}
            self._authenticated_v2_frame_receipts = registry
        lineage_identity_fields = (
            "project_id", "model_ref", "binding_before", "binding_after",
            "case_id", "study_tag", "solver_tag", "configuration_sha256",
            "snapshot_schema", "layout_sha256", "operation_ids", "job_ids",
            "source_hashes", "stored_times_s", "snapshot_sha256",
            "equation_view_artifact_sha256", "equation_view_association",
        )
        frame_hash_memo: dict[int, str] = {}
        registry[authentication_token] = {
            # Store a detached content digest for the entire returned lineage,
            # including nested bindings, operation IDs, tolerances, and status
            # fields. A shallow copy of selected keys aliases mutable children
            # and lets caller edits rewrite the apparent receipt in place.
            "lineage_sha256": _capture_value_sha256(lineage),
            **{key: lineage.get(key) for key in lineage_identity_fields},
            "frame_receipts": [
                {"time_s": float(frame["time_s"]),
                 "content_sha256": _authenticated_capture_frame_sha256(frame, frame_hash_memo)}
                for frame in frames
            ],
        }
        return frames, lineage

    def _load_registered_copy(self, name: str, path: Path, *, timeout_s: float) -> ManagedModelBinding:
        from tools.run_native_resume_smoke import _dispatch

        if self.project_id != self._immutable_project_id:
            raise CampaignError("managed model_load project identity changed")
        scoped_path = _project_path(self.workspace, path, must_exist=True)
        response = self._journaled_rpc(
            "model_load",
            lambda: _dispatch(self.daemon, "model_load", {"path": str(scoped_path)},
                              project_id=self.project_id, rpc_timeout_s=timeout_s,
                              idempotency_key=f"w24-model-load-{uuid4()}",
                              request_id=f"w24-model-load-request-{uuid4()}"),
            worker_required=True)
        self._log_response(f"model_load.{name}", response)
        binding = ManagedModelBinding.from_load_response(response, project_id=self.project_id)
        epoch = self._validate_current_worker_binding(binding, name=name)
        persisted = self.daemon.backend.model_project_binding(dict(binding.model_ref))
        if (not isinstance(persisted, Mapping) or persisted.get("attribution") != "PROJECT_BOUND" or
                persisted.get("project_id") != self.project_id):
            raise CampaignError(f"managed model_load for {name} did not persist project attribution")
        receipt = {"model_name": name, "input_path": str(scoped_path),
                   "input_sha256": sha256(scoped_path), "binding": binding.as_record(),
                   "worker_model_ref_epoch": list(epoch),
                   "persisted_project_binding": dict(persisted), "response": response}
        if self.cure_law_v2_enabled and name != "mechanics_parent":
            receipt["public_load_reference"] = self._record_public_model_load(
                name=name, path=scoped_path, binding=binding, response=response)
        receipt_key = name
        if receipt_key in self.model_load_receipts and self.worker_sessions > 1:
            receipt_key = f"{name}_worker{self.worker_sessions}"
        self.model_load_receipts[receipt_key] = receipt
        self.models[name] = binding
        return binding

    def prepare_campaign(self, stress_components: Mapping[str, Mapping[str, str]],
                         *, timeout_s: float) -> Mapping[str, Any]:
        if set(stress_components) != set(STRESS_ROLES):
            raise CampaignError("prepare_campaign requires the exact four mapped native stress roles")
        if {key: dict(value) for key, value in stress_components.items()} != self.stress_components:
            raise CampaignError("prepare_campaign stress map differs from the frozen native mapping")
        loaded: dict[str, ManagedModelBinding] = {}
        for name in ("staged_baseline", "continuous_comparator", "reset_negative_control",
                     "coarse_mesh", "tight_time", "dose_sensitivity", "mechanics_parent"):
            loaded[name] = self._load_registered_copy(name, self.input_paths[name], timeout_s=timeout_s)

        parent = loaded["mechanics_parent"]
        for case_id in ("mechanics_free_expansion", "mechanics_fully_fixed"):
            inventory_path = self.workspace / f"{case_id}_equation_view_inventory.json"
            inventory_path = _project_path(self.workspace, inventory_path, must_exist=False)
            parent, _, build = self._fixture_action(parent, "mechanics_build", {
                "case_id": case_id, "equation_view_path": str(inventory_path)},
                timeout_s=timeout_s)
            if (build.get("status") != "MECHANICS_BENCHMARK_BUILT_NOT_SOLVED" or
                    build.get("case_id") != case_id or
                    not isinstance(build.get("model_tag"), str) or
                    not isinstance(build.get("solver_sequence"), str) or
                    build.get("study_run_calls") != 0):
                raise CampaignError(f"native mechanics model build readback is incomplete for {case_id}")
            mechanics_inventory = build.get("equation_view_inventory")
            actual_map = map_stress_components(mechanics_inventory)
            for role in STRESS_ROLES:
                for field in ("expression", "unit", "description"):
                    if actual_map[role].get(field) != self.stress_components[role].get(field):
                        raise CampaignError(f"mechanics model Equation View changed mapped {role}.{field}")
            adopt_response = self._journaled_rpc("model.adopt", lambda: self.daemon.dispatch({
                "operation": "model.adopt",
                "arguments": {"server_model_tag": build["model_tag"]},
                "execution": {"project_id": self.project_id, "session_id": parent.session_id,
                              "idempotency_key": f"w24-mechanics-adopt-{uuid4()}",
                              "request_id": f"w24-mechanics-adopt-request-{uuid4()}",
                              "rpc_timeout_s": min(timeout_s, 120.0), "queue_timeout_s": 60.0,
                              "execution_timeout_s": None},
            }), worker_required=False)
            self._log_response(f"model.adopt.{case_id}", adopt_response)
            binding = ManagedModelBinding.from_adopt_response(
                adopt_response, project_id=self.project_id, expected_model_tag=build["model_tag"])
            persisted = self.daemon.backend.model_project_binding(dict(binding.model_ref))
            if (not isinstance(persisted, Mapping) or persisted.get("attribution") != "PROJECT_BOUND" or
                    persisted.get("project_id") != self.project_id):
                raise CampaignError(f"canonical model.adopt did not persist {case_id} project attribution")
            identity = self._managed_model_inspect(binding, timeout_s=min(timeout_s, 120.0))
            structure = identity["data"].get("structure")
            studies = structure.get("studies") if isinstance(structure, Mapping) else None
            if not isinstance(studies, list) or "stdMech" not in studies:
                raise CampaignError(f"adopted {case_id} model.inspect lacks the native stdMech study")
            build_record = {**dict(build), "binding": binding.as_record(),
                            "persisted_project_binding": dict(persisted),
                            "managed_native_identity_readback": identity}
            self.mechanics_builds[case_id] = build_record
            self.mechanics_models[case_id] = binding

        bindings = {case_id: self.mechanics_models[case_id].as_record()
                    for case_id in sorted(self.mechanics_models)}
        if set(bindings) != MechanicsModelBindings.CASES:
            raise CampaignError("both distinct mechanics models were not created and adopted")
        if len({row["model_tag"] for row in bindings.values()}) != 2 or len({
                json.dumps(row["model_ref"], sort_keys=True) for row in bindings.values()}) != 2:
            raise CampaignError("mechanics models do not have independent native tags and ModelRefs")
        return {"status": "NATIVE_CAMPAIGN_PREPARED",
                "project_id": self.project_id, "project_workspace": str(self.workspace),
                "model_loads": dict(self.model_load_receipts),
                "mechanics_models": dict(self.mechanics_builds),
                "mechanics_model_bindings": bindings,
                "stress_components": {role: dict(value) for role, value in self.stress_components.items()},
                "worker_session_count": self.worker_sessions,
                "native_study_run_calls": 0}

    def _binding_for_slot(self, slot: SolveSlot) -> ManagedModelBinding:
        if slot.case_id in MechanicsModelBindings.CASES:
            try:
                return self.mechanics_models[slot.case_id]
            except KeyError as exc:
                raise CampaignError(f"mechanics native model was not prepared for {slot.case_id}") from exc
        model_name = {
            "staged_baseline": "staged_baseline",
            "continuous_comparator": "continuous_comparator",
            "reset_negative_control": "reset_negative_control",
            "coarse_mesh": "coarse_mesh", "tight_time": "tight_time",
            "dose_sensitivity": "dose_sensitivity",
        }.get(slot.case_id)
        if model_name is None or model_name not in self.models:
            raise CampaignError(f"slot has no separately loaded project-bound source model: {slot.case_id}")
        return self.models[model_name]

    def _check_solver_readback(self, readback: Mapping[str, Any], slot: SolveSlot,
                               *, max_step_s: float) -> str:
        solvers = readback.get("solver_readbacks")
        if not isinstance(solvers, Mapping) or slot.study_tag not in solvers:
            raise CampaignError(f"native solver tree lacks study {slot.study_tag}")
        row = solvers[slot.study_tag]
        if (not isinstance(row, Mapping) or row.get("sequence_attached") is not True or
                row.get("sequence_study") != slot.study_tag or row.get("timemethod") != "bdf" or
                row.get("tstepsbdf") != "strict" or row.get("tout") != "tsteps" or
                row.get("tstepsstore") != 1 or
                not math.isclose(float(row.get("maxstepbdf", math.nan)), max_step_s,
                                 rel_tol=0.0, abs_tol=1e-12)):
            raise CampaignError(f"native solver BDF/output readbacks differ from the frozen settings for {slot.study_tag}")
        tag = row.get("sequence_tag")
        if not isinstance(tag, str) or not tag:
            raise CampaignError(f"native {slot.study_tag} solver sequence tag is missing")
        return tag

    def _check_study_readback(self, readback: Mapping[str, Any], slot: SolveSlot,
                              *, max_step_s: float) -> dict[str, Any]:
        rows = readback.get("study_time_readbacks")
        if not isinstance(rows, Mapping):
            raise CampaignError("native study readback omitted time-step/transfer properties")
        expected_lists = {
            "stdUV": "range(0[s],1[s],120[s])",
            "stdBake": "range(120[s],10[s],960[s])",
            "stdCool": "range(960[s],10[s],1500[s])",
        }
        expected_sources = {"stdUV": "", "stdBake": "stdUV", "stdCool": "stdBake"}
        for tag, expected_tlist in expected_lists.items():
            row = rows.get(tag)
            if not isinstance(row, Mapping) or row.get("tlist") != expected_tlist:
                raise CampaignError(f"native {tag} requested output-time list differs from the frozen schedule")
            expected_use = "off" if tag == "stdUV" else "on"
            if tag == "stdBake" and slot.case_id == "reset_negative_control":
                expected_use = "off"
            if row.get("useinitsol") != expected_use:
                raise CampaignError(f"native {tag} initial-solution transfer readback differs from this control")
            source = expected_sources[tag]
            if expected_use == "on" and (
                    row.get("initmethod") != "sol" or row.get("initstudy") != source or
                    row.get("solnum") != "last"):
                raise CampaignError(f"native {tag} does not use the exact preceding study's last solution")
        expected_continuous = (
            "range(0[s],0.5[s],120[s]) range(130[s],10[s],1500[s])"
            if max_step_s == 0.5 else
            "range(0[s],1[s],120[s]) range(130[s],10[s],1500[s])"
        )
        if slot.study_tag == "stdCont":
            row = rows.get("stdCont")
            if (not isinstance(row, Mapping) or row.get("tlist") != expected_continuous or
                    row.get("useinitsol") != "off"):
                raise CampaignError("native continuous study time schedule/initial-state readback differs")
        return {tag: dict(value) for tag, value in rows.items() if isinstance(value, Mapping)}

    def configure_slot(self, slot: SolveSlot, *, timeout_s: float) -> Mapping[str, Any]:
        binding = self._binding_for_slot(slot)
        if slot.case_id in MechanicsModelBindings.CASES:
            identity = self._managed_model_inspect(binding, timeout_s=timeout_s)
            current = binding
            build = self.mechanics_builds[slot.case_id]
            solver_tag = build.get("solver_sequence")
            if not isinstance(solver_tag, str) or not solver_tag:
                raise CampaignError("mechanics build receipt omitted stdMech SolverSequence")
            self.slot_solver_tags[(slot.case_id, slot.study_tag)] = solver_tag
            return {"status": "NATIVE_SLOT_CONFIGURED", "model_binding": current.as_record(),
                    "native_identity_readback": identity,
                    "solver_sequence": solver_tag,
                    "configuration": build}

        if slot.case_id == "continuous_comparator":
            binding, _, config = self._fixture_action(binding, "configure_continuous",
                                                       {"max_step_s": 1.0}, timeout_s=timeout_s)
        elif slot.case_id == "reset_negative_control":
            binding, _, config = self._fixture_action(binding, "configure_scenario",
                                                       {"scenario": "reset_negative_control"},
                                                       timeout_s=timeout_s)
        elif slot.case_id == "coarse_mesh":
            binding, _, config = self._fixture_action(binding, "configure_scenario",
                                                       {"scenario": "coarse_mesh",
                                                        "adhesive_hmax_m": 20e-6},
                                                       timeout_s=timeout_s)
            binding, _, continuous = self._fixture_action(binding, "configure_continuous",
                                                           {"max_step_s": 1.0}, timeout_s=timeout_s)
            config = {"scenario_configuration": config, "continuous_configuration": continuous}
        elif slot.case_id == "tight_time":
            binding, _, config = self._fixture_action(binding, "configure_continuous",
                                                       {"max_step_s": 0.5}, timeout_s=timeout_s)
        elif slot.case_id == "dose_sensitivity":
            binding, _, config = self._fixture_action(binding, "configure_scenario",
                                                       {"scenario": "dose_sensitivity"},
                                                       timeout_s=timeout_s)
            binding, _, continuous = self._fixture_action(binding, "configure_continuous",
                                                           {"max_step_s": 1.0}, timeout_s=timeout_s)
            config = {"scenario_configuration": config, "continuous_configuration": continuous}
        else:
            config = {"scenario": "frozen_staged_baseline"}
        if slot.case_id == "staged_baseline":
            self.models["staged_baseline"] = binding
        else:
            self.models[slot.case_id] = binding
        readback_before = binding
        binding, readback_response, readback = self._fixture_action(
            binding, "readback", {}, timeout_s=timeout_s)
        max_step = 0.5 if slot.case_id == "tight_time" else 1.0
        solver_tag = self._check_solver_readback(readback, slot, max_step_s=max_step)
        study_configuration = self._check_study_readback(readback, slot, max_step_s=max_step)
        setup_readback_key = f"{slot.case_id}:{slot.study_tag}"
        if self.cure_law_v2_enabled:
            readback_ref = self._record_public_java_action(
                action="readback", response=readback_response,
                binding_before=readback_before, binding_after=binding,
                source_fixture=self.fixture, response_dir=self.evidence / "v2_setup_readbacks",
                response_label=f"{slot.case_id}_{slot.study_tag}_setup")
            readback_verified = self._reauthenticate_public_java_action(readback_ref)
            if (readback_verified.get("readback_data") != readback or
                    self._check_solver_readback(readback_verified["readback_data"], slot,
                                                max_step_s=max_step) != solver_tag or
                    self._check_study_readback(readback_verified["readback_data"], slot,
                                              max_step_s=max_step) != study_configuration):
                raise CampaignError("authenticated v2 setup response differs from the exact solver/study configuration readback")
            self.slot_native_setup_readbacks[setup_readback_key] = readback_ref
        binding, v2_contract = self._ensure_v2_contract_readback(
            slot, binding, timeout_s=min(timeout_s, 180.0))
        if slot.case_id == "staged_baseline":
            self.models["staged_baseline"] = binding
        else:
            self.models[slot.case_id] = binding
        if slot.case_id == "staged_baseline" and slot.study_tag == "stdCool":
            self.staged_baseline_configuration_readback = {
                "study_time_readbacks": study_configuration,
                "solver_readbacks": dict(readback.get("solver_readbacks", {})),
                "mesh_tags": list(readback.get("mesh_tags", [])),
                "quasistatic_readback": readback.get("quasistatic_readback"),
            }
        self.slot_solver_tags[(slot.case_id, slot.study_tag)] = solver_tag
        return {"status": "NATIVE_SLOT_CONFIGURED", "configuration": config,
                "native_readback": readback,
                "cure_law_capture_mode": ("V2_EXPLICIT_MODEL_READBACK_PASS" if v2_contract else
                                           "V1_LEGACY_PATH_NOT_V2_ACCEPTANCE"),
                "cure_law_v2_contract": v2_contract,
                "study_time_readbacks": study_configuration,
                "model_binding": binding.as_record(),
                "solver_sequence": solver_tag}

    def run_study(self, slot: SolveSlot, *, ledger_path: Path,
                  save_after_success_path: Path | None,
                  timeout_s: float) -> Mapping[str, Any]:
        binding = self._binding_for_slot(slot)
        if self.project_ledger is None:
            supplied_ledger = Path(ledger_path)
            candidate_ledger = (supplied_ledger if supplied_ledger.is_absolute()
                                else self.workspace / supplied_ledger)
            ledger_already_created = candidate_ledger.exists()
            ledger = _project_path(self.workspace, ledger_path,
                                   must_exist=ledger_already_created)
            if ledger_already_created and read_solve_ledger(ledger):
                raise CampaignError("a new W24 science campaign must begin with an empty native solve ledger")
            self.project_ledger = ledger
        else:
            # The first Study.run creates the durable ledger; later planned
            # submissions append to that same regular file.  Revalidate it as
            # an existing project file instead of treating every call as a new
            # output (which would make multi-study campaigns impossible).
            ledger = _project_path(self.workspace, ledger_path, must_exist=True)
            if ledger != self.project_ledger:
                raise CampaignError("native solve controller changed the single frozen project ledger path")
        if slot.case_id in MechanicsModelBindings.CASES:
            identity = self._managed_model_inspect(binding, timeout_s=min(timeout_s, 120.0))
            if identity["model_binding"]["model_tag"] != binding.model_tag:
                raise CampaignError("mechanics identity changed immediately before study.run")
            action = "mechanics_study_run"
            arguments = {"case_id": slot.case_id, "ledger_path": str(ledger)}
            requested_save_path = None
        else:
            action = "study_run"
            arguments = {"case_id": slot.case_id, "study_tag": slot.study_tag,
                         "ledger_path": str(ledger)}
            requested_save_path = None
            if save_after_success_path is not None:
                save_path = _project_path(self.workspace, save_after_success_path, must_exist=False)
                arguments["save_after_success_path"] = str(save_path)
                requested_save_path = str(save_path)
        binding_before = binding
        updated, response, result = self._fixture_action(binding, action, arguments, timeout_s=timeout_s)
        if result.get("status") != "NATIVE_STUDY_RUN_RETURNED" or result.get("study_run_calls_from_this_action") != 1:
            raise CampaignError("native Java study action did not return its exact terminal one-call receipt")
        if (result.get("case_id") != slot.case_id or result.get("study_tag") != slot.study_tag or
                result.get("submission_index") != len(read_solve_ledger(ledger))):
            raise CampaignError("native Java study receipt differs from its durable solve ledger slot")
        solver_tag = result.get("solver_sequence")
        if not isinstance(solver_tag, str) or solver_tag != self.slot_solver_tags.get((slot.case_id, slot.study_tag)):
            raise CampaignError("native Java study receipt changed its frozen SolverSequence tag")
        java_save_receipt = result.get("immediate_save_receipt")
        if requested_save_path is None:
            if (result.get("immediate_save_path") is not None or java_save_receipt is not None):
                raise CampaignError("Study.run returned save evidence without an exact save_after_success_path request")
        else:
            if (result.get("immediate_save_path") != requested_save_path or
                    not isinstance(java_save_receipt, Mapping) or
                    java_save_receipt.get("status") != "STUDY_RUN_MPH_SAVED_AND_HASHED" or
                    java_save_receipt.get("path") != requested_save_path):
                raise CampaignError("Study.run did not return the exact requested immediate-save byte receipt")
            saved_path = _project_path(self.workspace, requested_save_path, must_exist=True)
            actual_size = saved_path.stat().st_size
            actual_hash = sha256(saved_path)
            if (actual_size <= 0 or java_save_receipt.get("size_bytes") != actual_size or
                    java_save_receipt.get("sha256") != actual_hash):
                raise CampaignError("Worker Study.run save receipt differs from the immediate saved MPH bytes")
        result = {**dict(result), "model_binding_after_solve": updated.as_record()}
        if self.cure_law_v2_enabled and slot.case_id not in MechanicsModelBindings.CASES:
            action_ref = self._record_public_java_action(
                action="study_run", response=response, binding_before=binding_before,
                binding_after=updated, source_fixture=self.fixture,
                response_dir=self.evidence / "v2_study_run_responses",
                response_label=f"{slot.case_id}_{slot.study_tag}_solve")
            if requested_save_path is not None:
                artifact = action_ref.get("artifact")
                artifact_receipt = action_ref.get("artifact_receipt")
                validation = action_ref.get("capture_validation")
                if (not isinstance(artifact, Mapping) or
                        not isinstance(artifact_receipt, Mapping) or
                        not isinstance(validation, Mapping) or
                        validation.get("save_request_path") != requested_save_path or
                        artifact.get("path") != requested_save_path or
                        artifact.get("size_bytes") != actual_size or
                        artifact.get("sha256") != actual_hash):
                    raise CampaignError("authenticated Study.run operation does not carry the exact immediate-save receipt")
                action_ref["saved_artifact_provenance"] = {
                    "status": "STUDY_RUN_SAVED_ARTIFACT_PROVENANCE_AUTHENTICATED",
                    "save_request_path": requested_save_path,
                    "artifact": dict(artifact),
                    "artifact_receipt": dict(artifact_receipt),
                    "producer_public_identity": dict(action_ref["public_identity"]),
                    "model_binding_after_solve": updated.as_record(),
                }
                self._reauthenticate_public_java_action(action_ref)
            self.slot_study_run_actions[f"{slot.case_id}:{slot.study_tag}"] = action_ref
            result["public_operation_identity"] = action_ref["public_identity"]
        if slot.case_id not in MechanicsModelBindings.CASES:
            self.models[slot.case_id] = updated
        if requested_save_path is not None:
            saved = _project_path(self.workspace, requested_save_path, must_exist=True)
            if self.cure_law_v2_enabled and slot == SolveSlot(
                    "staged_baseline", "stdCool", SOLVE_PLAN[4].purpose):
                producer = self.slot_study_run_actions.get(f"{slot.case_id}:{slot.study_tag}")
                if not isinstance(producer, Mapping):
                    raise CampaignError("v2 staged-baseline save has no authenticated stdCool Study.run producer")
                save_link = self._validate_staged_baseline_save(producer)
                if save_link["artifact"] != {
                        "path": str(saved), "size_bytes": saved.stat().st_size, "sha256": sha256(saved)}:
                    raise CampaignError("staged baseline summary cannot substitute different saved MPH bytes")
                self.staged_baseline_saved_model = {
                    **dict(save_link["artifact"]), "case_id": slot.case_id,
                    "study_tag": slot.study_tag, "producer_study_run": dict(producer),
                    "save_receipt": dict(save_link["save_provenance"]),
                    "model_binding_after_solve": updated.as_record(),
                    "native_acceptance": "NOT_RUN",
                }
        self.slot_solver_tags[(slot.case_id, slot.study_tag)] = solver_tag
        return result

    def _capture_v2_pair(self, slot: SolveSlot, binding: ManagedModelBinding,
                         output_dir: Path, base: Path, token: str, solver_tag: str,
                         *, timeout_s: float,
                         reopened_origin: Mapping[str, Any] | None = None,
                         reopened_model_load: Mapping[str, Any] | None = None
                         ) -> tuple[ManagedModelBinding, dict[str, Any]]:
        from tools.w24_cure_v2_capture import CaptureError
        from tools.w24_maxwell_branch_state import v2_physics_configuration_sha256

        slot_key = f"{slot.case_id}:{slot.study_tag}"
        state_key = (f"{slot_key}:worker{self.worker_sessions}"
                     if reopened_origin is not None else slot_key)
        contract = self.v2_contract_readbacks.get(state_key)
        setup_readback_ref = self.slot_native_setup_readbacks.get(state_key)
        solve_action = (self.slot_study_run_actions.get(slot_key)
                        if reopened_origin is None else None)
        if not isinstance(contract, Mapping) or not isinstance(setup_readback_ref, Mapping):
            raise CampaignError("declared v2 slot lacks its exact current-model public setup and cure-law readbacks")
        contract_ref = contract.get("reference")
        if not isinstance(contract_ref, Mapping):
            raise CampaignError("declared v2 slot contract is not bound to a public response reference")
        capture_start_binding = binding.as_record()
        setup_verified = self._reauthenticate_public_java_action(setup_readback_ref)
        contract_verified = self._reauthenticate_public_java_action(contract_ref)
        setup_data = setup_verified.get("readback_data")
        contract_data = contract_verified.get("readback_data")
        if not isinstance(contract_data, Mapping):
            raise CampaignError("v2 contract response omitted its exact cure/Activation/Maxwell readback")
        configuration_sha256 = v2_physics_configuration_sha256(contract_data)
        if (not isinstance(setup_data, Mapping) or
                setup_readback_ref.get("binding_after") != contract_ref.get("binding_before") or
                contract_verified.get("capture_validation", {}).get("cure_law_version") != "W24_CURE_LAW_V2" or
                slot.study_tag not in setup_data.get("studies", []) or
                setup_data.get("quasistatic_readback") != "Quasistatic" or
                self._check_solver_readback(setup_data, slot,
                    max_step_s=0.5 if slot.case_id == "tight_time" else 1.0) != solver_tag):
            raise CampaignError("authenticated current-model setup/cure readbacks do not bind to this exact model revision")

        origin_report: Mapping[str, Any] | None = None
        model_load_link: dict[str, Any] | None = None
        if reopened_origin is None:
            if not isinstance(solve_action, Mapping):
                raise CampaignError("declared v2 slot lacks its exact successful Study.run public response")
            solve_verified = self._reauthenticate_public_java_action(solve_action)
            if (contract_ref.get("binding_after") != solve_action.get("binding_before") or
                    solve_action.get("binding_after") != binding.as_record() or
                    solve_verified.get("readback_data", {}).get("case_id") != slot.case_id or
                    solve_verified.get("readback_data", {}).get("study_tag") != slot.study_tag or
                    solve_verified.get("readback_data", {}).get("solver_sequence") != solver_tag):
                raise CampaignError("v2 contract readback, actual Study.run, and current ModelRef revisions do not form one chain")
            if slot.case_id == "staged_baseline" and slot.study_tag == "stdCool":
                self._validate_staged_baseline_save(
                    solve_action, saved_summary=self.staged_baseline_saved_model)
        else:
            origin_v2 = reopened_origin.get("v2_capture")
            if not isinstance(origin_v2, Mapping):
                raise CampaignError("fresh-Worker v2 capture lacks the original solved capture provenance")
            _origin_frames, origin_lineage = self._authenticated_v2_capture_frames(
                reopened_origin, expected_case=slot.case_id, expected_study=slot.study_tag)
            origin_report = dict(origin_v2)
            origin_equation = origin_v2.get("equation_view_capture")
            origin_equation_ref = (origin_equation.get("operation")
                                   if isinstance(origin_equation, Mapping) else None)
            origin_snapshot = origin_v2.get("solution_snapshot")
            origin_snapshot_ref = (origin_snapshot.get("operation")
                                   if isinstance(origin_snapshot, Mapping) else None)
            if (origin_lineage.get("source_identity_authenticated") is not True or
                    not isinstance(origin_v2.get("study_run"), Mapping) or
                    not isinstance(origin_equation_ref, Mapping) or
                    not isinstance(origin_snapshot_ref, Mapping) or
                    origin_v2.get("study_run", {}).get("binding_after") !=
                    origin_equation_ref.get("binding_before") or
                    origin_equation_ref.get("binding_after") !=
                    origin_snapshot_ref.get("binding_before")):
                raise CampaignError("original Worker solved capture does not retain its authenticated lineage")
            if not isinstance(reopened_model_load, Mapping) or self.staged_baseline_saved_model is None:
                raise CampaignError("fresh-Worker v2 capture lacks its exact saved-model producer and model_load receipt")
            source_staged_captures = reopened_origin.get("staged_source_captures")
            if not isinstance(source_staged_captures, Mapping):
                raise CampaignError("fresh-Worker v2 capture lacks the complete original staged source chain")
            _source_frames, source_staged_lineages = self._authenticated_staged_v2_schedule(
                source_staged_captures)
            expected_source = source_staged_captures.get(f"staged_baseline:{slot.study_tag}")
            terminal_source = source_staged_captures.get("staged_baseline:stdCool")
            terminal_report = terminal_source.get("v2_capture") if isinstance(terminal_source, Mapping) else None
            source_terminal_study_ref = (terminal_report.get("study_run")
                                         if isinstance(terminal_report, Mapping) else None)
            source_terminal_equation_row = (terminal_report.get("equation_view_capture")
                                            if isinstance(terminal_report, Mapping) else None)
            source_terminal_equation_ref = (
                source_terminal_equation_row.get("operation")
                if isinstance(source_terminal_equation_row, Mapping) else None)
            terminal_snapshot = (terminal_report.get("solution_snapshot", {}).get("operation")
                                 if isinstance(terminal_report, Mapping) else None)
            if (not isinstance(expected_source, Mapping) or
                    expected_source.get("v2_capture") != origin_v2 or
                    not isinstance(source_terminal_study_ref, Mapping) or
                    not isinstance(source_terminal_equation_ref, Mapping) or
                    not isinstance(terminal_snapshot, Mapping)):
                raise CampaignError("Worker2 stage origin or terminal solve is not in the full authenticated source chain")
            saved_source = self.staged_baseline_saved_model
            save_link = self._validate_staged_baseline_save(
                source_terminal_study_ref, saved_summary=saved_source)
            saved_artifact = save_link["artifact"]
            saved_path = Path(str(saved_artifact.get("path", "")))
            saved_hash = saved_source.get("sha256")
            if (not saved_path.is_file() or saved_path.is_symlink() or
                    sha256(saved_path) != saved_hash or
                    reopened_model_load.get("input_path") != str(saved_path) or
                    reopened_model_load.get("input_sha256") != saved_hash):
                raise CampaignError("fresh Worker did not load the exact saved staged-baseline bytes")
            if (saved_source.get("case_id") != slot.case_id or
                    saved_source.get("study_tag") != "stdCool" or
                    saved_source.get("producer_study_run") != source_terminal_study_ref or
                    saved_source.get("model_binding_after_solve") !=
                    source_terminal_study_ref.get("binding_after") or
                    source_terminal_study_ref.get("binding_after") !=
                    source_terminal_equation_ref.get("binding_before") or
                    source_terminal_equation_ref.get("binding_after") !=
                    terminal_snapshot.get("binding_before")):
                raise CampaignError("saved MPH is not bound to the authenticated terminal stdCool solve revision")
            load_ref = reopened_model_load.get("public_load_reference")
            if not isinstance(load_ref, Mapping):
                raise CampaignError("fresh Worker model_load response lacks its project-bound public operation evidence")
            load_verified = self._reauthenticate_public_model_load(reopened_model_load)
            if (load_verified.get("sha256") != saved_hash or
                    load_verified.get("path") != str(saved_path)):
                raise CampaignError("saved model load response differs from its terminal staged MPH bytes")
            source_producer = source_terminal_study_ref
            producer_verified = save_link["verified_producer"]
            if (producer_verified.get("readback_data", {}).get("case_id") != slot.case_id or
                    producer_verified.get("readback_data", {}).get("study_tag") != "stdCool" or
                    producer_verified.get("readback_data", {}).get("study_run_calls_from_this_action") != 1):
                raise CampaignError("saved staged baseline lacks its authenticated terminal stdCool Study.run producer")
            saved_model_link = {
                "path": str(saved_path), "size_bytes": saved_path.stat().st_size,
                "sha256": saved_hash, "producer_study_run": dict(source_producer),
                "save_receipt": dict(save_link["save_provenance"]),
                "model_binding_after_solve": dict(saved_source["model_binding_after_solve"]),
            }
            current_load_link = {
                **{key: reopened_model_load.get(key) for key in
                   ("model_name", "input_path", "input_sha256", "binding")},
                **dict(load_ref),
            }
            prior_worker2_captures = reopened_origin.get("worker2_prior_captures")
            self._validate_worker2_reopen_prefix(
                expected_case=slot.case_id, expected_study=slot.study_tag,
                prior_captures=prior_worker2_captures,
                current_start=setup_readback_ref.get("binding_before"),
                current_load=current_load_link, saved_model=saved_model_link,
                source_staged_captures=source_staged_captures)
            model_load_link = {
                "status": "SAVED_MPH_TO_CURRENT_WORKER_MODEL_LOAD_AUTHENTICATED",
                "saved_model": saved_model_link,
                "current_worker_model_load": current_load_link,
                "source_staged_captures": {
                    str(key): dict(value) for key, value in source_staged_captures.items()
                    if isinstance(value, Mapping)
                },
                "worker2_prior_captures": {
                    str(key): dict(value) for key, value in prior_worker2_captures.items()
                    if isinstance(value, Mapping)
                },
                "native_acceptance": "NOT_RUN",
            }

        equation_view_path = _project_path(self.workspace,
            base.with_name(token + "_equation_view_v1.json"), must_exist=False)
        equation_view_before = binding
        binding, equation_view_response, equation_view_readback = self._fixture_action(
            binding, "equation_view_readback_v1", {
                "phase": "equation_view_readback_v1", "study_tag": slot.study_tag,
                "solver_tag": solver_tag, "path": str(equation_view_path)},
            timeout_s=timeout_s, source_fixture=self.coupon_fixture,
            entrypoint="W24CureCouponFixture#run")
        equation_view_ref = self._record_public_java_action(
            action="equation_view_readback_v1", response=equation_view_response,
            binding_before=equation_view_before, binding_after=binding,
            source_fixture=self.coupon_fixture, response_dir=self.evidence / "v2_capture_responses",
            response_label=f"{slot.case_id}_{slot.study_tag}_equation_view")
        equation_view_verified = self._reauthenticate_public_java_action(equation_view_ref)
        if equation_view_readback != equation_view_verified.get("artifact_receipt"):
            raise CampaignError("Equation View Worker response differs from its durable OperationStore receipt")
        equation_view_artifact = equation_view_verified.get("artifact_data")
        if not isinstance(equation_view_artifact, Mapping):
            raise CampaignError("authenticated Equation View response omitted its verified raw artifact")
        equation_view_evidence = _evidence_copy(
            equation_view_path, output_dir / "equation_view_raw_v1.json",
            status="PUBLIC_EQUATION_VIEW_RAW_TABLES_NATIVE_SEMANTICS_UNVERIFIED")

        field_path = _project_path(self.workspace,
            base.with_name(token + "_v3_dofs.gz"), must_exist=False)
        snapshot_before = binding
        binding, snapshot_response, snapshot_readback = self._fixture_action(
            binding, "solution_snapshot_v3",
            {"study_tag": slot.study_tag, "solver_tag": solver_tag, "path": str(field_path)},
            timeout_s=timeout_s, source_fixture=self.v2_control_fixture,
            entrypoint="W24CureLawV2ControlFixture#run")
        snapshot_ref = self._record_public_java_action(
            action="solution_snapshot_v3", response=snapshot_response,
            binding_before=snapshot_before, binding_after=binding,
            source_fixture=self.v2_control_fixture, response_dir=self.evidence / "v2_capture_responses",
            response_label=f"{slot.case_id}_{slot.study_tag}_snapshot")
        snapshot_verified = self._reauthenticate_public_java_action(snapshot_ref)
        if snapshot_readback != snapshot_verified.get("artifact_receipt"):
            raise CampaignError("V3 full-Xmesh Worker response differs from its durable OperationStore receipt")
        snapshot_evidence = _evidence_copy(field_path, output_dir / "v3_full_xmesh_snapshot.gz",
                                          status="V3_PUBLIC_FULL_XMESH_SNAPSHOT_NATIVE_REVIEW_REQUIRED")
        snapshot_frames = list(iter_solution_snapshot(Path(snapshot_evidence["path"])))
        snapshot_times = [float(frame["time_s"]) for frame in snapshot_frames]
        snapshot_validation = snapshot_verified.get("capture_validation")
        if (not isinstance(snapshot_validation, Mapping) or
                snapshot_validation.get("snapshot_schema") != "W24-DOF-SNAPSHOT-3" or
                snapshot_validation.get("stored_times_s") != snapshot_times or
                snapshot_validation.get("complete_internal_dof_capture") is not True or
                not snapshot_frames):
            raise CampaignError("verified V3 snapshot raw frames differ from its public stored-time or mapping receipt")
        dof_names = set(snapshot_frames[0]["dofs"].get("dofNames", []))
        required_dofs = {"comp1_T", "comp1_alpha", "comp1_Duv_rel", "comp1_qpost"}
        axes = snapshot_frames[0]["dofs"].get("coordinate_axes")
        required_dofs |= ({"comp1_u", "comp1_w"} if axes == 2 else
                          {"comp1_u", "comp1_v", "comp1_w"} if axes == 3 else set())
        if not required_dofs.issubset(dof_names):
            raise CampaignError("actual V2 complete Xmesh snapshot omits cure-law or displacement field members")
        if (equation_view_ref.get("binding_after") != snapshot_ref.get("binding_before") or
                equation_view_verified.get("capture_validation", {}).get("stored_times_s") != snapshot_times or
                equation_view_artifact.get("study_tag") != slot.study_tag or
                equation_view_artifact.get("solver_tag") != solver_tag):
            raise CampaignError("Equation View and V3 Xmesh captures are discontinuous in model revision, study, solver, or stored times")
        if snapshot_validation.get("snapshot_schema") == "W24-DOF-SNAPSHOT-3":
            from tools.w24_cure_v2_capture import associate_equation_view_with_xmesh_v1

            equation_view_association = associate_equation_view_with_xmesh_v1(
                equation_view_artifact, snapshot_frames[0]["dofs"])
            if equation_view_association.get("layout_sha256") != snapshot_frames[0]["dofs"].get("layout_sha256"):
                raise CampaignError("Equation View association is not bound to the exact V3 Xmesh layout hash")
        else:
            equation_view_association = {
                "schema": "W24_EQUATION_VIEW_XMESH_EXACT_CELL_ASSOCIATION_V1",
                "status": "UNVERIFIED_LEGACY_SNAPSHOT_HAS_NO_XMESH_LAYOUT",
                "maxwell_branch_field_identity": "UNVERIFIED_NOT_INFERRED_FROM_FIELD_NAME",
                "maxwell_branch_reference_state": "UNVERIFIED", "native_acceptance": "NOT_RUN",
            }

        history_path = _project_path(self.workspace,
            base.with_name(token + "_v2_history.json"), must_exist=False)
        history_before = binding
        binding, history_response, history_readback = self._fixture_action(
            binding, "history_capture_v2",
            {"study_tag": slot.study_tag, "solver_tag": solver_tag, "path": str(history_path)},
            timeout_s=timeout_s, source_fixture=self.fixture,
            entrypoint="W24CureScienceFixture#run")
        history_ref = self._record_public_java_action(
            action="history_capture_v2", response=history_response,
            binding_before=history_before, binding_after=binding,
            source_fixture=self.fixture, response_dir=self.evidence / "v2_capture_responses",
            response_label=f"{slot.case_id}_{slot.study_tag}_history")
        history_verified = self._reauthenticate_public_java_action(history_ref)
        if history_readback != history_verified.get("artifact_receipt"):
            raise CampaignError("V2 history Worker response differs from its durable OperationStore receipt")
        history_artifact = history_verified.get("artifact_data")
        if not isinstance(history_artifact, Mapping):
            raise CampaignError("authenticated V2 history route omitted the verified Java-produced artifact")
        history_evidence = _evidence_copy(history_path, output_dir / "v2_history_capture.json",
                                          status="V2_PUBLIC_HISTORY_CAPTURE_NATIVE_REVIEW_REQUIRED")
        if (snapshot_ref.get("binding_after") != history_ref.get("binding_before") or
                snapshot_times != history_artifact.get("stored_times_s") or
                history_artifact.get("study_tag") != slot.study_tag or
                history_artifact.get("solver_tag") != solver_tag or
                history_artifact.get("dataset_solution_readback") != solver_tag or
                snapshot_readback.get("study_tag") != slot.study_tag or
                snapshot_readback.get("solver_tag") != solver_tag):
            raise CampaignError("v2 snapshot/history artifacts do not share their exact study, solver, times, and revision chain")
        if snapshot_ref.get("binding_after") != history_ref.get("binding_before"):
            raise CampaignError("v2 snapshot→history ModelRef/revision transition is discontinuous")
        if reopened_origin is None and (
                solve_action.get("binding_after") != equation_view_ref.get("binding_before") or
                equation_view_ref.get("binding_after") != snapshot_ref.get("binding_before")):
            raise CampaignError("v2 Study.run→Equation View→snapshot ModelRef/revision transition is discontinuous")
        if reopened_origin is not None and (
                setup_readback_ref.get("binding_after") != contract_ref.get("binding_before") or
                contract_ref.get("binding_after") != capture_start_binding or
                equation_view_ref.get("binding_before") != capture_start_binding or
                equation_view_ref.get("binding_after") != snapshot_ref.get("binding_before")):
            raise CampaignError("v2 Worker2 prior-stage→setup→contract→Equation View→snapshot revision chain is discontinuous")
        report = {
            "status": ("V2_PUBLIC_CAPTURE_CHAIN_VERIFIED_NATIVE_REVIEW_REQUIRED" if reopened_origin is None else
                       "V2_REOPEN_PUBLIC_CAPTURE_CHAIN_VERIFIED_NATIVE_REVIEW_REQUIRED"),
            "case_id": slot.case_id, "study_tag": slot.study_tag,
            "solver_tag": solver_tag,
            "model_contract": contract,
            "configuration_sha256": configuration_sha256,
            "setup_readback": setup_readback_ref,
            "study_run": solve_action if reopened_origin is None else None,
            "reopened_from": origin_report,
            "model_load": model_load_link,
            "equation_view_capture": {
                "operation": equation_view_ref,
                "evidence": equation_view_evidence,
                "artifact_receipt": dict(equation_view_readback),
                "capture_validation": equation_view_verified.get("capture_validation"),
                "association": equation_view_association,
                "maxwell_branch_field_identity": "UNVERIFIED_NOT_INFERRED_FROM_FIELD_NAME",
                "maxwell_branch_reference_state": "UNVERIFIED",
            },
            "solution_snapshot": {
                "operation": snapshot_ref,
                "evidence": snapshot_evidence,
                "artifact_receipt": dict(snapshot_readback),
                "capture_validation": dict(snapshot_validation),
            },
            "history_capture": {
                "operation": history_ref,
                "evidence": history_evidence,
                "artifact_receipt": dict(history_readback),
                "capture_validation": history_verified.get("capture_validation"),
                "schema": history_artifact.get("schema"),
                "dataset_tag": history_artifact.get("dataset_tag"),
                "stored_times_s": list(history_artifact.get("stored_times_s", [])),
            },
            "snapshot_stored_times_s": snapshot_times,
            "model_binding_after_history": binding.as_record(),
            "maxwell_branch_reference_state": "UNVERIFIED_NO_PUBLIC_REFERENCE_STATE_CAPTURE",
            "full_xmesh_internal_dof_capture": "COMPLETE_MAPPING_CAPTURED_NATIVE_BRANCH_IDENTITY_UNVERIFIED",
            "activation_history_semantics": "UNVERIFIED_NATIVE_CAPTURE_ONLY",
            "native_acceptance": "NOT_RUN",
        }
        _write_json_fsynced(output_dir / "v2_capture_chain.json", {
            "status": report["status"], "case_id": slot.case_id,
            "study_tag": slot.study_tag, "solver_tag": solver_tag,
            "model_contract": contract.get("reference"),
            "study_run": solve_action,
            "equation_view_capture": equation_view_ref,
            "solution_snapshot": snapshot_ref, "history_capture": history_ref,
            "equation_view_evidence": equation_view_evidence,
            "equation_view_association": equation_view_association,
            "snapshot_evidence": snapshot_evidence,
            "history_evidence": history_evidence,
            "maxwell_branch_reference_state": report["maxwell_branch_reference_state"],
            "native_acceptance": "NOT_RUN",
        })
        return binding, report

    def _capture_native_files(self, slot: SolveSlot, binding: ManagedModelBinding,
                              output_dir: Path, *, timeout_s: float,
                              reopened_origin: Mapping[str, Any] | None = None,
                              reopened_model_load: Mapping[str, Any] | None = None) -> dict[str, Any]:
        from tools.w24_science_acceptance import AcceptanceError

        output_dir.mkdir(parents=True, exist_ok=False)
        solver_tag = self.slot_solver_tags.get((slot.case_id, slot.study_tag))
        if not isinstance(solver_tag, str) or not solver_tag:
            raise CampaignError("native solution capture lacks the exact solved SolverSequence")
        token = re.sub(r"[^A-Za-z0-9_-]", "_", f"{slot.case_id}_{slot.study_tag}")
        if reopened_origin is not None:
            token = f"{token}_worker{self.worker_sessions}"
        base = self.workspace / "native_results" / token
        (self.workspace / "native_results").mkdir(exist_ok=True)
        v2_capture: dict[str, Any] | None = None
        post_v2_operations: list[dict[str, Any]] = []
        if self.cure_law_v2_enabled and slot.case_id not in MechanicsModelBindings.CASES:
            binding, v2_capture = self._capture_v2_pair(
                slot, binding, output_dir, base, token, solver_tag, timeout_s=timeout_s,
                reopened_origin=reopened_origin, reopened_model_load=reopened_model_load)
        field_path = _project_path(self.workspace, base.with_name(token + "_dofs.gz"), must_exist=False)
        field_binding_before = binding
        binding, field_response, field_readback = self._fixture_action(binding, "solution_snapshot", {
            "solver_tag": solver_tag, "path": str(field_path)}, timeout_s=timeout_s)
        if field_readback.get("status") != "SOLUTION_SNAPSHOT_WRITTEN" or field_readback.get("solver_tag") != solver_tag:
            raise CampaignError("native solution snapshot action lacks its exact solver/status readback")
        if field_readback.get("real_solution") is not True:
            raise CampaignError("native solution snapshot omitted COMSOL's real-valued solution readback")
        if v2_capture is not None:
            post_v2_operations.append(self._record_public_java_action(
                action="solution_snapshot", response=field_response,
                binding_before=field_binding_before, binding_after=binding,
                source_fixture=self.fixture,
                response_dir=self.evidence / "v2_postcapture_responses",
                response_label=f"{token}_legacy_snapshot"))
        field_evidence = _evidence_copy(field_path, output_dir / "field_snapshot.gz",
                                        status="NATIVE_RAW_FIELD_SNAPSHOT")
        frames = list(iter_solution_snapshot(Path(field_evidence["path"])))
        if not frames:
            raise CampaignError("native field snapshot contains no frames")
        capture: dict[str, Any] = {
            "status": "NATIVE_RAW_SNAPSHOT_CAPTURED",
            "cure_law_capture_mode": ("V2_PUBLIC_AUTHENTICATED" if v2_capture is not None else
                                       "V1_LEGACY_PATH_NOT_V2_ACCEPTANCE"),
            "field_snapshot": {**field_evidence, "solver_tag": solver_tag,
                               "dof_count": field_readback.get("dof_count"),
                               "stored_time_count": field_readback.get("stored_time_count"),
                               "real_solution": field_readback.get("real_solution")},
            "stored_times_s": [frame["time_s"] for frame in frames],
            "dof_names": field_readback.get("dof_names"),
            "model_binding": binding.as_record(),
        }
        if v2_capture is not None:
            capture["v2_capture"] = v2_capture
        if slot.case_id in MechanicsModelBindings.CASES:
            metrics_path = _project_path(self.workspace, base.with_name(token + "_mechanics.json"),
                                         must_exist=False)
            binding, _, metrics_readback = self._fixture_action(binding, "mechanics_capture", {
                "case_id": slot.case_id, "solver_tag": solver_tag,
                "stress_components": self.stress_components, "path": str(metrics_path)},
                timeout_s=timeout_s)
            if metrics_readback.get("status") != "NATIVE_MECHANICS_METRICS_CAPTURED":
                raise CampaignError("native mechanics capture returned no exact status")
            capture["mechanics_metrics"] = _evidence_copy(
                metrics_path, output_dir / "mechanics_metrics.json",
                status="NATIVE_MECHANICS_METRICS_CAPTURED")
        else:
            metrics_path = _project_path(self.workspace, base.with_name(token + "_cure_metrics.json"),
                                         must_exist=False)
            metrics_binding_before = binding
            binding, metrics_response, metrics_readback = self._fixture_action(binding, "cure_metrics_capture", {
                "solver_tag": solver_tag, "stress_components": self.stress_components,
                "path": str(metrics_path)}, timeout_s=timeout_s)
            if metrics_readback.get("status") != "NATIVE_CURE_METRICS_CAPTURED":
                raise CampaignError("native cure capture returned no exact status")
            if v2_capture is not None:
                post_v2_operations.append(self._record_public_java_action(
                    action="cure_metrics_capture", response=metrics_response,
                    binding_before=metrics_binding_before, binding_after=binding,
                    source_fixture=self.fixture,
                    response_dir=self.evidence / "v2_postcapture_responses",
                    response_label=f"{token}_cure_metrics"))
            capture["native_metrics"] = _evidence_copy(
                metrics_path, output_dir / "native_metrics.json",
                status="NATIVE_CURE_METRICS_CAPTURED")
        if v2_capture is not None:
            capture["post_v2_operations"] = post_v2_operations
        capture["model_binding"] = binding.as_record()
        self.captures[f"{slot.case_id}:{slot.study_tag}"] = capture
        return capture

    def capture_solution(self, slot: SolveSlot, *, output_dir: Path,
                         timeout_s: float) -> Mapping[str, Any]:
        binding = self._binding_for_slot(slot)
        if slot.case_id in MechanicsModelBindings.CASES:
            identity = self._managed_model_inspect(binding, timeout_s=min(timeout_s, 120.0))
            if identity["model_binding"]["model_tag"] != binding.model_tag:
                raise CampaignError("mechanics identity changed immediately before metrics capture")
        capture = self._capture_native_files(slot, binding, output_dir, timeout_s=timeout_s)
        if slot.case_id in MechanicsModelBindings.CASES:
            self.mechanics_models[slot.case_id] = ManagedModelBinding(
                self.project_id, binding.session_id, binding.model_ref,
                int(capture["model_binding"]["revision"]))
        else:
            self.models[slot.case_id] = ManagedModelBinding(
                self.project_id, binding.session_id, binding.model_ref,
                int(capture["model_binding"]["revision"]))
        return capture

    def validate_slot(self, slot: SolveSlot, capture: Mapping[str, Any],
                      prior_captures: Mapping[str, Mapping[str, Any]],
                      *, timeout_s: float) -> Mapping[str, Any]:
        del timeout_s
        result = _validate_native_science_slot(
            slot, capture, prior_captures, evidence=self.evidence,
            stress_components=self.stress_components)
        if slot.case_id in MechanicsModelBindings.CASES:
            return result
        if not self.cure_law_v2_enabled:
            if capture.get("cure_law_capture_mode") == "V2_PUBLIC_AUTHENTICATED" or "v2_capture" in capture:
                raise CampaignError("legacy v1 setup cannot claim a v2 authenticated capture")
            return {**result, "cure_law_capture_mode": "LEGACY_OR_UNDECLARED_V1_NOT_V2_ACCEPTANCE"}

        if (capture.get("cure_law_capture_mode") != "V2_PUBLIC_AUTHENTICATED" or
                not isinstance(capture.get("v2_capture"), Mapping)):
            raise CampaignError("setup declared cure-law v2 but this solved slot lacks its complete public v2 capture")
        current_frames, current_lineage = self._authenticated_v2_capture_frames(
            capture, expected_case=slot.case_id, expected_study=slot.study_tag)
        result = {**result, "v2_capture_lineage": current_lineage}

        if slot.case_id == "staged_baseline" and slot.study_tag in {"stdBake", "stdCool"}:
            source_tag = "stdUV" if slot.study_tag == "stdBake" else "stdBake"
            source_capture = prior_captures.get(f"staged_baseline:{source_tag}")
            if not isinstance(source_capture, Mapping):
                raise CampaignError(f"v2 staged handoff lacks its exact {source_tag} public capture")
            source_frames, source_lineage = self._authenticated_v2_capture_frames(
                source_capture, expected_case="staged_baseline", expected_study=source_tag)
            boundary = 120.0 if slot.study_tag == "stdBake" else 960.0
            source_index = _exact_time_row([row["time_s"] for row in source_frames], boundary,
                                           f"v2 {source_tag} source stored times")
            target_index = _exact_time_row([row["time_s"] for row in current_frames], boundary,
                                           f"v2 {slot.study_tag} target stored times")
            result["v2_stage_handoff"] = self._compare_authenticated_v2_frames(
                [source_frames[source_index]], [current_frames[target_index]],
                [source_lineage], [current_lineage], label=f"{source_tag}->{slot.study_tag} at {boundary:g}s",
                handoff=True)

        if slot.case_id == "continuous_comparator":
            staged_frames, staged_lineages = self._authenticated_staged_v2_schedule(prior_captures)
            result["v2_staged_continuous_comparison"] = self._compare_authenticated_v2_frames(
                staged_frames, current_frames, staged_lineages, [current_lineage],
                label="complete staged versus continuous W24 cure history")
        return result

    @staticmethod
    def _v2_full_dof_tolerances(frames: Sequence[Mapping[str, Any]],
                                lineages: Sequence[Mapping[str, Any]]) -> dict[str, float]:
        from tools.w24_science_acceptance import dof_value_map

        if not frames or not lineages:
            raise CampaignError("authenticated v2 comparison requires native frames and source lineages")
        observed = {row[0] for row in dof_value_map(frames[0]).values()}
        candidate_maps = [row.get("full_dof_absolute_tolerances") for row in lineages]
        if (any(not isinstance(row, Mapping) for row in candidate_maps) or
                any(dict(row) != dict(candidate_maps[0]) for row in candidate_maps[1:]) or
                observed != set(candidate_maps[0])):
            raise CampaignError("full Xmesh DOF set is not exactly covered by the authenticated v2 solver-atol readbacks")
        return {str(name): float(value) for name, value in candidate_maps[0].items()}

    def _validate_authenticated_v2_frame_set(self,
                                            frames: Sequence[Mapping[str, Any]],
                                            lineages: Sequence[Mapping[str, Any]], *,
                                            label: str) -> None:
        """Reject caller-edited frame tuples before any public-capture comparison."""
        if (not isinstance(frames, Sequence) or isinstance(frames, (str, bytes)) or not frames or
                not isinstance(lineages, Sequence) or isinstance(lineages, (str, bytes)) or not lineages):
            raise CampaignError(f"{label} lacks its in-process authenticated frame receipt")
        identity_fields = (
            "project_id", "model_ref", "binding_before", "binding_after",
            "case_id", "study_tag", "solver_tag", "configuration_sha256",
            "snapshot_schema", "layout_sha256", "operation_ids", "job_ids",
            "source_hashes", "stored_times_s", "snapshot_sha256",
        )
        authenticated_receipts: set[tuple[float, str]] = set()
        seen_tokens: set[str] = set()
        frame_hash_memo: dict[int, str] = {}
        for index, lineage in enumerate(lineages):
            if not isinstance(lineage, Mapping) or lineage.get("source_identity_authenticated") is not True:
                raise CampaignError(f"{label} lineage {index} is not a verified public source")
            token = lineage.get("capture_authentication_token")
            if not isinstance(token, str) or not token or token in seen_tokens:
                raise CampaignError(f"{label} lineage {index} has a missing or repeated in-process capture token")
            seen_tokens.add(token)
            registry = getattr(self, "_authenticated_v2_frame_receipts", {})
            receipt = registry.get(token) if isinstance(registry, Mapping) else None
            if not isinstance(receipt, Mapping):
                raise CampaignError(f"{label} lineage {index} is not present in this live adapter's authenticated capture registry")
            try:
                current_lineage_sha256 = _capture_value_sha256(lineage)
            except CampaignError as exc:
                raise CampaignError(f"{label} lineage {index} has invalid authenticated lineage content") from exc
            if current_lineage_sha256 != receipt.get("lineage_sha256"):
                raise CampaignError(f"{label} lineage {index} differs from the complete authenticated lineage content")
            if any(lineage.get(key) != receipt.get(key) for key in identity_fields):
                raise CampaignError(f"{label} lineage {index} no longer matches its authenticated OperationStore/artifact identity")
            frame_receipts = receipt.get("frame_receipts")
            if not isinstance(frame_receipts, list) or not frame_receipts:
                raise CampaignError(f"{label} lineage {index} has no exact authenticated frame hashes")
            for frame_receipt in frame_receipts:
                if not isinstance(frame_receipt, Mapping):
                    raise CampaignError(f"{label} lineage {index} has an invalid native frame hash record")
                time_s = frame_receipt.get("time_s")
                digest = frame_receipt.get("content_sha256")
                if (isinstance(time_s, bool) or not isinstance(time_s, (int, float)) or
                        not math.isfinite(float(time_s)) or not isinstance(digest, str) or
                        not SHA256_RE.fullmatch(digest)):
                    raise CampaignError(f"{label} lineage {index} has an incomplete native frame hash record")
                authenticated_receipts.add((float(time_s), digest))
        for index, frame in enumerate(frames):
            if not isinstance(frame, Mapping):
                raise CampaignError(f"{label} frame {index} is not a mapping")
            time_s = frame.get("time_s")
            if isinstance(time_s, bool) or not isinstance(time_s, (int, float)) or not math.isfinite(float(time_s)):
                raise CampaignError(f"{label} frame {index} has a nonfinite or invalid stored time")
            digest = _authenticated_capture_frame_sha256(frame, frame_hash_memo)
            if (float(time_s), digest) not in authenticated_receipts:
                raise CampaignError(f"{label} frame {index} differs from the exact frame parsed from its authenticated public artifact")

    def _compare_authenticated_v2_frames(self,
                                         source_frames: Sequence[Mapping[str, Any]],
                                         target_frames: Sequence[Mapping[str, Any]],
                                         source_lineages: Sequence[Mapping[str, Any]],
                                         target_lineages: Sequence[Mapping[str, Any]], *,
                                         label: str,
                                         handoff: bool = False) -> dict[str, Any]:
        from tools.w24_cure_law_v2 import (
            AcceptanceError, compare_v2_history_handoff,
            compare_v2_history_schedules,
        )
        from tools.w24_maxwell_branch_state import (
            MaxwellStateError, compare_full_xmesh_diagnostics,
        )

        self._validate_authenticated_v2_frame_set(
            source_frames, source_lineages, label=f"{label} source")
        self._validate_authenticated_v2_frame_set(
            target_frames, target_lineages, label=f"{label} target")

        all_frames = [*source_frames, *target_frames]
        all_lineages = [*source_lineages, *target_lineages]
        schemas = {row.get("dofs", {}).get("snapshot_schema")
                   for row in all_frames if isinstance(row, Mapping) and isinstance(row.get("dofs"), Mapping)}
        if len(schemas) != 1 or not schemas.issubset({"W24-DOF-SNAPSHOT-2", "W24-DOF-SNAPSHOT-3"}):
            raise CampaignError("authenticated cure comparison mixes unsupported Xmesh snapshot schemas")
        v3_diagnostic: dict[str, Any] | None = None
        if schemas == {"W24-DOF-SNAPSHOT-3"}:
            if not all(isinstance(row, Mapping) and row.get("snapshot_schema") == "W24-DOF-SNAPSHOT-3"
                       for row in all_lineages):
                raise CampaignError("V3 full-Xmesh comparison lineage does not bind every source and target capture")
            source_configurations = {row.get("configuration_sha256") for row in source_lineages}
            target_configurations = {row.get("configuration_sha256") for row in target_lineages}
            if (len(source_configurations) != 1 or len(target_configurations) != 1 or
                    not source_configurations or not target_configurations):
                raise CampaignError("V3 full-Xmesh comparison has missing or inconsistent validated physics fingerprints")
            try:
                v3_diagnostic = compare_full_xmesh_diagnostics(
                    source_frames, target_frames,
                    source_configuration_sha256=next(iter(source_configurations)),
                    target_configuration_sha256=next(iter(target_configurations)))
            except MaxwellStateError as exc:
                raise CampaignError(f"{label} V3 all-vector diagnostic failed closed: {exc}") from exc
            # No approved all-state physical mapping/unit/tolerance exists for the
            # hidden Xmesh fields. Keep the frozen visible-history gates while
            # reporting full-vector differences without calling them a pass.
            dof_tolerances = None
        else:
            dof_tolerances = self._v2_full_dof_tolerances(all_frames, all_lineages)
        try:
            if handoff:
                if len(source_frames) != 1 or len(target_frames) != 1:
                    raise CampaignError("v2 stage handoff compares exactly one shared native boundary frame")
                comparison = compare_v2_history_handoff(
                    source_frames[0], target_frames[0],
                    dof_abs_tolerances=dof_tolerances,
                    history_abs_tolerances=V2_HISTORY_ABS_TOLERANCES)
            else:
                comparison = compare_v2_history_schedules(
                    source_frames, target_frames,
                    dof_abs_tolerances=dof_tolerances,
                    history_abs_tolerances=V2_HISTORY_ABS_TOLERANCES)
        except AcceptanceError as exc:
            raise CampaignError(f"{label} v2 authenticated numerical comparison failed: {exc}") from exc
        return {
            **comparison,
            "status": ("V2_AUTHENTICATED_VISIBLE_HISTORY_GATES_MATCH_FULL_XMESH_DIAGNOSTIC_ONLY"
                       "_TOLERANCE_NOT_FROZEN_BRANCH_STATE_UNVERIFIED"
                       if v3_diagnostic is not None else
                       "V2_AUTHENTICATED_NUMERICAL_COMPARISON_PASS_BRANCH_STATE_UNVERIFIED"),
            "frozen_visible_history_gates": "PASS",
            "full_xmesh_diagnostics": v3_diagnostic,
            "label": label,
            "source_identity_authenticated": True,
            "source_lineages": [dict(row) for row in source_lineages],
            "target_lineages": [dict(row) for row in target_lineages],
            "native_acceptance": "NOT_RUN",
            "maxwell_branch_reference_state": "UNVERIFIED_NO_PUBLIC_REFERENCE_STATE_CAPTURE",
        }

    def _validate_worker2_reopen_prefix(self, *, expected_case: str, expected_study: str,
                                        prior_captures: Any,
                                        current_start: Any, current_load: Any,
                                        saved_model: Any,
                                        source_staged_captures: Any) -> None:
        """Bind each Worker2 stage to one original MPH load and its ordered prior captures."""
        prefixes = {"stdUV": (), "stdBake": ("stdUV",), "stdCool": ("stdUV", "stdBake")}
        if expected_case != "staged_baseline" or expected_study not in prefixes:
            raise CampaignError("Worker2 reopen chain is only defined for the exact staged baseline sequence")
        if not isinstance(prior_captures, Mapping):
            raise CampaignError("Worker2 capture omitted its exact prior-stage capture mapping")
        expected_keys = {f"staged_baseline:{tag}" for tag in prefixes[expected_study]}
        if set(prior_captures) != expected_keys:
            raise CampaignError(f"Worker2 {expected_study} reopen must retain every ordered prior stage exactly once")
        if (not isinstance(current_load, Mapping) or
                not isinstance(current_load.get("public_identity"), Mapping) or
                not isinstance(current_load.get("binding"), Mapping) or
                not isinstance(saved_model, Mapping) or
                not isinstance(source_staged_captures, Mapping) or
                not isinstance(current_start, Mapping)):
            raise CampaignError("Worker2 reopen prefix omitted its exact load, saved artifact, source chain, or start binding")

        def _binding_key(record: Any, label: str) -> tuple[Any, ...]:
            if (not isinstance(record, Mapping) or record.get("project_id") != self.project_id or
                    not isinstance(record.get("session_id"), str) or not record.get("session_id") or
                    not isinstance(record.get("model_ref"), Mapping) or
                    isinstance(record.get("revision"), bool) or not isinstance(record.get("revision"), int) or
                    record.get("revision") < 0):
                raise CampaignError(f"{label} omitted an exact project/session/ModelRef/revision identity")
            model_ref = record["model_ref"]
            if (model_ref.get("session_id") != record.get("session_id") or
                    not isinstance(model_ref.get("server_instance_id"), str) or
                    not model_ref.get("server_instance_id") or
                    isinstance(model_ref.get("generation"), bool) or
                    not isinstance(model_ref.get("generation"), int) or model_ref.get("generation") < 1):
                raise CampaignError(f"{label} ModelRef does not identify its exact Worker epoch")
            return (record.get("project_id"), record.get("session_id"),
                    json.dumps(dict(model_ref), sort_keys=True, separators=(",", ":")))

        load_binding = current_load.get("binding")
        expected_identity = current_load.get("public_identity")
        load_model_key = _binding_key(load_binding, "Worker2 model_load")
        prior_end: Mapping[str, Any] | None = None
        for tag in prefixes[expected_study]:
            key = f"staged_baseline:{tag}"
            previous_capture = prior_captures.get(key)
            if not isinstance(previous_capture, Mapping):
                raise CampaignError(f"Worker2 {expected_study} capture skipped its required {tag} predecessor")
            previous_report = previous_capture.get("v2_capture")
            if (not isinstance(previous_report, Mapping) or
                    previous_report.get("status") != "V2_REOPEN_PUBLIC_CAPTURE_CHAIN_VERIFIED_NATIVE_REVIEW_REQUIRED"):
                raise CampaignError(f"Worker2 prior {tag} capture is not an authenticated reopened stage")
            _frames, previous_lineage = self._authenticated_v2_capture_frames(
                previous_capture, expected_case=expected_case, expected_study=tag)
            previous_model_load = previous_report.get("model_load")
            previous_load = (previous_model_load.get("current_worker_model_load")
                             if isinstance(previous_model_load, Mapping) else None)
            if (not isinstance(previous_model_load, Mapping) or
                    not isinstance(previous_load, Mapping) or
                    previous_load.get("public_identity") != expected_identity or
                    previous_load.get("binding") != load_binding or
                    previous_model_load.get("saved_model") != saved_model or
                    previous_model_load.get("source_staged_captures") != source_staged_captures):
                raise CampaignError("Worker2 prior stage does not share the exact model_load, saved MPH, and Worker1 source chain")
            previous_start = previous_lineage.get("binding_before")
            previous_end = previous_lineage.get("binding_after")
            if prior_end is None and _binding_key(previous_start, f"Worker2 {tag} start") != load_model_key:
                raise CampaignError(f"Worker2 {tag} did not begin at the exact saved-MPH model_load revision")
            if prior_end is not None and previous_start != prior_end:
                raise CampaignError(f"Worker2 reopened stage revision chain breaks before {tag}")
            if _binding_key(previous_end, f"Worker2 {tag} end") != load_model_key:
                raise CampaignError(f"Worker2 {tag} changed ModelRef epoch during its capture")
            prior_end = previous_end

        current_key = _binding_key(current_start, f"Worker2 {expected_study} current start")
        if current_key != load_model_key:
            raise CampaignError("Worker2 staged capture changed the exact model_load ModelRef epoch")
        if prior_end is None:
            if current_start != load_binding:
                raise CampaignError(f"Worker2 {expected_study} first setup does not begin at model_load revision")
        elif current_start != prior_end:
            raise CampaignError(f"Worker2 revision chain does not continue from its prior staged capture before {expected_study}")

    def _authenticated_staged_v2_schedule(
            self, captures: Mapping[str, Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        expected_keys = {f"staged_baseline:{tag}" for tag in ("stdUV", "stdBake", "stdCool")}
        if set(captures) != expected_keys:
            raise CampaignError("full v2 staged schedule must contain exactly stdUV, stdBake, and stdCool")

        def _binding_key(binding: Any, label: str) -> tuple[Any, ...]:
            if (not isinstance(binding, Mapping) or binding.get("project_id") != self.project_id or
                    not isinstance(binding.get("session_id"), str) or not binding.get("session_id") or
                    not isinstance(binding.get("model_ref"), Mapping) or
                    isinstance(binding.get("revision"), bool) or
                    not isinstance(binding.get("revision"), int) or binding["revision"] < 0):
                raise CampaignError(f"{label} has no exact project/session/ModelRef/revision binding")
            ref = binding["model_ref"]
            if (ref.get("session_id") != binding.get("session_id") or
                    not isinstance(ref.get("server_instance_id"), str) or not ref.get("server_instance_id") or
                    isinstance(ref.get("generation"), bool) or not isinstance(ref.get("generation"), int) or
                    ref.get("generation") < 1):
                raise CampaignError(f"{label} ModelRef lacks its exact Worker epoch identity")
            return (binding.get("project_id"), binding.get("session_id"),
                    json.dumps(dict(binding.get("model_ref", {})), sort_keys=True, separators=(",", ":")))

        frames: list[dict[str, Any]] = []
        lineages: list[dict[str, Any]] = []
        previous_end: Mapping[str, Any] | None = None
        for index, study_tag in enumerate(("stdUV", "stdBake", "stdCool")):
            capture = captures.get(f"staged_baseline:{study_tag}")
            if not isinstance(capture, Mapping):
                raise CampaignError(f"full v2 staged schedule is missing {study_tag}")
            stage_frames, lineage = self._authenticated_v2_capture_frames(
                capture, expected_case="staged_baseline", expected_study=study_tag)
            start_binding = lineage.get("binding_before")
            end_binding = lineage.get("binding_after")
            start_identity = _binding_key(start_binding, f"{study_tag} staged capture start")
            end_identity = _binding_key(end_binding, f"{study_tag} staged capture end")
            start_revision = start_binding.get("revision")
            end_revision = end_binding.get("revision")
            if start_identity != end_identity or start_revision >= end_revision:
                raise CampaignError(f"{study_tag} staged capture changed ModelRef epoch or reversed revisions")
            if previous_end is not None and previous_end != start_binding:
                raise CampaignError(f"stdUV→stdBake→stdCool same-model revision chain breaks before {study_tag}")
            lineage["stage_binding_before"] = dict(start_binding)
            lineage["stage_binding_after"] = dict(end_binding)
            lineage["stage_post_v2_operation_ids"] = [
                row.get("public_identity", {}).get("operation_id")
                for row in capture.get("post_v2_operations", [])
                if isinstance(row, Mapping) and isinstance(row.get("public_identity"), Mapping)]
            token = lineage.get("capture_authentication_token")
            registry = getattr(self, "_authenticated_v2_frame_receipts", {})
            receipt = registry.get(token) if isinstance(token, str) and isinstance(registry, Mapping) else None
            if not isinstance(receipt, dict):
                raise CampaignError(f"{study_tag} staged lineage lost its in-process authenticated receipt")
            # These schedule fields are derived by this adapter after the
            # underlying capture was authenticated. Reseal only after those
            # trusted additions so later caller edits still fail the digest.
            receipt["lineage_sha256"] = _capture_value_sha256(lineage)
            frames.extend(stage_frames[:-1] if index < 2 else stage_frames)
            lineages.append(lineage)
            previous_end = end_binding
        return frames, lineages

    def reopen_staged_baseline(self, saved_path: Path, *, timeout_s: float) -> Mapping[str, Any]:
        if (self.worker_start_attempt_count >= MAX_SEQUENTIAL_WORKERS or
                self.worker2_start_intent_written):
            raise CampaignError("sequential Worker start-attempt cap is exhausted; retries are forbidden")
        path = _project_path(self.workspace, saved_path, must_exist=True)
        if self.project_ledger is None:
            raise CampaignError("staged baseline reopen lacks the one frozen project solve ledger")
        ledger_count_before = len(read_solve_ledger(self.project_ledger))
        if ledger_count_before != 5:
            raise CampaignError("staged baseline reopen is only valid after exactly five native solve submissions")
        original_captures = dict(self.captures)
        if not all(f"staged_baseline:{tag}" in original_captures
                   for tag in ("stdUV", "stdBake", "stdCool")):
            raise CampaignError("first Worker lacks all three solved stage captures before reopen")
        retired_bindings = list(self.models.values()) + list(self.mechanics_models.values())
        retired_refs = [binding.as_record() for binding in retired_bindings]
        retired_epochs = sorted({_model_ref_epoch(binding) for binding in retired_bindings})
        retired_server_ids = sorted({epoch[1] for epoch in retired_epochs})
        session_ids = {binding.session_id for binding in retired_bindings}
        if len(session_ids) != 1:
            raise CampaignError("Worker transition requires every current ModelRef to share its exact logical session")
        retirement_guard = getattr(self.daemon, "worker_retirement_guard", None)
        if not callable(retirement_guard):
            raise CampaignError("ControlDaemon has no admission-fenced Worker retirement guard")
        worker1_connection = self._preserve_worker_connection_receipt(
            worker_index=1, expected_pid=self._worker_process_identity["pid"])
        # The shared admission fence takes a fresh cross-project snapshot and
        # blocks new managed Worker work until exact Worker death is confirmed.
        with retirement_guard(self.project_id, next(iter(session_ids))) as lease:
            transition = self._require_worker_transition_safe(lease.readback)
            worker_close = self._close_first_worker_term_only(retirement_lease=lease)
            lease.confirm_worker_stopped()
            # The SQLite store is reopened by Worker 2; durable UNKNOWN is never
            # rewritten by this transition.
            self.daemon.close()
        self.daemon = None
        self._retired_model_ref_epochs.update(retired_epochs)
        self._retired_worker_server_instance_ids.update(retired_server_ids)
        self.server.worker_state = self.work / "worker-state-02"
        self.worker2_start_intent_written = True
        self.worker_start_attempt_count += 1
        _append_fsynced_jsonl(self.worker_lifecycle_log, {
            "event": "worker_start_intent", "attempt_index": 2,
            "at_epoch_s": _current_epoch_s(),
            "reason": "staged_baseline_fresh_worker_reopen",
            "worker_birth_count_before_attempt": len(self.worker_births),
            "successful_connected_worker_sessions_before_attempt": self.worker_sessions,
            "retry_policy": "never_retry_after_intent_or_observed_birth"})
        try:
            connection = self._journaled_worker2_start(self.server.start_worker)
        except BaseException as exc:
            self._record_worker_start_attempt(
                attempt_index=2, outcome="START_WORKER_RAISED_NO_RETRY",
                error=f"{type(exc).__name__}: {exc}")
            raise CampaignError("second Worker startup raised after its fsynced intent; no retry") from exc
        birth_observation = self._record_worker_start_attempt(
            attempt_index=2, outcome="START_WORKER_RETURNED")
        if birth_observation.get("birth_observed") is not True:
            raise CampaignError("second Worker returned without an observed Popen PID birth; no retry")
        observed_pid = birth_observation.get("pid")
        worker_runtime = connection.get("worker_runtime") if isinstance(connection, Mapping) else None
        if (not isinstance(worker_runtime, Mapping) or worker_runtime.get("pid") != observed_pid or
                connection.get("status") != "WORKER_CONNECTED_LOOPBACK_PROOF_PASSED" or
                connection.get("endpoint") != f"127.0.0.1:{self.server.port}"):
            raise CampaignError("second Worker connection receipt does not match its observed PID/owned loopback endpoint")
        worker2_connection = self._preserve_worker_connection_receipt(
            worker_index=2, expected_pid=observed_pid)
        self.worker_connection_receipts.append(connection)
        self._worker_object = getattr(self.server, "worker", None)
        self._worker_process_identity = _capture_worker_process_identity(self.server)
        if worker_runtime.get("pid") != self._worker_process_identity.get("pid"):
            raise CampaignError("second Worker startup receipt PID differs from exact task-owned process identity")
        if (birth_observation.get("process_identity") is None or
                not _identity_same(birth_observation["process_identity"], self._worker_process_identity)):
            raise CampaignError("second Worker Popen birth and exact live PID/birth/command identity differ")
        self.worker_sessions += 1
        _append_fsynced_jsonl(self.worker_lifecycle_log, {
            "event": "worker_connection_succeeded", "attempt_index": 2,
            "worker_session_index": self.worker_sessions, "worker_pid": observed_pid,
            "connection_receipt": worker2_connection,
            "worker_birth_count": len(self.worker_births),
            "successful_connected_worker_sessions": self.worker_sessions,
            "at_epoch_s": _current_epoch_s()})
        identity = self.setup_runner._engine_build_identity(connection.get("engine_version"))
        if not identity.get("matches_frozen_target"):
            raise CampaignError("second sequential Worker attached to a different COMSOL build")
        from comsol_mcp._control_daemon import ControlDaemon
        self.daemon = ControlDaemon(self.work / "control", worker=self.server.worker,
                                    project_root=self.server.project)
        reopened_project = self.daemon.project_authority.get_project(self.project_id)
        if (reopened_project.get("project_id") != self._immutable_project_id or
                Path(reopened_project.get("workspace", "")).resolve(strict=True) != self.workspace):
            raise CampaignError("reopened ControlDaemon changed registered project identity or workspace")
        connect = self._dispatch("server_connect", {"host": "127.0.0.1", "port": self.server.port},
                                 timeout_s=min(timeout_s, 120.0))
        if (connect.get("success") is not True or
                connect.get("data", {}).get("endpoint") != f"127.0.0.1:{self.server.port}"):
            raise CampaignError("second managed Worker connection did not verify the owned listener")
        self.models.clear()
        self.mechanics_models.clear()
        reopened = self._load_registered_copy("staged_baseline", path,
                                               timeout_s=min(timeout_s, 240.0))
        load_receipt = self.model_load_receipts.get("staged_baseline_worker2")
        if not isinstance(load_receipt, Mapping):
            raise CampaignError("second Worker staged baseline model_load receipt was not preserved separately")
        response = load_receipt["response"]
        persisted = load_receipt["persisted_project_binding"]
        if (not isinstance(persisted, Mapping) or persisted.get("attribution") != "PROJECT_BOUND" or
                persisted.get("project_id") != self.project_id):
            raise CampaignError("second Worker staged baseline load lost its project association")
        identity = self._managed_model_inspect(reopened, timeout_s=min(timeout_s, 120.0))
        self.models["staged_baseline"] = reopened
        binding = reopened
        readback_before = binding
        binding, readback_response, readback = self._fixture_action(
            binding, "readback", {}, timeout_s=min(timeout_s, 180.0))
        studies = readback.get("studies")
        if not isinstance(studies, list) or not {"stdUV", "stdBake", "stdCool"}.issubset(studies):
            raise CampaignError("second Worker reopened MPH lacks the three native staged studies")
        reopened_study_configuration = self._check_study_readback(
            readback, SOLVE_PLAN[4], max_step_s=1.0)
        reopened_solvers: dict[str, Any] = {}
        for slot in SOLVE_PLAN[2:5]:
            reopened_tag = self._check_solver_readback(readback, slot, max_step_s=1.0)
            original_tag = self.slot_solver_tags.get((slot.case_id, slot.study_tag))
            if reopened_tag != original_tag:
                raise CampaignError(f"second Worker changed the saved {slot.study_tag} solver sequence identity")
            reopened_solvers[slot.study_tag] = reopened_tag
        expected_configuration = self.staged_baseline_configuration_readback
        if not isinstance(expected_configuration, Mapping):
            raise CampaignError("first Worker never recorded the exact staged solver/handoff configuration")
        if reopened_study_configuration != expected_configuration.get("study_time_readbacks"):
            raise CampaignError("second Worker staged time/initial-solution handoff readbacks differ from the saved model")
        if dict(readback.get("solver_readbacks", {})) != expected_configuration.get("solver_readbacks"):
            raise CampaignError("second Worker solver attachment/strict output readbacks differ from the saved model")
        if list(readback.get("mesh_tags", [])) != expected_configuration.get("mesh_tags") or \
                readback.get("quasistatic_readback") != expected_configuration.get("quasistatic_readback"):
            raise CampaignError("second Worker mesh or quasistatic physics readback differs from the saved baseline")
        if self.cure_law_v2_enabled:
            initial_slot = next(row for row in SOLVE_PLAN if row.case_id == "staged_baseline" and row.study_tag == "stdUV")
            initial_ref = self._record_public_java_action(
                action="readback", response=readback_response,
                binding_before=readback_before, binding_after=binding,
                source_fixture=self.fixture, response_dir=self.evidence / "v2_setup_readbacks",
                response_label="staged_baseline_worker2_initial_setup")
            if self._reauthenticate_public_java_action(initial_ref).get("readback_data") != readback:
                raise CampaignError("Worker2 actual setup response differs from its durable public readback")
            self.slot_native_setup_readbacks[
                f"{initial_slot.case_id}:{initial_slot.study_tag}:worker{self.worker_sessions}"] = initial_ref
            self.models["staged_baseline"] = binding
        self.models["staged_baseline"] = binding
        reloaded_case_models: dict[str, dict[str, Any]] = {}
        for case_name in ("continuous_comparator", "reset_negative_control", "coarse_mesh",
                          "tight_time", "dose_sensitivity"):
            if case_name not in self.input_paths:
                raise CampaignError(f"Worker 2 cannot reload missing registered input for {case_name}")
            case_binding = self._load_registered_copy(
                case_name, self.input_paths[case_name], timeout_s=min(timeout_s, 240.0))
            if _model_ref_epoch(case_binding) in self._retired_model_ref_epochs:
                raise CampaignError(f"Worker 2 reused a retired ModelRef epoch for {case_name}")
            reloaded_case_models[case_name] = case_binding.as_record()
        if set(reloaded_case_models) != {"continuous_comparator", "reset_negative_control",
                                        "coarse_mesh", "tight_time", "dose_sensitivity"}:
            raise CampaignError("Worker 2 did not rebind every remaining science case")
        reopened_receipts: dict[str, Any] = {}
        reopened_captures: dict[str, Mapping[str, Any]] = {}
        for study_tag in ("stdUV", "stdBake", "stdCool"):
            slot = next(row for row in SOLVE_PLAN if row.case_id == "staged_baseline" and row.study_tag == study_tag)
            state_key = f"{slot.case_id}:{slot.study_tag}:worker{self.worker_sessions}"
            if study_tag != "stdUV" and self.cure_law_v2_enabled:
                setup_before = binding
                binding, setup_response, setup_readback = self._fixture_action(
                    binding, "readback", {}, timeout_s=min(timeout_s, 180.0))
                if (self._check_solver_readback(setup_readback, slot, max_step_s=1.0) !=
                        self.slot_solver_tags.get((slot.case_id, slot.study_tag)) or
                        self._check_study_readback(setup_readback, slot, max_step_s=1.0) !=
                        expected_configuration.get("study_time_readbacks")):
                    raise CampaignError(f"Worker2 {study_tag} readback differs from its frozen staged setup")
                setup_ref = self._record_public_java_action(
                    action="readback", response=setup_response,
                    binding_before=setup_before, binding_after=binding,
                    source_fixture=self.fixture, response_dir=self.evidence / "v2_setup_readbacks",
                    response_label=f"staged_baseline_worker2_{study_tag}_setup")
                if self._reauthenticate_public_java_action(setup_ref).get("readback_data") != setup_readback:
                    raise CampaignError(f"Worker2 {study_tag} setup response differs from its durable public readback")
                self.slot_native_setup_readbacks[state_key] = setup_ref
            if self.cure_law_v2_enabled:
                binding, contract = self._ensure_v2_contract_readback(
                    slot, binding, timeout_s=min(timeout_s, 180.0), state_key=state_key)
                if not isinstance(contract, Mapping):
                    raise CampaignError(f"Worker2 {study_tag} current-model v2 contract readback is missing")
                self.models["staged_baseline"] = binding
            capture = self._capture_native_files(
                slot, binding, self.evidence / f"staged_reopen_worker2_{study_tag}",
                timeout_s=min(timeout_s, 600.0),
                reopened_origin=({**original_captures[f"staged_baseline:{study_tag}"],
                                 "staged_source_captures": original_captures,
                                 "worker2_prior_captures": {
                                     f"staged_baseline:{prior_tag}": reopened_captures[prior_tag]
                                     for prior_tag in {
                                         "stdUV": (), "stdBake": ("stdUV",),
                                         "stdCool": ("stdUV", "stdBake"),
                                     }[study_tag]}}
                                if self.cure_law_v2_enabled else None),
                reopened_model_load=load_receipt if self.cure_law_v2_enabled else None)
            _validate_native_science_slot(slot, capture, {}, evidence=self.evidence,
                                          stress_components=self.stress_components,
                                          allow_missing_prior_for_reopen=True)
            original = original_captures[f"staged_baseline:{study_tag}"]
            reopen_comparison = _compare_reopened_stage_capture(
                original, capture, self.evidence, stress_components=self.stress_components)
            if self.cure_law_v2_enabled:
                original_v2_frames, original_v2_lineage = self._authenticated_v2_capture_frames(
                    original, expected_case="staged_baseline", expected_study=study_tag)
                reopened_v2_frames, reopened_v2_lineage = self._authenticated_v2_capture_frames(
                    capture, expected_case="staged_baseline", expected_study=study_tag)
                reopen_comparison["v2_authenticated_schedule_comparison"] = \
                    self._compare_authenticated_v2_frames(
                        original_v2_frames, reopened_v2_frames,
                        [original_v2_lineage], [reopened_v2_lineage],
                        label=f"Worker1→Worker2 reopened {study_tag} stored-time schedule")
            reopened_receipts[study_tag] = reopen_comparison
            reopened_captures[study_tag] = capture
            binding = ManagedModelBinding(self.project_id, binding.session_id,
                                          binding.model_ref,
                                          int(capture["model_binding"]["revision"]))
        full_v2_reopen_comparison: dict[str, Any] | None = None
        if self.cure_law_v2_enabled:
            original_staged_frames, original_staged_lineages = self._authenticated_staged_v2_schedule(
                original_captures)
            reopened_stage_mapping = {
                f"staged_baseline:{tag}": reopened_captures[tag]
                for tag in ("stdUV", "stdBake", "stdCool")
            }
            reopened_staged_frames, reopened_staged_lineages = self._authenticated_staged_v2_schedule(
                reopened_stage_mapping)
            full_v2_reopen_comparison = self._compare_authenticated_v2_frames(
                original_staged_frames, reopened_staged_frames,
                original_staged_lineages, reopened_staged_lineages,
                label="complete Worker1 versus Worker2 staged cure schedule")
        self.models["staged_baseline"] = binding
        self.captures = original_captures
        self.reopened_stage_captures = reopened_captures
        ledger_count_after = len(read_solve_ledger(self.project_ledger))
        if ledger_count_after != ledger_count_before:
            raise CampaignError("stage readback in the second Worker changed the durable solve ledger")
        return {"status": "NATIVE_REOPEN_READBACK_PASS",
                "path": str(path), "sha256": sha256(path),
                "project_id": self.project_id, "project_workspace": str(self.workspace),
                "worker_session_count": self.worker_sessions,
                "worker_birth_count": len(self.worker_births),
                "worker_births": list(self.worker_births),
                "worker_transition_reconciliation": transition,
                "worker_close_receipt": worker_close,
                "worker1_connection_receipt": worker1_connection,
                "worker2_connection_receipt": worker2_connection,
                "retired_worker1_model_refs": retired_refs,
                "retired_worker1_model_ref_epochs": [list(epoch) for epoch in retired_epochs],
                "retired_worker1_server_instance_ids": retired_server_ids,
                "worker2_reloaded_case_models": reloaded_case_models,
                "model_binding": binding.as_record(),
                "persisted_project_binding": dict(persisted),
                "managed_native_identity_readback": identity,
                "native_readback": readback,
                "stage_reopen_checks": reopened_receipts,
                "full_v2_staged_reopen_comparison": full_v2_reopen_comparison,
                "reopened_solver_tags": reopened_solvers,
                "solve_ledger_count_before": ledger_count_before,
                "solve_ledger_count_unchanged": ledger_count_after}


def _current_epoch_s() -> float:
    import time
    return time.time()


def _append_fsynced_jsonl(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(dict(value), ensure_ascii=False, sort_keys=True,
                                allow_nan=False, default=str) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _mac_birth_epoch(raw: str) -> float:
    parsed = datetime.strptime(raw, "%a %b %d %H:%M:%S %Y")
    return parsed.replace(tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()


# This is a ceiling and an exact order, not an executed-solve count.  The
# native ledger is the sole counter of calls actually submitted to Study.run.
SOLVE_PLAN: tuple[SolveSlot, ...] = (
    SolveSlot("mechanics_free_expansion", "stdMech", "free-expansion analytic benchmark; stress variables first evaluated here"),
    SolveSlot("mechanics_fully_fixed", "stdMech", "fully-fixed hydrostatic benchmark; stop campaign if four stress components fail"),
    SolveSlot("staged_baseline", "stdUV", "positive staged path, UV stage"),
    SolveSlot("staged_baseline", "stdBake", "positive staged path, bake stage"),
    SolveSlot("staged_baseline", "stdCool", "positive staged path, cool stage; immediate MPH save"),
    SolveSlot("continuous_comparator", "stdCont", "same-equation continuous reference"),
    SolveSlot("reset_negative_control", "stdBake", "stage-2 reset negative control"),
    SolveSlot("coarse_mesh", "stdCont", "20 um adhesive mesh sensitivity"),
    SolveSlot("tight_time", "stdCont", "0.5 s maximum-step sensitivity"),
    SolveSlot("dose_sensitivity", "stdCont", "kUV=1.1e-2 1/s sensitivity"),
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _as_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CampaignError(f"{label} must be an object")
    return value


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CampaignError(f"cannot read {label}: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise CampaignError(f"{label} must contain a JSON object")
    return value


def _expression_candidate_cues(row: list[Any]) -> list[str]:
    joined = "\x1f".join(cell for cell in row if isinstance(cell, str))
    lowered = joined.lower()
    cues = [cue for cue in ("stress", "cauchy", "shear") if cue in lowered]
    solid_stress_identifier = bool(
        re.search(r"(?<![a-z0-9_])solid\.s[a-z0-9_]*(?![a-z0-9_])", lowered))
    if not cues and not solid_stress_identifier:
        return []
    cues.extend(cue for cue in ("hoop", "radial", "circumferential", "azimuthal")
                if cue in lowered)
    if solid_stress_identifier:
        cues.append("solid.s-prefixed-identifier")
    return cues


def validate_equation_view_inventory(value: Any) -> dict[str, Any]:
    """Verify a complete native descriptor artifact without guessing variable names."""
    mapping = _as_mapping(value, "equation_view_inventory")
    if mapping.get("status") != "COMPLETE_NOT_EVALUATED":
        raise CampaignError("Solid Mechanics Equation View inventory is not complete")
    path_text = mapping.get("path")
    if not isinstance(path_text, str) or not path_text:
        raise CampaignError("Equation View inventory receipt has no durable artifact path")
    path = Path(path_text).expanduser()
    try:
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise CampaignError(f"Equation View inventory artifact is unavailable: {exc}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise CampaignError("Equation View inventory artifact must be a regular non-symlink file")
    allowed = (REPO / "docs/full_project_execution/w24/evidence").resolve()
    if not (resolved.is_relative_to(allowed) or
            resolved.as_posix().startswith("/private/tmp/comsol-mcp-w24-cure-")):
        raise CampaignError("Equation View inventory artifact is outside W24 evidence/private work roots")
    expected_size = mapping.get("size_bytes")
    expected_sha = mapping.get("sha256")
    if (isinstance(expected_size, bool) or not isinstance(expected_size, int) or expected_size <= 0 or
            not isinstance(expected_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha)):
        raise CampaignError("Equation View inventory receipt has an invalid size or SHA-256")
    if resolved.stat().st_size != expected_size or sha256(resolved) != expected_sha:
        raise CampaignError("Equation View inventory bytes differ from their frozen receipt")
    artifact = _read_json(resolved, "native Equation View inventory")
    candidate_rule = (
        "candidate rows have case-insensitive stress/cauchy/shear text or a "
        "solid.s[a-z0-9_]* identifier; optional component cues are "
        "hoop/radial/circumferential/azimuthal; full raw rows are preserved; discovery only"
    )
    if (artifact.get("schema") != "W24_COMSOL_EQUATION_VIEW_EXPRESSION_INVENTORY_V1" or
            artifact.get("status") != "COMPLETE_NOT_EVALUATED" or
            artifact.get("complete") is not True or artifact.get("read_only") is not True or
            artifact.get("model_mutations") != 0 or artifact.get("study_run_calls") != 0 or
            artifact.get("table_request") != ["Expression", "recursive", "all"] or
            artifact.get("candidate_rule") != candidate_rule or
            artifact.get("solid_physics_tags") != ["solid"] or artifact.get("errors") != []):
        raise CampaignError("Equation View inventory lacks its complete native read-only table contract")
    tables = artifact.get("feature_tables")
    if not isinstance(tables, list) or not tables:
        raise CampaignError("Equation View inventory contains no Solid Mechanics feature tables")
    row_count = 0
    candidate_count = 0
    for table in tables:
        if not isinstance(table, dict) or table.get("status") != "READ":
            raise CampaignError("Equation View inventory contains an unreadable feature table")
        rows = table.get("raw_rows")
        candidates = table.get("stress_candidate_rows")
        if not isinstance(rows, list) or table.get("row_count") != len(rows):
            raise CampaignError("Equation View inventory raw rows are truncated")
        if not isinstance(candidates, list):
            raise CampaignError("Equation View inventory candidate rows are missing")
        if any(not isinstance(row, list) or
               not all(cell is None or isinstance(cell, str) for cell in row) for row in rows):
            raise CampaignError("Equation View inventory contains a malformed native row")
        expected_candidates = [
            {"row_index": index, "cues": _expression_candidate_cues(row), "raw_row": row}
            for index, row in enumerate(rows) if _expression_candidate_cues(row)
        ]
        if candidates != expected_candidates:
            raise CampaignError("Equation View discovery candidates do not match full native rows/rule")
        row_count += len(rows)
        candidate_count += len(candidates)
    if (mapping.get("feature_count") != len(tables) or
            mapping.get("expression_row_count") != row_count or
            mapping.get("stress_candidate_row_count") != candidate_count or
            artifact.get("feature_count") != len(tables) or
            artifact.get("expression_row_count") != row_count or
            artifact.get("stress_candidate_row_count") != candidate_count):
        raise CampaignError("Equation View receipt counters disagree with the full raw artifact")
    return {
        **dict(mapping),
        "path": str(resolved),
        "status": "COMPLETE_NOT_EVALUATED",
        "stress_semantics": "DOCUMENTED_NATIVE_TABLE_CANDIDATES_NOT_EVALUATED",
    }


def _validated_setup_cure_v2_claim(build: Mapping[str, Any]) -> dict[str, Any]:
    """Separate an explicit, complete native v2 setup readback from legacy v1."""
    version = build.get("cure_law_version")
    v2_markers = ("relative_exposure_dose", "spatial_uv_readback",
                  "activation_readback", "viscoelastic_readback",
                  "dose_solver_tolerance_readbacks")
    has_v2_fields = any(key in build for key in v2_markers)
    if version in (None, "W24_CURE_LAW_V1") and not has_v2_fields:
        return {"enabled": False, "status": "LEGACY_OR_UNDECLARED_V1_SETUP_NOT_V2_ACCEPTANCE"}
    from tools.w24_cure_v2_capture import CaptureError, validate_cure_law_v2_contract_readback

    try:
        validated = validate_cure_law_v2_contract_readback(build)
    except CaptureError as exc:
        raise CampaignError(f"setup declares an incomplete or invalid cure-law v2 readback: {exc}") from exc
    return {"enabled": True, "status": "EXPLICIT_V2_SETUP_READBACK_VALIDATED",
            "validation": validated}


def validate_template_receipt(receipt_path: Path) -> dict[str, Any]:
    """Verify an immutable configured native template without touching COMSOL."""
    receipt_path = receipt_path.expanduser().resolve(strict=True)
    receipt = _read_json(receipt_path, "configured template receipt")
    if receipt.get("status") != "NATIVE_CONFIGURED_TEMPLATE_SAVED_NOT_SOLVED":
        raise CampaignError("setup receipt does not prove a successful unsolved native template")
    if receipt.get("native_solver_submissions") != []:
        raise CampaignError("configured template receipt must show zero preflight study/solver submissions")
    if receipt.get("reopen_status") != "NATIVE_TEMPLATE_REOPEN_READBACK_PASS_NOT_SOLVED":
        raise CampaignError("configured template receipt lacks native same-worker save/reopen readback")
    identity = _as_mapping(receipt.get("engine_identity"), "engine_identity")
    if identity.get("matches_frozen_target") is not True:
        raise CampaignError(f"template engine does not match {EXPECTED_ENGINE}")
    freeze_sha = receipt.get("freeze_sha256")
    if not isinstance(freeze_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", freeze_sha):
        raise CampaignError("configured template receipt is not bound to a valid setup freeze hash")

    template_text = receipt.get("path")
    if not isinstance(template_text, str) or not template_text:
        raise CampaignError("configured template receipt has no saved MPH path")
    template_path = Path(template_text).expanduser()
    try:
        metadata = template_path.lstat()
        template_path = template_path.resolve(strict=True)
    except OSError as exc:
        raise CampaignError(f"configured template is unavailable: {template_path}: {exc}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise CampaignError("configured template must be a regular non-symlink file")
    if not template_path.as_posix().startswith("/private/tmp/comsol-mcp-w24-cure-"):
        raise CampaignError("configured template path is outside its task-owned W24 work tree")
    expected_size = receipt.get("size_bytes")
    expected_sha = receipt.get("sha256")
    if isinstance(expected_size, bool) or not isinstance(expected_size, int) or expected_size <= 0:
        raise CampaignError("configured template receipt has an invalid byte count")
    if not isinstance(expected_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha):
        raise CampaignError("configured template receipt has an invalid SHA-256")
    if template_path.stat().st_size != expected_size or sha256(template_path) != expected_sha:
        raise CampaignError("configured template bytes no longer match their native receipt")

    build = _as_mapping(receipt.get("fixture_readback"), "fixture_readback")
    if (build.get("status") != "BUILT_NOT_SOLVED" or
            build.get("geometry_dimension") != 2 or
            build.get("geometry_axisymmetric") is not True or
            build.get("geometry_domain_count") != 4):
        raise CampaignError("template native readback does not prove the frozen four-domain axisymmetric coupon")
    volumes = _as_mapping(build.get("domain_readbacks"), "domain_readbacks")
    if set(volumes) != {"alumina", "gold", "adhesive", "fiber"}:
        raise CampaignError("template native readback lacks all four material-domain volume checks")
    for material, row in volumes.items():
        row = _as_mapping(row, f"domain_readbacks.{material}")
        error = row.get("relative_volume_error")
        if isinstance(error, bool) or not isinstance(error, (int, float)) or not 0 <= error <= 1e-6:
            raise CampaignError(f"template native volume gate did not pass for {material}")
    if len(_as_mapping(build.get("solver_readbacks"), "solver_readbacks")) != 3:
        raise CampaignError("template native readback lacks all three staged solver configurations")
    if build.get("solid_quasistatic_readback") != "Quasistatic":
        raise CampaignError("template does not natively read back quasistatic Solid Mechanics")
    v2_setup = _validated_setup_cure_v2_claim(build)
    equation_view_inventory = validate_equation_view_inventory(receipt.get("equation_view_inventory"))
    runtime_environment = receipt.get("runtime_environment")
    if (not isinstance(runtime_environment, Mapping) or
            not isinstance(runtime_environment.get("python_executable"), str) or
            not Path(runtime_environment["python_executable"]).is_absolute() or
            not isinstance(runtime_environment.get("python_resolved_executable"), str) or
            not Path(runtime_environment["python_resolved_executable"]).is_absolute() or
            not isinstance(runtime_environment.get("distributions"), list) or
            not isinstance(runtime_environment.get("distributions_sha256"), str) or
            not SHA256_RE.fullmatch(runtime_environment["distributions_sha256"]) or
            not isinstance(runtime_environment.get("pip_check_python"), str) or
            not Path(runtime_environment["pip_check_python"]).is_absolute()):
        raise CampaignError("configured template receipt lacks the exact frozen Python/dependency environment")
    if hashlib.sha256(json.dumps(runtime_environment["distributions"], ensure_ascii=False,
                                 sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest() != runtime_environment["distributions_sha256"]:
        raise CampaignError("configured template frozen distribution list does not match its SHA-256")
    from tools.run_native_w24_cure_preflight import _verify_registered_template_files
    try:
        registered_project = _verify_registered_template_files(
            receipt, template_path, equation_view_inventory)
    except RuntimeError as exc:
        raise CampaignError(f"registered project artifact validation failed: {exc}") from exc
    return {
        "receipt_path": str(receipt_path),
        "receipt_sha256": sha256(receipt_path),
        "template_path": str(template_path),
        "template_size_bytes": expected_size,
        "template_sha256": expected_sha,
        "engine_identity": dict(identity),
        "setup_freeze_sha256": freeze_sha,
        "fixture_readback": dict(build),
        "cure_law_v2_capture": v2_setup,
        "equation_view_inventory": equation_view_inventory,
        "runtime_environment": dict(runtime_environment),
        "project_id": registered_project["project_id"],
        "project_workspace": registered_project["workspace"],
        "registered_project": registered_project,
    }


def runtime_source_manifest() -> dict[str, dict[str, str]]:
    """Hash all Python runtime modules because imports are dynamic/indirect."""
    paths = sorted(path for path in (REPO / "comsol_mcp").glob("*.py")
                   if not path.name.startswith("._"))
    if not paths:
        raise CampaignError("no runtime modules found for the frozen source closure")
    paths.extend(sorted(path for path in (REPO / "comsol_mcp/worker_java").glob("*.java")
                        if not path.name.startswith("._")))
    paths.extend([
        Path(__file__).resolve(), SCIENCE_FIXTURE, V2_CONTROL_FIXTURE,
        COUPON_FIXTURE, PLAN, SETUP_RUNNER,
        REPO / "tools/w24_cure_law_v2.py",
        REPO / "tools/w24_maxwell_branch_state.py",
        REPO / "tools/w24_cure_v2_capture.py",
        REPO / "tools/w24_science_acceptance.py",
        REPO / "tools/run_native_resume_smoke.py",
        REPO / "tools/run_native_w23_te_managed_preflight.py",
        REPO / "tools/run_native_artifact_geometry.py",
        REPO / "tests/test_w24_cure_science_runner.py",
        REPO / "tests/test_w24_cure_law_v2.py",
        REPO / "tests/test_w24_cure_v2_capture.py",
        REPO / "tests/test_w24_cure_capture_link.py",
        REPO / "tests/test_w24_maxwell_branch_state.py",
        REPO / "tests/test_w24_cure_coupon_fixture.py",
        REPO / "tests/test_w24_science_acceptance.py",
    ])
    result: dict[str, dict[str, str]] = {}
    for path in sorted(set(paths)):
        if not path.is_file():
            raise CampaignError(f"frozen W24 source is missing: {path}")
        result[str(path.relative_to(REPO))] = {"path": str(path), "sha256": sha256(path)}
    return result


def verify_source_manifest(manifest: Mapping[str, Mapping[str, str]]) -> None:
    current = runtime_source_manifest()
    frozen = {name: dict(record) for name, record in manifest.items()}
    if current != frozen:
        changed = sorted(name for name in set(current) | set(frozen) if current.get(name) != frozen.get(name))
        raise CampaignError("frozen W24 source closure changed before launch: " + ", ".join(changed))


def _canonical_json_sha256(value: Mapping[str, Any]) -> str:
    payload = json.dumps(dict(value), ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _write_new_json(path: Path, value: Mapping[str, Any]) -> None:
    """Write an immutable JSON candidate without replacing an existing artifact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write((json.dumps(dict(value), ensure_ascii=False, sort_keys=True,
                                 indent=2, allow_nan=False, default=str) + "\n").encode("utf-8"))
        stream.flush()
        os.fsync(stream.fileno())
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def prepare_campaign_candidate(template_receipt_path: Path, output_path: Path) -> dict[str, Any]:
    """Freeze a complete, unsolved setup input for later independent approval."""
    setup = validate_template_receipt(template_receipt_path)
    source_manifest = runtime_source_manifest()
    stress_components = map_stress_components(setup["equation_view_inventory"])
    candidate = freeze_campaign(
        {"path": setup["template_path"], "size_bytes": setup["template_size_bytes"],
         "sha256": setup["template_sha256"], "freeze_sha256": setup["setup_freeze_sha256"],
         "project_id": setup.get("project_id"),
         "project_workspace": setup.get("project_workspace"),
         "registered_project": setup.get("registered_project"),
         "native_solver_submissions": []},
        source_manifest=source_manifest,
        equation_view_inventory=setup["equation_view_inventory"],
    )
    candidate.update({
        "schema": "W24_CURE_SCIENCE_CANDIDATE_V1",
        "status": "CANDIDATE_FROZEN_AWAITING_ROOT_APPROVAL",
        "approval_status": "NOT_APPROVED",
        "template_receipt": {
            "path": str(Path(template_receipt_path).expanduser().resolve(strict=True)),
            "sha256": setup["receipt_sha256"],
        },
        "stress_components_from_complete_native_equation_view": stress_components,
        "solve_plan_sha256": _canonical_json_sha256(
            {"slots": [slot.__dict__ for slot in SOLVE_PLAN],
             "max_submissions": MAX_STUDY_RUN_SUBMISSIONS,
             "max_sequential_workers": MAX_SEQUENTIAL_WORKERS}),
        "native_scope": {
            "owned_servers": 1,
            "max_seconds_from_exact_birth_including_cleanup": MAX_BIRTH_BUDGET_S,
            "max_study_run_submissions": MAX_STUDY_RUN_SUBMISSIONS,
            "max_sequential_workers": MAX_SEQUENTIAL_WORKERS,
            "cleanup_reserve_seconds": CLEANUP_RESERVE_S,
            "study_run_order": [slot.__dict__ for slot in SOLVE_PLAN],
            "export_calls": 0,
            "automatic_retries": 0,
        },
        "project_workspace_contract": {
            "create_before_server_birth": True,
            "use_authoritative_project_create_workspace": True,
            "template_and_independent_model_copies_inside_workspace": True,
            "native_outputs_inside_workspace": True,
        },
    })
    candidate.pop("freeze_sha256", None)
    candidate["freeze_sha256"] = _canonical_json_sha256(candidate)
    _write_new_json(output_path, candidate)
    return {"status": candidate["status"], "path": str(output_path.resolve()),
            "freeze_sha256": candidate["freeze_sha256"],
            "file_sha256": sha256(output_path), "source_count": len(source_manifest),
            "study_run_submissions_authorized_by_candidate": MAX_STUDY_RUN_SUBMISSIONS,
            "approval_status": "NOT_APPROVED"}


def validate_campaign_candidate(candidate_path: Path, expected_sha256: str,
                                approval_path: Path, setup: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    candidate_path = candidate_path.expanduser().resolve(strict=True)
    candidate = _read_json(candidate_path, "W24 campaign candidate")
    recorded = candidate.get("freeze_sha256")
    without_digest = dict(candidate)
    without_digest.pop("freeze_sha256", None)
    actual_digest = _canonical_json_sha256(without_digest)
    if (candidate.get("schema") != "W24_CURE_SCIENCE_CANDIDATE_V1" or
            candidate.get("status") != "CANDIDATE_FROZEN_AWAITING_ROOT_APPROVAL" or
            candidate.get("approval_status") != "NOT_APPROVED" or
            recorded != actual_digest or expected_sha256 != actual_digest):
        raise CampaignError("W24 candidate schema/status/freeze digest is invalid or differs from the requested SHA-256")
    receipt_row = candidate.get("template_receipt")
    if (not isinstance(receipt_row, Mapping) or
            Path(str(receipt_row.get("path", ""))).resolve(strict=True) != Path(setup["receipt_path"]) or
            receipt_row.get("sha256") != setup.get("receipt_sha256")):
        raise CampaignError("frozen W24 candidate is not bound to the exact successful setup receipt")
    template = candidate.get("template")
    if (not isinstance(template, Mapping) or template.get("sha256") != setup.get("template_sha256") or
            template.get("project_id") != setup.get("project_id") or
            template.get("project_workspace") != setup.get("project_workspace")):
        raise CampaignError("frozen W24 candidate template/project identity differs from current readback")
    source_manifest = candidate.get("source_manifest")
    if not isinstance(source_manifest, Mapping):
        raise CampaignError("frozen W24 candidate omitted its source closure")
    verify_source_manifest(source_manifest)
    stress_components = map_stress_components(setup["equation_view_inventory"])
    if candidate.get("stress_components_from_complete_native_equation_view") != stress_components:
        raise CampaignError("candidate stress component map differs from the current complete Equation View file")
    expected_scope = {
        "owned_servers": 1,
        "max_seconds_from_exact_birth_including_cleanup": MAX_BIRTH_BUDGET_S,
        "max_study_run_submissions": MAX_STUDY_RUN_SUBMISSIONS,
        "max_sequential_workers": MAX_SEQUENTIAL_WORKERS,
        "cleanup_reserve_seconds": CLEANUP_RESERVE_S,
        "study_run_order": [slot.__dict__ for slot in SOLVE_PLAN],
        "export_calls": 0,
        "automatic_retries": 0,
    }
    if candidate.get("native_scope") != expected_scope:
        raise CampaignError("candidate native execution limits or ten-slot order differ from the reviewed runner")

    approval = _read_json(approval_path, "root W24 campaign approval")
    approval_scope = approval.get("native_scope")
    if (approval.get("schema") != "W24_ROOT_NATIVE_CAMPAIGN_APPROVAL_V1" or
            approval.get("status") != APPROVAL_STATUS or
            approval.get("candidate_id") != CANDIDATE_ID or
            approval.get("freeze_sha256") != actual_digest or
            approval_scope != expected_scope or
            approval.get("approval_authority") != "root" or
            approval.get("no_automatic_retry") is not True):
        raise CampaignError("root approval does not bind this exact frozen W24 candidate and limits")
    return candidate, approval


def read_solve_ledger(path: Path) -> list[dict[str, Any]]:
    """Read durable invocation attempts; planned slots are never counted."""
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as stream:
        for line_number, raw in enumerate(stream, 1):
            if not raw.strip():
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise CampaignError(f"malformed native solve ledger line {line_number}") from exc
            if not isinstance(row, dict) or row.get("event") != "study_run_submitted":
                raise CampaignError(f"unexpected native solve ledger event at line {line_number}")
            if row.get("submission_index") != len(rows) + 1:
                raise CampaignError(f"nonconsecutive native solve ledger index at line {line_number}")
            if row.get("case_id") not in {slot.case_id for slot in SOLVE_PLAN}:
                raise CampaignError(f"unknown native solve case at line {line_number}")
            if row.get("study_tag") not in {slot.study_tag for slot in SOLVE_PLAN}:
                raise CampaignError(f"unknown native study tag at line {line_number}")
            if not isinstance(row.get("at_utc"), str) or not row["at_utc"]:
                raise CampaignError(f"native solve ledger line {line_number} lacks invocation time")
            rows.append(row)
    if len(rows) > MAX_STUDY_RUN_SUBMISSIONS:
        raise CampaignError("native solve ledger exceeds the frozen ten-submission cap")
    for i, row in enumerate(rows):
        slot = SOLVE_PLAN[i]
        if (row.get("case_id"), row.get("study_tag")) != (slot.case_id, slot.study_tag):
            raise CampaignError(f"native solve submission {i+1} does not match the frozen order")
    return rows


def next_solve_slot(ledger: Iterable[Mapping[str, Any]]) -> SolveSlot | None:
    rows = list(ledger)
    if len(rows) >= len(SOLVE_PLAN):
        return None
    return SOLVE_PLAN[len(rows)]


_STRESS_ROLES = ("radial_normal", "hoop_normal", "axial_normal", "rz_shear")


def map_stress_components(equation_view_inventory: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    """Map only explicit COMSOL Expression/Unit/Description rows to stress roles.

    COMSOL documents each returned row as one item and supports an ``all``
    column option, but does not freeze column order in this API signature.
    Therefore expression identifiers and the Pa unit are located by shape,
    while physical component identity comes exclusively from native textual
    descriptions. An expression's spelling is retained and never interpreted.
    """
    inventory = validate_equation_view_inventory(equation_view_inventory)
    artifact = _read_json(Path(inventory["path"]), "native Equation View inventory")
    matches: dict[str, list[dict[str, str]]] = {role: [] for role in _STRESS_ROLES}
    for table in artifact["feature_tables"]:
        for row_index, row in enumerate(table["raw_rows"]):
            if not isinstance(row, list) or len(row) < 3:
                continue
            cells = [cell.strip() for cell in row if isinstance(cell, str) and cell.strip()]
            expression_cells = [cell for cell in cells
                                if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+", cell)]
            unit_cells = [cell for cell in cells if cell == "Pa"]
            if len(expression_cells) != 1 or len(unit_cells) != 1:
                continue
            expression = expression_cells[0]
            description = " ".join(cell for cell in cells
                                   if cell != expression and cell != "Pa")
            text = re.sub(r"\s+", " ", description.casefold()).strip()
            if "stress" not in text and "cauchy" not in text:
                continue
            has_shear = "shear" in text
            roles: list[str] = []
            if not has_shear and re.search(r"\bradial\b", text):
                roles.append("radial_normal")
            if not has_shear and re.search(r"\b(hoop|circumferential|azimuthal)\b", text):
                roles.append("hoop_normal")
            if not has_shear and re.search(r"\b(axial|longitudinal)\b", text):
                roles.append("axial_normal")
            if has_shear and re.search(r"\b(r\s*[-/]\s*z|rz|z\s*[-/]\s*r)\b", text):
                roles.append("rz_shear")
            for role in roles:
                matches[role].append({
                    "expression": expression,
                    "unit": "Pa",
                    "description": description,
                    "physics_tag": str(table["physics_tag"]),
                    "feature_tag": str(table["feature_tag"]),
                    "row_index": str(row_index),
                })
    ambiguous = {role: len(rows) for role, rows in matches.items() if len(rows) != 1}
    if ambiguous:
        raise CampaignError("native Equation View descriptions do not uniquely identify four Pa stress components: "
                            + json.dumps(ambiguous, sort_keys=True))
    mapped = {role: matches[role][0] for role in _STRESS_ROLES}
    expressions = [mapped[role]["expression"] for role in _STRESS_ROLES]
    if len(set(expressions)) != len(_STRESS_ROLES):
        raise CampaignError("native Equation View stress roles resolve to duplicate expressions")
    return mapped


def _read_exact(stream: Any, length: int, label: str) -> bytes:
    data = stream.read(length)
    if len(data) != length:
        raise CampaignError(f"truncated native DOF snapshot while reading {label}")
    return data


def _read_i32(stream: Any, label: str) -> int:
    return struct.unpack(">i", _read_exact(stream, 4, label))[0]


def _read_f64(stream: Any, label: str) -> float:
    value = struct.unpack(">d", _read_exact(stream, 8, label))[0]
    if not math.isfinite(value):
        raise CampaignError(f"native DOF snapshot contains a nonfinite {label}")
    return value


def _read_modified_utf(stream: Any, label: str) -> str:
    byte_count = struct.unpack(">H", _read_exact(stream, 2, label + " length"))[0]
    try:
        return _read_exact(stream, byte_count, label).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CampaignError(f"native DOF snapshot has invalid UTF-8 in {label}") from exc


def _iter_solution_snapshot_v3(stream: Any) -> Iterator[dict[str, Any]]:
    """Read V3 snapshots, including the full CompileEquations Xmesh layout.

    Xmesh metadata is hashed as its original binary header is read. The full
    element-local maps are validated and represented by that digest, avoiding
    retaining a second copy of potentially large element connectivity arrays.
    """
    layout_digest = hashlib.sha256()
    layout_digest.update(b"W24-DOF-SNAPSHOT-3\0")

    def read_header_exact(size: int, label: str) -> bytes:
        raw = _read_exact(stream, size, label)
        layout_digest.update(raw)
        return raw

    def read_header_i32(label: str) -> int:
        return struct.unpack(">i", read_header_exact(4, label))[0]

    def read_header_f64(label: str) -> float:
        value = struct.unpack(">d", read_header_exact(8, label))[0]
        if not math.isfinite(value):
            raise CampaignError(f"V3 Xmesh metadata contains nonfinite {label}")
        return value

    def read_header_utf(label: str) -> str:
        byte_count = struct.unpack(">H", read_header_exact(2, label + " length"))[0]
        try:
            return read_header_exact(byte_count, label).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CampaignError(f"V3 Xmesh metadata has invalid UTF-8 in {label}") from exc

    def bounded(raw: int, maximum: int, label: str, *, allow_minus_one: bool = False) -> int:
        minimum = -1 if allow_minus_one else 0
        if raw < minimum or raw > maximum:
            raise CampaignError(f"V3 Xmesh {label} is outside its supported bounds")
        return raw

    axes = read_header_i32("coordinate axis count")
    declared_dof_count = read_header_i32("XmeshInfo.nDofs")
    if axes not in (2, 3):
        raise CampaignError("V3 Xmesh snapshot has invalid coordinate-axis count")
    field_count = bounded(read_header_i32("field count"), 10000, "field count")
    field_names: list[str] = []
    field_ndofs: list[int] = []
    for index in range(field_count):
        field_names.append(read_header_utf(f"fieldNames[{index}]"))
        field_ndofs.append(read_header_i32(f"fieldNDofs[{index}]"))
    name_count = bounded(read_header_i32("dof name count"), 10000, "dof name count")
    names = [read_header_utf(f"dofNames[{index}]") for index in range(name_count)]
    dof_count = bounded(read_header_i32("Xmesh DOF row count"), 20_000_000, "Xmesh DOF row count")
    geom_nums: list[int] = []
    nodes: list[int] = []
    name_indices: list[int] = []
    vector_indices: list[int] = []
    coordinates: list[list[float]] = [[] for _ in range(axes)]
    for index in range(dof_count):
        geom_nums.append(read_header_i32(f"geomNums[{index}]"))
        nodes.append(read_header_i32(f"nodes[{index}]"))
        name_indices.append(read_header_i32(f"nameInds[{index}]"))
        vector_indices.append(read_header_i32(f"solVectorInds[{index}]"))
        for axis in range(axes):
            coordinates[axis].append(read_header_f64(f"coords[{axis}][{index}]"))

    geometry_count = bounded(read_header_i32("geometry count"), 1000, "geometry count")
    element_group_count = 0
    element_local_map_entries = 0
    invalid_element_dof_references = 0
    for geometry_index in range(geometry_count):
        read_header_utf(f"geometry[{geometry_index}]")
        mesh_type_count = bounded(read_header_i32(f"geometry[{geometry_index}] mesh-type count"),
                                  10000, "mesh-type count")
        for mesh_index in range(mesh_type_count):
            read_header_utf(f"meshType[{geometry_index}][{mesh_index}]")
            element_group_count += 1
            local_dof_count = bounded(read_header_i32("local DOF count"),
                                      20_000_000, "local DOF count")
            local_dof_axes = bounded(read_header_i32("local DOF coordinate axes"), 3,
                                      "local DOF coordinate axes", allow_minus_one=True)
            local_dof_names = [read_header_utf(f"localDofNames[{index}]")
                               for index in range(local_dof_count)]
            if local_dof_axes >= 0:
                for axis in range(local_dof_axes):
                    for local_index in range(local_dof_count):
                        read_header_f64(f"localDofCoords[{axis}][{local_index}]")

            local_node_axes = bounded(read_header_i32("local node coordinate axes"), 3,
                                      "local node coordinate axes", allow_minus_one=True)
            local_node_count = bounded(read_header_i32("local node count"),
                                       20_000_000, "local node count")
            if local_node_axes == -1 and local_node_count != 0:
                raise CampaignError("V3 local node coordinates are absent but a nonzero node count was declared")
            if local_node_axes >= 0:
                for axis in range(local_node_axes):
                    for node_index in range(local_node_count):
                        read_header_f64(f"localCoords[{axis}][{node_index}]")

            element_count = bounded(read_header_i32("element count"), 20_000_000, "element count")
            node_row_count = bounded(read_header_i32("element node row count"),
                                     20_000_000, "element node row count")
            if node_row_count * element_count > 100_000_000:
                raise CampaignError("V3 element node map exceeds the bounded decoder size")
            for row in range(node_row_count):
                for element in range(element_count):
                    read_header_i32(f"elementNodes[{row}][{element}]")
            dof_row_count = bounded(read_header_i32("element DOF row count"),
                                    20_000_000, "element DOF row count")
            if dof_row_count * element_count > 100_000_000:
                raise CampaignError("V3 element DOF map exceeds the bounded decoder size")
            if dof_row_count != local_dof_count:
                raise CampaignError("V3 localDofNames and element DOF map row counts differ")
            for row in range(dof_row_count):
                for element in range(element_count):
                    dof_index = read_header_i32(f"elementDofs[{row}][{element}]")
                    element_local_map_entries += 1
                    if dof_index < -1 or dof_index >= dof_count:
                        invalid_element_dof_references += 1

    layout_sha256 = layout_digest.hexdigest()
    time_count = bounded(_read_i32(stream, "stored time count"), 10_000_000, "stored time count")
    if time_count <= 0:
        raise CampaignError("V3 snapshot has no native stored solutions")
    prior_time: float | None = None
    vector_length: int | None = None
    vector_summary: dict[str, Any] | None = None
    metadata = {
        "snapshot_schema": "W24-DOF-SNAPSHOT-3",
        "coordinate_axes": axes,
        "xmesh_n_dofs": declared_dof_count,
        "fieldNames": field_names,
        "fieldNDofs": field_ndofs,
        "field_ndofs_sum": sum(field_ndofs),
        "dofNames": names,
        "geomNums": geom_nums,
        "nodes": nodes,
        "nameInds": name_indices,
        "solVectorInds": vector_indices,
        "coords": coordinates,
        "layout_sha256": layout_sha256,
        "element_local_map_group_count": element_group_count,
        "element_local_map_entries": element_local_map_entries,
        "invalid_element_dof_references": invalid_element_dof_references,
    }
    for frame_index in range(time_count):
        time_s = _read_f64(stream, f"V3 time[{frame_index}]")
        if prior_time is not None and time_s <= prior_time:
            raise CampaignError("V3 stored times are nonfinite or not strictly increasing")
        frame_vector_length = bounded(_read_i32(stream, f"V3 solution vector length[{frame_index}]"),
                                      100_000_000, "solution vector length")
        if frame_vector_length <= 0:
            raise CampaignError("V3 native solution vector is empty")
        values: list[float] = []
        for index in range(frame_vector_length):
            values.append(_read_f64(stream, f"V3 u_real[{frame_index}][{index}]"))
        if vector_length is None:
            vector_length = frame_vector_length
            seen = bytearray(vector_length)
            unmapped_rows = 0
            invalid_indices = 0
            duplicate_index_rows = 0
            unique_mapped_indices = 0
            out_of_range_indices = 0
            for vector_index in vector_indices:
                if vector_index == -1:
                    unmapped_rows += 1
                elif vector_index < -1:
                    invalid_indices += 1
                elif vector_index >= vector_length:
                    out_of_range_indices += 1
                elif seen[vector_index]:
                    duplicate_index_rows += 1
                else:
                    seen[vector_index] = 1
                    unique_mapped_indices += 1
            unrepresented = vector_length - unique_mapped_indices
            field_counts_valid = (field_count > 0 and len(field_names) == len(field_ndofs) and
                                  all(count >= 0 for count in field_ndofs) and
                                  sum(field_ndofs) == declared_dof_count == dof_count)
            name_indices_valid = all(0 <= index < len(names) for index in name_indices)
            complete = (field_counts_valid and name_indices_valid and unmapped_rows == 0 and
                        invalid_indices == 0 and out_of_range_indices == 0 and
                        unrepresented == 0 and invalid_element_dof_references == 0 and
                        element_group_count > 0 and element_local_map_entries > 0)
            vector_summary = {
                "vector_length": vector_length,
                "mapped_dof_rows": sum(0 <= index < vector_length for index in vector_indices),
                "unmapped_dof_rows": unmapped_rows,
                "invalid_solution_indices": invalid_indices,
                "out_of_range_solution_indices": out_of_range_indices,
                "duplicate_solution_vector_index_rows": duplicate_index_rows,
                "unique_mapped_vector_indices": unique_mapped_indices,
                "unrepresented_solution_vector_indices": unrepresented,
                "field_counts_valid": field_counts_valid,
                "name_indices_valid": name_indices_valid,
                "full_vector_and_internal_dof_map_covered": complete,
            }
            metadata["mapping_summary"] = vector_summary
            metadata["complete_xmesh_internal_dof_capture"] = complete
            metadata["complete_xmesh_dofs"] = complete
        elif frame_vector_length != vector_length:
            raise CampaignError("V3 solution-vector length changed across accepted stored times")
        yield {"time_s": time_s, "dofs": metadata, "u_real": values,
               "u_imag": [0.0] * frame_vector_length,
               "frame_index": frame_index, "frame_count": time_count}
        prior_time = time_s
    if stream.read(1):
        raise CampaignError("V3 snapshot contains trailing bytes after the declared solution vectors")
    if vector_summary is None:
        raise CampaignError("V3 snapshot omitted its native solution vector mapping")


def iter_solution_snapshot(path: Path) -> Iterator[dict[str, Any]]:
    """Stream complete Java W24 DOF snapshots (legacy 2D V1 or full 3D V2).

    The binary format is written by DataOutputStream in big-endian order. The
    iterator checks every index/vector, coordinate, finite solution value,
    duplicate exact DOF identity, and the complete accepted-time tail without
    materializing all accepted solutions at once.
    """
    try:
        snapshot = gzip.open(path, "rb")
    except OSError as exc:
        raise CampaignError(f"cannot open compressed native DOF snapshot: {exc}") from exc
    with snapshot as stream:
        magic = _read_modified_utf(stream, "snapshot magic")
        if magic == "W24-DOF-SNAPSHOT-3":
            yield from _iter_solution_snapshot_v3(stream)
            return
        if magic not in {"W24-DOF-SNAPSHOT-1", "W24-DOF-SNAPSHOT-2"}:
            raise CampaignError(f"unsupported native DOF snapshot schema: {magic!r}")
        axes = _read_i32(stream, "coordinate axis count")
        name_count = _read_i32(stream, "dof name count")
        valid_axes = (axes == 2) if magic == "W24-DOF-SNAPSHOT-1" else axes in (2, 3)
        if not valid_axes or name_count <= 0 or name_count > 10000:
            raise CampaignError("native DOF snapshot has an invalid coordinate/name dimension")
        names = [_read_modified_utf(stream, f"dofNames[{i}]") for i in range(name_count)]
        count = _read_i32(stream, "DOF count")
        if count <= 0 or count > 20_000_000:
            raise CampaignError("native DOF snapshot has an invalid DOF count")
        geom_nums: list[int] = []
        nodes: list[int] = []
        name_indices: list[int] = []
        vector_indices: list[int] = []
        coordinates: list[tuple[float, ...]] = []
        exact_keys: set[tuple[Any, ...]] = set()
        for i in range(count):
            geom_num = _read_i32(stream, f"geomNums[{i}]")
            node = _read_i32(stream, f"nodes[{i}]")
            name_index = _read_i32(stream, f"nameInds[{i}]")
            vector_index = _read_i32(stream, f"solVectorInds[{i}]")
            if not 0 <= name_index < len(names) or vector_index < 0:
                raise CampaignError(f"native DOF snapshot index is out of bounds at row {i}")
            point = tuple(_read_f64(stream, f"coords[{axis}][{i}]") for axis in range(axes))
            key = (geom_num, node, *(axis.hex() for axis in point), names[name_index], name_index)
            if key in exact_keys:
                raise CampaignError(f"native DOF snapshot contains duplicate exact key at row {i}")
            exact_keys.add(key)
            geom_nums.append(geom_num)
            nodes.append(node)
            name_indices.append(name_index)
            vector_indices.append(vector_index)
            coordinates.append(point)
        max_vector_index = max(vector_indices)
        time_count = _read_i32(stream, "stored time count")
        if time_count <= 0 or time_count > 10_000_000:
            raise CampaignError("native DOF snapshot has an invalid accepted-time count")
        metadata = {
            "dofNames": names,
            "geomNums": geom_nums,
            "nodes": nodes,
            "nameInds": name_indices,
            "solVectorInds": vector_indices,
            "coords": [[row[axis] for row in coordinates] for axis in range(axes)],
            "snapshot_schema": magic,
            "coordinate_axes": axes,
            "complete_xmesh_dofs": magic == "W24-DOF-SNAPSHOT-2",
        }
        prior_time: float | None = None
        for frame_index in range(time_count):
            time_s = _read_f64(stream, f"time[{frame_index}]")
            if prior_time is not None and time_s <= prior_time:
                raise CampaignError("native DOF snapshot times are not strictly increasing")
            vector_size = _read_i32(stream, f"solution-vector length[{frame_index}]")
            if vector_size <= 0 or max_vector_index >= vector_size:
                raise CampaignError("native DOF snapshot vector does not cover the exact DOF mapping")
            values = [_read_f64(stream, f"u_real[{frame_index}][{i}]")
                      for i in range(vector_size)]
            yield {"time_s": time_s, "dofs": metadata, "u_real": values,
                   # The native Java writer first checks isRealU(solnum, "Sol")
                   # for every accepted solution and serializes only real vectors.
                   # A zero imaginary component is therefore an exact encoding
                   # consequence of this explicitly real-only snapshot format.
                   "u_imag": [0.0] * vector_size,
                   "frame_index": frame_index, "frame_count": time_count}
            prior_time = time_s
        if stream.read(1):
            raise CampaignError("native DOF snapshot contains trailing bytes after the declared frames")


def _verify_native_artifact(receipt: Any, evidence: Path, label: str) -> tuple[Path, dict[str, Any]]:
    row = _as_mapping(receipt, label)
    path_text = row.get("path")
    size = row.get("size_bytes")
    digest = row.get("sha256")
    if (not isinstance(path_text, str) or isinstance(size, bool) or not isinstance(size, int) or
            size <= 0 or not isinstance(digest, str) or not SHA256_RE.fullmatch(digest)):
        raise CampaignError(f"{label} lacks an explicit path/size/SHA-256 receipt")
    path = Path(path_text).expanduser()
    try:
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise CampaignError(f"{label} artifact is unavailable: {exc}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise CampaignError(f"{label} artifact must be a regular non-symlink file")
    root = evidence.resolve(strict=True)
    if not resolved.is_relative_to(root):
        raise CampaignError(f"{label} artifact is outside the current W24 evidence directory")
    if resolved.stat().st_size != size or sha256(resolved) != digest:
        raise CampaignError(f"{label} artifact bytes differ from their native receipt")
    return resolved, dict(row)


def verify_native_metrics_receipt(receipt: Any, evidence: Path, *,
                                  expected_times: Sequence[float],
                                  stress_components: Mapping[str, Mapping[str, str]]) -> dict[str, Any]:
    """Check native metric bytes, dimensions, units, and exact accepted times."""
    path, artifact_receipt = _verify_native_artifact(receipt, evidence, "native_metrics")
    artifact = _read_json(path, "native W24 metric artifact")
    if (artifact_receipt.get("status") != "NATIVE_CURE_METRICS_CAPTURED" or
            artifact.get("schema") != "W24_NATIVE_CURE_METRICS_V1" or
            artifact.get("status") != "NATIVE_CURE_METRICS_CAPTURED" or
            artifact.get("native") is not True or artifact.get("native_study_run_calls") != 0 or
            artifact_receipt.get("temporary_dataset_removed") is not True):
        raise CampaignError("native metric artifact lacks its COMSOL capture/zero-extra-solve contract")
    removed_tags = artifact_receipt.get("temporary_numerical_tags_removed")
    if (not isinstance(removed_tags, list) or len(removed_tags) != 10 or
            len(set(removed_tags)) != len(removed_tags) or
            any(not isinstance(tag, str) or not tag.startswith(("w24n", "w24i")) for tag in removed_tags)):
        raise CampaignError("native metric capture did not remove all ten temporary COMSOL numerical features")
    times = artifact.get("stored_times_s")
    if not isinstance(times, list) or times != list(expected_times) or len(times) < 2:
        raise CampaignError("native metric time vector differs from the exact field snapshot accepted times")
    for index, time_s in enumerate(times):
        if isinstance(time_s, bool) or not isinstance(time_s, (int, float)) or not math.isfinite(float(time_s)):
            raise CampaignError(f"native metric stored time {index} is not finite")
        if index and float(time_s) <= float(times[index - 1]):
            raise CampaignError("native metric accepted times are not strictly increasing")

    def finite_vector(value: Any, label: str, length: int) -> list[float]:
        if not isinstance(value, list) or len(value) != length:
            raise CampaignError(f"{label} must contain one native value for every accepted time")
        result: list[float] = []
        for index, item in enumerate(value):
            if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(float(item)):
                raise CampaignError(f"{label}[{index}] is not finite")
            result.append(float(item))
        return result

    for key in ("alpha_iso_mean", "alpha_mean", "qpost_mean"):
        finite_vector(artifact.get(key), key, len(times))
    energy = artifact.get("energy_samples")
    if not isinstance(energy, list) or len(energy) != len(times):
        raise CampaignError("native energy integrals must cover each accepted solution time exactly once")
    for index, (time_s, row) in enumerate(zip(times, energy)):
        if not isinstance(row, Mapping) or row.get("time_s") != time_s:
            raise CampaignError(f"native energy sample {index} is not bound to its accepted time")
        if row.get("outward_sign") != "positive_outward":
            raise CampaignError("native outward heat integral does not declare the frozen positive-outward sign")
        for key in ("stored_energy_j", "reaction_power_w", "outward_power_w"):
            item = row.get(key)
            if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(float(item)):
                raise CampaignError(f"native energy sample {index}.{key} is invalid")
        domain_energy = row.get("stored_energy_by_domain_j")
        if not isinstance(domain_energy, Mapping) or set(domain_energy) != {"alumina", "gold", "adhesive", "fiber"}:
            raise CampaignError(f"native energy sample {index} lacks all four material-domain contributions")
        for label, item in domain_energy.items():
            if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(float(item)):
                raise CampaignError(f"native energy sample {index}.{label} is invalid")

    if set(stress_components) != set(STRESS_ROLES):
        raise CampaignError("metric verification requires exactly the four frozen stress component roles")
    descriptors = artifact.get("stress_component_descriptors")
    if not isinstance(descriptors, Mapping) or set(descriptors) != set(STRESS_ROLES):
        raise CampaignError("native metric artifact omitted a mapped stress component descriptor")
    mean_stress = artifact.get("adhesive_mean_stress_pa")
    probes = artifact.get("stress_probe_values_pa")
    if not isinstance(mean_stress, Mapping) or set(mean_stress) != set(STRESS_ROLES):
        raise CampaignError("native metric artifact omitted adhesive mean stress components")
    if not isinstance(probes, Mapping) or set(probes) != set(STRESS_ROLES):
        raise CampaignError("native metric artifact omitted all three native stress probe histories")
    for role in STRESS_ROLES:
        expected = dict(stress_components[role])
        actual = descriptors.get(role)
        if expected.get("unit") != "Pa" or not expected.get("description") or not expected.get("expression"):
            raise CampaignError(f"frozen stress mapping {role} is incomplete")
        if actual != expected:
            raise CampaignError(f"native metric stress mapping changed for {role}")
        finite_vector(mean_stress.get(role), f"adhesive_mean_stress_pa.{role}", len(times))
        role_probes = probes.get(role)
        if not isinstance(role_probes, list) or len(role_probes) != len(times):
            raise CampaignError(f"native stress probe {role} does not cover every accepted time")
        for time_index, row in enumerate(role_probes):
            if not isinstance(row, list) or len(row) != len(STRESS_PROBE_COORDINATES_M):
                raise CampaignError(f"native stress probe {role}[{time_index}] has the wrong point count")
            for point_index, item in enumerate(row):
                if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(float(item)):
                    raise CampaignError(f"native stress probe {role}[{time_index}][{point_index}] is invalid")
    if artifact.get("stress_probe_coordinates_m") != [list(point) for point in STRESS_PROBE_COORDINATES_M]:
        raise CampaignError("native stress probe coordinates differ from the frozen three interior points")
    if artifact.get("axisymmetric_physical_measure_readback") != "intvolume=on; intsurface=on":
        raise CampaignError("native axisymmetric numerical operators lack their explicit physical measure flags")

    readbacks = artifact.get("feature_readbacks")
    if not isinstance(readbacks, Mapping):
        raise CampaignError("native metric artifact omitted numerical feature readbacks")
    dataset = artifact.get("dataset_tag")

    def require_feature(key: str, *, feature_type: str, units: Sequence[str],
                        dimension: int | None, axisymmetric: str | None,
                        entities_count: int | None = None,
                        expressions: Sequence[str] | None = None,
                        shape: Sequence[int] | None = None) -> Mapping[str, Any]:
        row = readbacks.get(key)
        if (not isinstance(row, Mapping) or row.get("type") != feature_type or
                row.get("dataset") != dataset or row.get("unit") != list(units) or
                row.get("solution_selection") != "all"):
            raise CampaignError(f"native numerical feature {key} lacks exact type/dataset/unit/time readback")
        if not isinstance(dataset, str) or not dataset:
            raise CampaignError("native metric dataset tag is missing")
        if expressions is not None and row.get("expression") != list(expressions):
            raise CampaignError(f"native numerical feature {key} expressions differ from the frozen mapping")
        if shape is not None and row.get("shape") != list(shape):
            raise CampaignError(f"native numerical feature {key} result shape differs from accepted-time contract")
        if dimension is not None and row.get("entity_dimension") != dimension:
            raise CampaignError(f"native numerical feature {key} has the wrong geometric entity dimension")
        if dimension is not None:
            ids = row.get("entity_ids")
            if (row.get("geometry") != "geom1" or not isinstance(ids, list) or
                    (entities_count is not None and len(ids) != entities_count) or
                    len(ids) != len(set(ids)) or any(type(entity_id) is not int for entity_id in ids)):
                raise CampaignError(f"native numerical feature {key} has an invalid dimension-bound selection readback")
        if axisymmetric is not None:
            flag = "intvolume" if dimension == 2 else "intsurface"
            if (row.get("axisymmetric_measure_property") != flag or
                    row.get("axisymmetric_measure_value") != axisymmetric):
                raise CampaignError(f"native numerical feature {key} lacks its axisymmetric physical measure flag")
        return row

    require_feature("adhesive_cure_averages", feature_type="AvVolume", units=("1", "1", "1"),
                    dimension=2, axisymmetric="on", entities_count=1,
                    expressions=("alpha_iso", "alpha", "qpost"), shape=(3, len(times)))
    for label in ("alumina", "gold", "adhesive", "fiber"):
        require_feature(f"energy_{label}", feature_type="IntVolume", units=("J",),
                        dimension=2, axisymmetric="on", entities_count=1, shape=(1, len(times)))
    require_feature("reaction_power", feature_type="IntVolume", units=("W",),
                    dimension=2, axisymmetric="on", entities_count=1,
                    expressions=("Qrxn",), shape=(1, len(times)))
    require_feature("outward_power", feature_type="IntSurface", units=("W",),
                    dimension=1, axisymmetric="on", entities_count=8,
                    expressions=("hconv*(T-Tenv)",), shape=(1, len(times)))
    expected_stress_expressions = tuple(stress_components[role]["expression"] for role in STRESS_ROLES)
    require_feature("adhesive_mean_stress", feature_type="AvVolume",
                    units=tuple("Pa" for _ in STRESS_ROLES), dimension=2, axisymmetric="on",
                    entities_count=1, expressions=expected_stress_expressions,
                    shape=(4, len(times)))
    probe_readback = require_feature("stress_probes", feature_type="Interp",
                                     units=tuple("Pa" for _ in STRESS_ROLES),
                                     dimension=None, axisymmetric=None,
                                     expressions=expected_stress_expressions,
                                     shape=(4, len(times), 3))
    if probe_readback.get("coordinate_error") != "on" or probe_readback.get("math_error") != "on":
        raise CampaignError("native stress interpolation did not enable coordinate and expression errors")
    fiber_row = artifact.get("fiber_end_displacement_m")
    if not isinstance(fiber_row, list) or len(fiber_row) != len(times):
        raise CampaignError("native fiber-end displacement does not cover every accepted time")
    for index, values in enumerate(fiber_row):
        if (not isinstance(values, list) or len(values) != 2 or
                any(isinstance(value, bool) or not isinstance(value, (int, float)) or
                    not math.isfinite(float(value)) for value in values)):
            raise CampaignError(f"native fiber-end displacement sample {index} must contain finite u/w meters")
    fiber_readback = require_feature("fiber_end_displacement", feature_type="Interp",
                                     units=("m", "m"), dimension=None, axisymmetric=None,
                                     expressions=("u", "w"), shape=(2, len(times), 1))
    if (fiber_readback.get("coordinate_error") != "on" or
            fiber_readback.get("math_error") != "on" or
            fiber_readback.get("coordinates_m") != [[62.5e-6, 1050e-6]]):
        raise CampaignError("native fiber-end displacement interpolation differs from the frozen coordinate/readback")
    expected_thermal = [
        ("alumina", "rhoAl", "3900", "880"), ("gold", "rhoAu", "19300", "129"),
        ("adhesive", "rhoAdh", "1200", "1000"), ("fiber", "rhoFiber", "2200", "703"),
    ]
    thermal = artifact.get("material_heat_capacity_readback")
    if not isinstance(thermal, list) or len(thermal) != len(expected_thermal):
        raise CampaignError("energy integrals are not bound to all four native density/heat-capacity readbacks")
    for index, (row, expected) in enumerate(zip(thermal, expected_thermal)):
        if (not isinstance(row, list) or len(row) != 4 or row[0] != expected[0] or
                row[3] != expected[1] or not isinstance(row[1], str) or not isinstance(row[2], str) or
                not row[1].replace(" ", "").startswith(expected[2]) or
                not row[2].replace(" ", "").startswith(expected[3])):
            raise CampaignError(f"native material density/Cp readback differs from the frozen baseline at row {index}")
    return {"receipt": artifact_receipt, "artifact": artifact,
            "stored_time_count": len(times), "stored_times_s": list(times)}


def verify_native_mechanics_receipt(receipt: Any, evidence: Path, *,
                                    case_id: str,
                                    stress_components: Mapping[str, Mapping[str, str]]) -> dict[str, Any]:
    """Verify one stationary mechanics metrics artifact and every native readback."""
    path, artifact_receipt = _verify_native_artifact(receipt, evidence, "native_mechanics_metrics")
    artifact = _read_json(path, "native W24 mechanics metric artifact")
    if (case_id not in {"mechanics_free_expansion", "mechanics_fully_fixed"} or
            artifact_receipt.get("status") != "NATIVE_MECHANICS_METRICS_CAPTURED" or
            artifact.get("schema") != "W24_NATIVE_MECHANICS_METRICS_V1" or
            artifact.get("status") != "NATIVE_MECHANICS_METRICS_CAPTURED" or
            artifact.get("native") is not True or artifact.get("native_study_run_calls") != 0 or
            artifact.get("case_id") != case_id or
            artifact_receipt.get("case_id") != case_id or
            artifact_receipt.get("temporary_dataset_removed") is not True):
        raise CampaignError("native mechanics artifact lacks its exact benchmark/cleanup/zero-extra-solve contract")
    removed = artifact_receipt.get("temporary_numerical_tags_removed")
    if (not isinstance(removed, list) or len(removed) != 4 or len(set(removed)) != 4 or
            any(not isinstance(tag, str) or not tag.startswith(("w24m", "w24mi")) for tag in removed)):
        raise CampaignError("native mechanics capture did not remove its four temporary numerical features")
    times = artifact.get("stored_times_s")
    if not isinstance(times, list) or len(times) != 1:
        raise CampaignError("mechanics metrics must contain exactly one stationary solution time")
    for time_s in times:
        if isinstance(time_s, bool) or not isinstance(time_s, (int, float)) or not math.isfinite(float(time_s)):
            raise CampaignError("mechanics solution parameter/time is not finite")
    if set(stress_components) != set(STRESS_ROLES):
        raise CampaignError("mechanics verification requires the four frozen stress component roles")
    descriptors = artifact.get("stress_component_descriptors")
    if not isinstance(descriptors, Mapping) or set(descriptors) != set(STRESS_ROLES):
        raise CampaignError("mechanics metric artifact omitted a mapped stress descriptor")
    for role in STRESS_ROLES:
        expected = dict(stress_components[role])
        actual = descriptors.get(role)
        if (expected.get("unit") != "Pa" or not expected.get("expression") or
                not expected.get("description") or actual != expected):
            raise CampaignError(f"native mechanics stress descriptor changed or is incomplete for {role}")

    def finite_role_map(value: Any, label: str) -> dict[str, float]:
        if not isinstance(value, Mapping) or set(value) != set(STRESS_ROLES):
            raise CampaignError(f"{label} must contain exactly the four mapped stress roles")
        out: dict[str, float] = {}
        for role in STRESS_ROLES:
            item = value[role]
            if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(float(item)):
                raise CampaignError(f"{label}.{role} is not a finite native value")
            out[role] = float(item)
        return out

    means = finite_role_map(artifact.get("stress_mean_pa"), "stress_mean_pa")
    maxima = finite_role_map(artifact.get("stress_abs_max_pa"), "stress_abs_max_pa")
    displacement_max = artifact.get("displacement_abs_max_m")
    displacement_probe = artifact.get("displacement_probe_m")
    if not isinstance(displacement_max, list) or len(displacement_max) != 2:
        raise CampaignError("mechanics native absolute displacement maxima must contain radial and axial values")
    if not isinstance(displacement_probe, list) or len(displacement_probe) != 2:
        raise CampaignError("mechanics native corner displacement probe must contain radial and axial values")
    for label, values in (("displacement_abs_max_m", displacement_max),
                          ("displacement_probe_m", displacement_probe)):
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) or
               not math.isfinite(float(value)) for value in values):
            raise CampaignError(f"{label} contains a nonfinite native result")
    if artifact.get("displacement_probe_coordinate_m") != [100e-6, 200e-6]:
        raise CampaignError("mechanics displacement probe differs from the frozen cylinder corner")

    readbacks = artifact.get("feature_readbacks")
    if not isinstance(readbacks, Mapping):
        raise CampaignError("mechanics artifact omitted the native numerical-feature readbacks")
    dataset = artifact.get("dataset_tag")

    def require_feature(key: str, *, kind: str, units: Sequence[str],
                        expressions: Sequence[str], shape: Sequence[int],
                        dimension: int | None) -> Mapping[str, Any]:
        row = readbacks.get(key)
        if (not isinstance(row, Mapping) or row.get("type") != kind or
                row.get("dataset") != dataset or row.get("unit") != list(units) or
                row.get("expression") != list(expressions) or
                row.get("solution_selection") != "all" or row.get("shape") != list(shape)):
            raise CampaignError(f"native mechanics feature {key} has an incomplete type/unit/expression/shape readback")
        if dimension is not None:
            ids = row.get("entity_ids")
            if (row.get("geometry") != "geom1" or row.get("entity_dimension") != dimension or
                    not isinstance(ids, list) or len(ids) != 1 or len(set(ids)) != 1 or
                    any(type(entity_id) is not int for entity_id in ids)):
                raise CampaignError(f"native mechanics feature {key} has an invalid selected-domain readback")
        return row

    expected_stress = [stress_components[role]["expression"] for role in STRESS_ROLES]
    mean_row = require_feature("stress_mean", kind="AvVolume", units=("Pa",) * 4,
                               expressions=expected_stress, shape=(4, 1), dimension=2)
    if (mean_row.get("axisymmetric_measure_property") != "intvolume" or
            mean_row.get("axisymmetric_measure_value") != "on"):
        raise CampaignError("native mechanics volume stress mean lacks axisymmetric physical weighting")
    require_feature("stress_abs_max", kind="MaxVolume", units=("Pa",) * 4,
                    expressions=tuple(f"abs({expr})" for expr in expected_stress),
                    shape=(4, 1), dimension=2)
    require_feature("displacement_abs_max", kind="MaxVolume", units=("m", "m"),
                    expressions=("abs(u)", "abs(w)"), shape=(2, 1), dimension=2)
    interp = readbacks.get("displacement_probe")
    if (not isinstance(interp, Mapping) or interp.get("type") != "Interp" or
            interp.get("dataset") != dataset or interp.get("expression") != ["u", "w"] or
            interp.get("unit") != ["m", "m"] or interp.get("solution_selection") != "all" or
            interp.get("coordinate_error") != "on" or interp.get("math_error") != "on" or
            interp.get("coordinates_m") != [[100e-6, 200e-6]] or
            interp.get("shape") != [2, 1, 1]):
        raise CampaignError("native mechanics corner displacement interpolation readback is incomplete")
    return {"receipt": artifact_receipt, "artifact": artifact,
            "stored_time_count": 1, "stress_mean_pa": means,
            "stress_abs_max_pa": maxima,
            "displacement_abs_max_m": [float(x) for x in displacement_max],
            "displacement_probe_m": [float(x) for x in displacement_probe]}


def verify_capture_receipt(capture: Mapping[str, Any], evidence: Path, *,
                           requires_energy: bool,
                           stress_components: Mapping[str, Mapping[str, str]] | None = None,
                           mechanics_case_id: str | None = None) -> dict[str, Any]:
    """Verify native DOFs and, for cure cases, every accepted-time metric artifact."""
    if capture.get("status") != "NATIVE_RAW_SNAPSHOT_CAPTURED":
        raise CampaignError("case capture did not return an explicit native raw-snapshot status")
    field_path, field_receipt = _verify_native_artifact(capture.get("field_snapshot"), evidence,
                                                        "field_snapshot")
    if field_receipt.get("real_solution") is not True:
        raise CampaignError("native field snapshot is not explicitly verified as a real COMSOL solution")
    frames = iter_solution_snapshot(field_path)
    stored_times: list[float] = []
    try:
        for frame in frames:
            stored_times.append(frame["time_s"])
    except CampaignError:
        raise
    if not stored_times:
        raise CampaignError("native raw field snapshot has no accepted-time frames")
    if capture.get("stored_times_s") != stored_times:
        raise CampaignError("native field receipt time vector differs from its full raw DOF snapshot")
    metric_receipt = None
    mechanics_receipt = None
    energy_samples = None
    if requires_energy:
        if stress_components is None:
            raise CampaignError("cure capture verification requires its frozen native stress descriptor map")
        metrics = verify_native_metrics_receipt(capture.get("native_metrics"), evidence,
                                                expected_times=stored_times,
                                                stress_components=stress_components)
        metric_receipt = metrics["receipt"]
        energy_samples = metrics["artifact"]["energy_samples"]
    if mechanics_case_id is not None:
        if stress_components is None:
            raise CampaignError("mechanics capture verification requires its frozen stress descriptor map")
        mechanics = verify_native_mechanics_receipt(
            capture.get("mechanics_metrics"), evidence, case_id=mechanics_case_id,
            stress_components=stress_components)
        mechanics_receipt = mechanics["receipt"]
    return {"field_snapshot": field_receipt, "native_metrics": metric_receipt,
            "mechanics_metrics": mechanics_receipt,
            "energy_samples": energy_samples,
            "stored_time_count": len(stored_times), "stored_times_s": stored_times}


def _metrics_artifact(capture: Mapping[str, Any], evidence: Path, *,
                      requires_energy: bool,
                      stress_components: Mapping[str, Mapping[str, str]],
                      mechanics_case_id: str | None = None) -> dict[str, Any]:
    """Re-verify a native capture and return its hash-bound numerical artifact."""
    verify_capture_receipt(
        capture, evidence, requires_energy=requires_energy,
        stress_components=stress_components,
        mechanics_case_id=mechanics_case_id)
    receipt_key = "mechanics_metrics" if mechanics_case_id is not None else "native_metrics"
    row = capture.get(receipt_key)
    path_text = row.get("path") if isinstance(row, Mapping) else None
    if not isinstance(path_text, str):
        raise CampaignError(f"native {receipt_key} receipt has no durable artifact path")
    return _read_json(Path(path_text), f"native {receipt_key} artifact")


def _requested_times(study_tag: str, *, max_step_s: float) -> list[float]:
    if study_tag == "stdUV":
        return [float(value) for value in range(0, 121)]
    if study_tag == "stdBake":
        return [float(value) for value in range(120, 961, 10)]
    if study_tag == "stdCool":
        return [float(value) for value in range(960, 1501, 10)]
    if study_tag == "stdCont":
        early = 0.5 if max_step_s == 0.5 else 1.0
        count = int(round(120.0 / early))
        return [i * early for i in range(count + 1)] + [float(value) for value in range(130, 1501, 10)]
    raise CampaignError(f"no frozen W24 output-time schedule exists for {study_tag}")


def _read_metrics_for_capture(capture: Mapping[str, Any], evidence: Path, *,
                              stress_components: Mapping[str, Mapping[str, str]]) -> dict[str, Any]:
    path, _ = _verify_native_artifact(capture.get("native_metrics"), evidence, "native_metrics")
    return _read_json(path, "native W24 cure metrics")


def _solution_frames(capture: Mapping[str, Any], evidence: Path) -> list[dict[str, Any]]:
    path, receipt = _verify_native_artifact(capture.get("field_snapshot"), evidence, "field_snapshot")
    if receipt.get("real_solution") is not True:
        raise CampaignError("field snapshot lacks the native real-solution gate")
    frames = list(iter_solution_snapshot(path))
    if not frames or [row["time_s"] for row in frames] != capture.get("stored_times_s"):
        raise CampaignError("raw native DOF frames differ from their stored-time receipt")
    return frames


def _exact_time_row(times: Sequence[float], target: float, label: str) -> int:
    matches = [index for index, value in enumerate(times)
               if abs(float(value) - float(target)) <= TIME_TOLERANCE_S]
    if len(matches) != 1:
        raise CampaignError(f"{label} has {len(matches)} exact stored matches for t={target:g} s")
    return matches[0]


def _field_values(frame: Mapping[str, Any], dof_name: str) -> dict[tuple[Any, ...], float]:
    from tools.w24_science_acceptance import dof_value_map

    values = {key: row[1] for key, row in dof_value_map(frame).items()
              if row[0] == dof_name}
    if not values:
        raise CampaignError(f"native XmeshInfoDofs mapping contains no exact field {dof_name!r}")
    return values


def _stage_handoff(source_capture: Mapping[str, Any], target_capture: Mapping[str, Any], *,
                   source_study: str, target_study: str, boundary_s: float,
                   evidence: Path, stress_components: Mapping[str, Mapping[str, str]]) -> dict[str, Any]:
    from tools.w24_science_acceptance import validate_boundary_jump

    source_frames = _solution_frames(source_capture, evidence)
    target_frames = _solution_frames(target_capture, evidence)
    source_times = [row["time_s"] for row in source_frames]
    target_times = [row["time_s"] for row in target_frames]
    source_index = _exact_time_row(source_times, boundary_s, f"{source_study} source")
    target_index = _exact_time_row(target_times, boundary_s, f"{target_study} target")
    source_frame, target_frame = source_frames[source_index], target_frames[target_index]
    jumps: dict[str, float] = {}
    for label, name in (("temperature_k", "comp1.T"), ("alpha", "comp1.alpha"),
                        ("qpost", "comp1.qpost"), ("displacement_r_m", "comp1.u"),
                        ("displacement_z_m", "comp1.w")):
        source_values = _field_values(source_frame, name)
        target_values = _field_values(target_frame, name)
        if set(source_values) != set(target_values):
            raise CampaignError(f"stage handoff exact DOF keys differ for {name}")
        jumps[label] = max(abs(target_values[key] - source_values[key])
                           for key in source_values)
    limits = {"temperature_k": 1e-4, "alpha": 1e-5, "qpost": 1e-5,
              "displacement_r_m": 1e-10, "displacement_z_m": 1e-10}
    checked = validate_boundary_jump(jumps, limits=limits)

    source_metrics = _read_metrics_for_capture(source_capture, evidence,
                                               stress_components=stress_components)
    target_metrics = _read_metrics_for_capture(target_capture, evidence,
                                               stress_components=stress_components)
    for metrics, label in ((source_metrics, "source"), (target_metrics, "target")):
        times = metrics.get("stored_times_s")
        if not isinstance(times, list):
            raise CampaignError(f"{label} handoff stress metrics have no stored time vector")
        _exact_time_row(times, boundary_s, f"{label} handoff stress metrics")
    si = _exact_time_row(source_metrics["stored_times_s"], boundary_s, "source stress metrics")
    ti = _exact_time_row(target_metrics["stored_times_s"], boundary_s, "target stress metrics")
    probe_jumps: dict[str, float] = {}
    for role in STRESS_ROLES:
        source_probe = source_metrics["stress_probe_values_pa"][role][si]
        target_probe = target_metrics["stress_probe_values_pa"][role][ti]
        probe_jumps[role] = max(abs(float(a) - float(b))
                                for a, b in zip(source_probe, target_probe))
    if max(probe_jumps.values()) > 1.0:
        raise CampaignError("native four-component stress probe handoff jump exceeds 1 Pa")
    source_mean = {role: float(source_metrics["adhesive_mean_stress_pa"][role][si])
                   for role in STRESS_ROLES}
    target_mean = {role: float(target_metrics["adhesive_mean_stress_pa"][role][ti])
                   for role in STRESS_ROLES}

    def tensor_norm(values: Mapping[str, float]) -> float:
        return math.sqrt(values["radial_normal"] ** 2 + values["hoop_normal"] ** 2 +
                         values["axial_normal"] ** 2 + 2.0 * values["rz_shear"] ** 2)

    delta = {role: target_mean[role] - source_mean[role] for role in STRESS_ROLES}
    relative_mean = tensor_norm(delta) / max(tensor_norm(source_mean), 1e3)
    if relative_mean > 1e-3:
        raise CampaignError("native adhesive stress-tensor handoff exceeds the 1e-3 normalized Frobenius limit")
    return {"status": "PASS", "boundary_time_s": boundary_s,
            "source_study": source_study, "target_study": target_study,
            "dof_jumps": checked, "stress_probe_max_abs_jump_pa": probe_jumps,
            "stress_mean_frobenius_relative_jump": relative_mean,
            "stress_mean_source_pa": source_mean, "stress_mean_target_pa": target_mean,
            "exact_common_dof_keys": True}


def _joined_staged_metrics(captures: Mapping[str, Mapping[str, Any]], evidence: Path,
                           stress_components: Mapping[str, Mapping[str, str]]) -> dict[str, Any]:
    ordered = [captures.get(f"staged_baseline:{tag}") for tag in ("stdUV", "stdBake", "stdCool")]
    if not all(isinstance(capture, Mapping) for capture in ordered):
        raise CampaignError("staged baseline is missing one of its three native stage captures")
    parts = [_read_metrics_for_capture(capture, evidence, stress_components=stress_components)
             for capture in ordered]
    names = ("stored_times_s", "alpha_iso_mean", "alpha_mean", "qpost_mean", "energy_samples",
             "fiber_end_displacement_m")
    merged: dict[str, Any] = {key: [] for key in names}
    for part_index, part in enumerate(parts):
        times = part.get("stored_times_s")
        if not isinstance(times, list) or not times:
            raise CampaignError("staged cure metric segment has no native accepted times")
        if part_index:
            previous_times = merged["stored_times_s"]
            if abs(float(previous_times[-1]) - float(times[0])) > TIME_TOLERANCE_S:
                raise CampaignError("native stage time vectors do not share their exact transfer boundary")
        start = 1 if part_index else 0
        for key in names:
            values = part.get(key)
            if not isinstance(values, list) or len(values) != len(times):
                raise CampaignError(f"staged cure metric {key} does not cover every native time")
            merged[key].extend(values[start:])
    # Use the third stage's shared global descriptors/readbacks only as
    # metadata; numerical feature tags and dataset ids are capture-local.
    merged.update({
        "schema": "W24_NATIVE_CURE_METRICS_V1",
        "status": "NATIVE_CURE_METRICS_CAPTURED",
        "native": True,
        "native_study_run_calls": 0,
        "solver_tag": "staged_baseline_merged_native_outputs",
        "dataset_tag": "staged_baseline_merged_native_outputs",
        "energy_samples": merged["energy_samples"],
    })
    return merged


def _compare_staged_to_continuous(staged_captures: Mapping[str, Mapping[str, Any]],
                                  continuous_capture: Mapping[str, Any], *,
                                  evidence: Path) -> dict[str, Any]:
    from tools.w24_science_acceptance import compare_full_dof_frames

    staged: list[dict[str, Any]] = []
    for tag in ("stdUV", "stdBake", "stdCool"):
        capture = staged_captures.get(f"staged_baseline:{tag}")
        if not isinstance(capture, Mapping):
            raise CampaignError(f"all-field comparison lacks staged {tag} native frames")
        frames = _solution_frames(capture, evidence)
        staged.extend(frames[:-1] if tag != "stdCool" else frames)
    continuous = _solution_frames(continuous_capture, evidence)
    continuous_times = [row["time_s"] for row in continuous]
    staged_times = [row["time_s"] for row in staged]
    schedule = _requested_times("stdCont", max_step_s=1.0)
    checked: list[dict[str, float]] = []
    for target in schedule:
        staged_index = _exact_time_row(staged_times, target, "merged staged field history")
        continuous_index = _exact_time_row(continuous_times, target, "continuous field history")
        metrics = compare_full_dof_frames(staged[staged_index], continuous[continuous_index],
                                          CURE_FIELD_NAMES, t0_k=T0_K)
        limits = {"temperature_offset_l2": 1e-3, "displacement_l2": 1e-3,
                  "alpha_max_abs": 1e-4, "qpost_max_abs": 1e-4}
        for key, maximum in limits.items():
            if metrics[key] > maximum:
                raise CampaignError(f"staged/continuous {key}={metrics[key]} exceeds {maximum} at t={target:g} s")
        checked.append({"time_s": target, **metrics})
    return {"status": "PASS", "compared_output_times_s": schedule,
            "maximum_errors": {key: max(row[key] for row in checked)
                               for key in ("temperature_offset_l2", "displacement_l2",
                                           "alpha_max_abs", "qpost_max_abs")},
            "field_identity": "exact XmeshInfoDofs keys; no interpolation or nearest-node matching",
            "samples": checked}


def _validate_native_science_slot(slot: SolveSlot, capture: Mapping[str, Any],
                                  prior_captures: Mapping[str, Mapping[str, Any]], *,
                                  evidence: Path,
                                  stress_components: Mapping[str, Mapping[str, str]],
                                  allow_missing_prior_for_reopen: bool = False) -> dict[str, Any]:
    from tools.w24_science_acceptance import (
        AcceptanceError, analytic_alpha_iso, heat_balance_residuals,
        require_control_delta, validate_mechanics_benchmark,
        validate_monotone_series, validate_native_cure_metrics,
        validate_stored_times,
    )

    if slot.case_id in MechanicsModelBindings.CASES:
        artifact = _metrics_artifact(capture, evidence, requires_energy=False,
                                     stress_components=stress_components,
                                     mechanics_case_id=slot.case_id)
        result = validate_mechanics_benchmark(artifact, case_id=slot.case_id)
        return {**result, "native_gate": "analytic homogeneous mechanics benchmark"}

    verified = _metrics_artifact(capture, evidence, requires_energy=True,
                                 stress_components=stress_components)
    times = verified.get("stored_times_s")
    if not isinstance(times, list) or not times:
        raise CampaignError("native cure slot has no stored-time vector")
    max_step = 0.5 if slot.case_id == "tight_time" else 1.0
    requested = _requested_times(slot.study_tag, max_step_s=max_step)
    try:
        validate_stored_times(times, requested, max_step_s=max_step)
        for field, minimum, maximum in (("alpha_iso_mean", 0.20, 1.0),
                                        ("alpha_mean", 0.20, 1.0),
                                        ("qpost_mean", 0.0, 1.0)):
            validate_monotone_series(times, verified.get(field), minimum=minimum,
                                     maximum=maximum, label=field)
        energy_samples = verified.get("energy_samples")
        if not isinstance(energy_samples, list) or len(energy_samples) != len(times):
            raise CampaignError("native cure energy history must cover every stored accepted time")
        start_time = float(times[0])
        local_energy = [{**dict(row), "time_s": float(row["time_s"]) - start_time}
                        for row in energy_samples]
        energy_balance = heat_balance_residuals(local_energy, max_gap_s=max_step)
        expected_start_end = {
            "stdUV": (0.0, 120.0), "stdBake": (120.0, 960.0),
            "stdCool": (960.0, 1500.0), "stdCont": (0.0, 1500.0),
        }[slot.study_tag]
        if (abs(float(times[0]) - expected_start_end[0]) > TIME_TOLERANCE_S or
                abs(float(times[-1]) - expected_start_end[1]) > TIME_TOLERANCE_S):
            raise CampaignError(f"{slot.study_tag} accepted history does not cover its exact frozen interval")

        if slot.case_id != "reset_negative_control":
            uv_rate = 1.1e-2 if slot.case_id == "dose_sensitivity" else 1e-2
            checkpoint_times = [time_s for time_s in (0, 10, 30, 47, 60, 120, 240, 960, 1500)
                                if expected_start_end[0] <= time_s <= expected_start_end[1]]
            expected_alpha_iso = analytic_alpha_iso(
                checkpoint_times, alpha0=0.20, t0_k=T0_K, pre_exponential_s=1e5,
                activation_j_mol=55e3, gas_constant_j_mol_k=8.31446261815324,
                uv_rate_s=uv_rate, uv_duration_s=120.0)
            iso = verified["alpha_iso_mean"]
            for target, expected in zip(checkpoint_times, expected_alpha_iso):
                index = _exact_time_row(times, target, f"{slot.case_id}/{slot.study_tag} alpha_iso")
                error = abs(float(iso[index]) - expected)
                if error > 1e-5:
                    raise CampaignError(f"native alpha_iso analytic error {error} exceeds 1e-5 at t={target:g} s")
        if slot.study_tag == "stdCont":
            validate_native_cure_metrics(
                verified, max_step_s=max_step, requested_times_s=requested,
                uv_rate_s=1.1e-2 if slot.case_id == "dose_sensitivity" else 1e-2)
    except AcceptanceError as exc:
        raise CampaignError(f"native {slot.case_id}/{slot.study_tag} numerical acceptance failed: {exc}") from exc

    result: dict[str, Any] = {
        "status": "PASS", "case_id": slot.case_id, "study_tag": slot.study_tag,
        "stored_time_count": len(times), "stored_time_start_s": float(times[0]),
        "stored_time_end_s": float(times[-1]), "max_step_s": max_step,
        "energy_samples": len(energy_samples),
        "max_segment_energy_relative_residual": max(row["relative_residual"] for row in energy_balance),
        "analytic_alpha_iso": "checked at exact scheduled checkpoints",
    }
    if slot.case_id == "staged_baseline" and slot.study_tag in {"stdBake", "stdCool"} and not allow_missing_prior_for_reopen:
        source_tag = "stdUV" if slot.study_tag == "stdBake" else "stdBake"
        source = prior_captures.get(f"staged_baseline:{source_tag}")
        if not isinstance(source, Mapping):
            raise CampaignError(f"positive staged path lacks its solved {source_tag} source capture")
        boundary = 120.0 if slot.study_tag == "stdBake" else 960.0
        result["stage_handoff"] = _stage_handoff(
            source, capture, source_study=source_tag, target_study=slot.study_tag,
            boundary_s=boundary, evidence=evidence, stress_components=stress_components)

    if slot.case_id == "staged_baseline" and slot.study_tag == "stdCool":
        merged = _joined_staged_metrics({**prior_captures,
            "staged_baseline:stdCool": capture}, evidence, stress_components)
        requested_full = _requested_times("stdCont", max_step_s=1.0)
        full = validate_native_cure_metrics(merged, max_step_s=1.0,
                                            requested_times_s=requested_full)
        result["full_staged_history"] = full
    elif slot.case_id == "continuous_comparator":
        baseline = _joined_staged_metrics(prior_captures, evidence, stress_components)
        staged_full = validate_native_cure_metrics(
            baseline, max_step_s=1.0, requested_times_s=_requested_times("stdCont", max_step_s=1.0))
        continuous_full = validate_native_cure_metrics(
            verified, max_step_s=1.0, requested_times_s=_requested_times("stdCont", max_step_s=1.0))
        staged_captures = {key: value for key, value in prior_captures.items()
                           if key.startswith("staged_baseline:")}
        result["staged_continuous_field_comparison"] = _compare_staged_to_continuous(
            staged_captures, capture, evidence=evidence)
        result["staged_full_history"] = staged_full
        result["continuous_full_history"] = continuous_full
    elif slot.case_id == "reset_negative_control" and not allow_missing_prior_for_reopen:
        baseline = prior_captures.get("staged_baseline:stdUV")
        if not isinstance(baseline, Mapping):
            raise CampaignError("reset negative control lacks the positive staged UV reference")
        base_metrics = _read_metrics_for_capture(baseline, evidence, stress_components=stress_components)
        source_i = _exact_time_row(base_metrics["stored_times_s"], 120.0, "positive stage-1 reset reference")
        reset_i = _exact_time_row(times, 120.0, "reset stage-2 initial state")
        alpha_delta = float(verified["alpha_mean"][reset_i]) - float(base_metrics["alpha_mean"][source_i])
        qpost_delta = float(verified["qpost_mean"][reset_i]) - float(base_metrics["qpost_mean"][source_i])
        if max(abs(alpha_delta), abs(qpost_delta)) < 0.20:
            raise CampaignError("reset negative control was not detected at the 120 s stage boundary")
        result["expected_negative_control"] = {
            "status": "EXPECTED_RESET_FAILURE_DETECTED", "boundary_time_s": 120.0,
            "alpha_delta": alpha_delta, "qpost_delta": qpost_delta,
            "minimum_absolute_delta": 0.20,
        }
    elif slot.case_id in {"coarse_mesh", "tight_time"} and not allow_missing_prior_for_reopen:
        fine = prior_captures.get("continuous_comparator:stdCont")
        if not isinstance(fine, Mapping):
            raise CampaignError(f"{slot.case_id} sensitivity lacks the native fine continuous reference")
        fine_metrics = _read_metrics_for_capture(fine, evidence, stress_components=stress_components)
        final_i = _exact_time_row(times, 1500.0, f"{slot.case_id} final metrics")
        fine_i = _exact_time_row(fine_metrics["stored_times_s"], 1500.0, "fine comparator final metrics")
        alpha_error = abs(float(verified["alpha_mean"][final_i]) - float(fine_metrics["alpha_mean"][fine_i])) / max(
            abs(float(fine_metrics["alpha_mean"][fine_i])), 1e-12)
        stress_fine = {role: float(fine_metrics["adhesive_mean_stress_pa"][role][fine_i])
                       for role in STRESS_ROLES}
        stress_candidate = {role: float(verified["adhesive_mean_stress_pa"][role][final_i])
                            for role in STRESS_ROLES}
        def stress_norm(values: Mapping[str, float]) -> float:
            return math.sqrt(values["radial_normal"] ** 2 + values["hoop_normal"] ** 2 +
                             values["axial_normal"] ** 2 + 2 * values["rz_shear"] ** 2)
        stress_error = math.sqrt(sum((stress_candidate[key] - stress_fine[key]) ** 2
                                     * (2.0 if key == "rz_shear" else 1.0)
                                     for key in STRESS_ROLES)) / max(stress_norm(stress_fine), 1e3)
        fiber_fine = fine_metrics.get("fiber_end_displacement_m", [])[fine_i]
        fiber_candidate = verified.get("fiber_end_displacement_m", [])[final_i]
        fiber_norm = math.hypot(*[float(value) for value in fiber_fine])
        fiber_candidate_norm = math.hypot(*[float(value) for value in fiber_candidate])
        fiber_error = abs(fiber_candidate_norm - fiber_norm) / max(abs(fiber_norm), 10e-9)
        if alpha_error > 0.01 or stress_error > 0.05 or fiber_error > 0.05:
            raise CampaignError(f"{slot.case_id} convergence limits failed: alpha={alpha_error}, "
                                f"stress={stress_error}, fiber_end={fiber_error}")
        result["sensitivity_comparison"] = {
            "status": "PASS", "final_alpha_relative_error": alpha_error,
            "stress_frobenius_relative_error": stress_error,
            "fiber_end_displacement_relative_error": fiber_error,
            "fiber_end_displacement_fine_m": fiber_norm,
            "fiber_end_displacement_candidate_m": fiber_candidate_norm,
        }
    elif slot.case_id == "dose_sensitivity" and not allow_missing_prior_for_reopen:
        baseline = prior_captures.get("continuous_comparator:stdCont")
        if not isinstance(baseline, Mapping):
            raise CampaignError("dose sensitivity lacks the native baseline continuous reference")
        fine_metrics = _read_metrics_for_capture(baseline, evidence, stress_components=stress_components)
        dose_i = _exact_time_row(times, 120.0, "dose sensitivity 120 s")
        fine_i = _exact_time_row(fine_metrics["stored_times_s"], 120.0, "baseline 120 s")
        delta = float(verified["alpha_mean"][dose_i]) - float(fine_metrics["alpha_mean"][fine_i])
        if delta < 0.01:
            raise CampaignError(f"dose sensitivity conversion increase {delta} is below 0.01 at 120 s")
        result["dose_sensitivity"] = {"status": "PASS", "time_s": 120.0,
                                      "alpha_mean_increase": delta}
    return result


def _compare_reopened_stage_capture(original: Mapping[str, Any], reopened: Mapping[str, Any],
                                    evidence: Path, *,
                                    stress_components: Mapping[str, Mapping[str, str]]) -> dict[str, Any]:
    original_frames = _solution_frames(original, evidence)
    reopened_frames = _solution_frames(reopened, evidence)
    if len(original_frames) != len(reopened_frames):
        raise CampaignError("fresh-Worker saved baseline changed the accepted-time frame count")
    for index, (first, second) in enumerate(zip(original_frames, reopened_frames)):
        if first != second:
            raise CampaignError(f"fresh-Worker stage frame {index} differs from the saved native field state")
    first_metrics = _read_metrics_for_capture(
        original, evidence, stress_components=stress_components)
    second_metrics = _read_metrics_for_capture(
        reopened, evidence, stress_components=stress_components)
    compare_keys = ("stored_times_s", "alpha_iso_mean", "alpha_mean", "qpost_mean",
                    "energy_samples", "stress_component_descriptors",
                    "adhesive_mean_stress_pa", "stress_probe_coordinates_m",
                    "stress_probe_values_pa", "material_heat_capacity_readback",
                    "fiber_end_displacement_m")
    for key in compare_keys:
        if first_metrics.get(key) != second_metrics.get(key):
            raise CampaignError(f"fresh-Worker stage metric {key} differs from saved native state")
    return {"status": "PASS", "stored_time_count": len(original_frames),
            "exact_dof_metadata_and_solution_vectors": True,
            "compared_metric_fields": list(compare_keys),
            "maximum_absolute_field_difference": 0.0}


def execute_solve_plan(adapter: ScienceCampaignAdapter, *, ledger_path: Path,
                       evidence: Path, birth_budget: BirthBudget,
                       stress_components: Mapping[str, Mapping[str, str]],
                       staged_baseline_path: Path,
                       clock: Any = _current_epoch_s) -> dict[str, Any]:
    """Execute the frozen solve order once, accounting only durable native calls.

    This controller deliberately treats one failed or unobserved Worker request
    as terminal for the campaign. It never retries a Study.run slot. Native
    acceptance remains tied to each adapter's captured field/energy receipts.
    """
    evidence.mkdir(parents=True, exist_ok=True)
    if read_solve_ledger(ledger_path):
        raise CampaignError("a new W24 science campaign must begin with an empty native solve ledger")
    if set(stress_components) != set(_STRESS_ROLES):
        raise CampaignError("four reviewed native stress component expressions must be bound before any solve")
    for role, descriptor in stress_components.items():
        if descriptor.get("unit") != "Pa" or not descriptor.get("expression") or not descriptor.get("description"):
            raise CampaignError(f"stress mapping for {role} lacks a native Pa expression/description")

    result: dict[str, Any] = {
        "schema": "W24_NATIVE_SCIENCE_CAMPAIGN_EXECUTION_V1",
        "status": "RUNNING",
        "scope": "ten-slot native campaign controller; solve count derives only from fsynced Java Study.run ledger",
        "birth_budget": birth_budget.receipt(now_epoch_s=clock()),
        "max_study_run_submissions": MAX_STUDY_RUN_SUBMISSIONS,
        "max_sequential_workers": MAX_SEQUENTIAL_WORKERS,
        "actual_study_run_submissions": 0,
        "slots": [],
        "retry_policy": "none; any failed/unobserved Worker request stops dependent work",
    }
    _append_fsynced_jsonl(evidence / "campaign_events.jsonl", {
        "event": "campaign_execution_started", "at_epoch_s": clock(),
        "birth_epoch_s": birth_budget.birth_epoch_s,
        "stress_component_roles": {role: dict(value) for role, value in stress_components.items()},
    })

    def within_budget(label: str, preferred: float) -> float:
        try:
            return birth_budget.rpc_timeout_s(preferred, now_epoch_s=clock())
        except CampaignError as exc:
            raise CampaignError(f"{label}: {exc}") from exc

    prior_captures: dict[str, Mapping[str, Any]] = {}
    try:
        prepared = adapter.prepare_campaign(stress_components,
                                            timeout_s=within_budget("prepare campaign", 180.0))
        if not isinstance(prepared, Mapping) or prepared.get("status") != "NATIVE_CAMPAIGN_PREPARED":
            raise CampaignError("native campaign preparation did not verify model copies and stress mapping")
        result["preparation"] = dict(prepared)
        mechanics_bindings = _validate_mechanics_binding_records(
            prepared.get("mechanics_model_bindings"))
        result["mechanics_model_bindings"] = mechanics_bindings
        for slot_index, slot in enumerate(SOLVE_PLAN):
            before = read_solve_ledger(ledger_path)
            if len(before) != slot_index or next_solve_slot(before) != slot:
                raise CampaignError("native solve ledger no longer matches the exact frozen slot order")
            timeout = within_budget(f"configure {slot.case_id}/{slot.study_tag}", 180.0)
            configured = adapter.configure_slot(slot, timeout_s=timeout)
            if not isinstance(configured, Mapping) or configured.get("status") != "NATIVE_SLOT_CONFIGURED":
                raise CampaignError(f"slot configuration failed before solve {slot_index + 1}")
            mechanics_binding = _require_mechanics_slot_binding(slot, prepared, configured)

            save_path = staged_baseline_path if slot == SolveSlot(
                "staged_baseline", "stdCool", SOLVE_PLAN[4].purpose) else None
            row: dict[str, Any] = {
                "slot_index": slot_index + 1,
                "case_id": slot.case_id,
                "study_tag": slot.study_tag,
                "configured": dict(configured),
                "dispatch_status": "NOT_DISPATCHED",
                "native_submission_count_before": len(before),
            }
            if mechanics_binding is not None:
                row["mechanics_model_binding"] = mechanics_binding
            try:
                call_result = adapter.run_study(
                    slot, ledger_path=ledger_path, save_after_success_path=save_path,
                    timeout_s=within_budget(f"Study.run slot {slot_index + 1}", 1800.0))
            except BaseException as exc:
                after_failure = read_solve_ledger(ledger_path)
                row.update({"dispatch_status": "FAILED_OR_UNOBSERVED_NO_RETRY",
                            "native_submission_count_after": len(after_failure),
                            "error": f"{type(exc).__name__}: {exc}"})
                result["slots"].append(row)
                result["actual_study_run_submissions"] = len(after_failure)
                raise CampaignError(f"Study.run slot {slot_index + 1} failed or was unobserved; no retry") from exc

            after = read_solve_ledger(ledger_path)
            actual_count = len(after)
            row["native_submission_count_after"] = actual_count
            result["actual_study_run_submissions"] = actual_count
            if actual_count != len(before) + 1 or after[-1]["case_id"] != slot.case_id or after[-1]["study_tag"] != slot.study_tag:
                row.update({"dispatch_status": "LEDGER_MISMATCH_NO_RETRY",
                            "call_result": dict(call_result) if isinstance(call_result, Mapping) else repr(call_result)})
                result["slots"].append(row)
                raise CampaignError(f"Study.run slot {slot_index + 1} did not produce exactly one matching durable invocation")
            row["native_invocation"] = dict(after[-1])
            if (not isinstance(call_result, Mapping) or
                    call_result.get("status") != "NATIVE_STUDY_RUN_RETURNED" or
                    call_result.get("submission_index") != actual_count or
                    call_result.get("study_run_calls_from_this_action") != 1):
                row.update({"dispatch_status": "NATIVE_INVOCATION_NOT_CONFIRMED_NO_RETRY",
                            "call_result": dict(call_result) if isinstance(call_result, Mapping) else repr(call_result)})
                result["slots"].append(row)
                raise CampaignError(f"Study.run slot {slot_index + 1} has a ledger entry but no terminal successful Java receipt")
            row["dispatch_status"] = "NATIVE_STUDY_RUN_RETURNED"
            row["call_result"] = dict(call_result)

            if save_path is not None:
                if not save_path.is_file() or save_path.stat().st_size <= 0:
                    result["slots"].append(row)
                    raise CampaignError("staged baseline immediate save did not produce a nonempty MPH file")
                if call_result.get("immediate_save_path") != str(save_path):
                    result["slots"].append(row)
                    raise CampaignError("staged baseline immediate save receipt names a different MPH path")
                row["immediate_staged_baseline_save"] = {
                    "path": str(save_path), "size_bytes": save_path.stat().st_size,
                    "sha256": sha256(save_path), "occurred_before_capture": True,
                }

            capture = adapter.capture_solution(
                slot, output_dir=evidence / f"slot_{slot_index + 1:02d}_{slot.case_id}_{slot.study_tag}",
                timeout_s=within_budget(f"capture {slot.case_id}/{slot.study_tag}", 600.0))
            verified_capture = verify_capture_receipt(
                capture, evidence,
                requires_energy=slot.case_id not in MechanicsModelBindings.CASES,
                stress_components=stress_components,
                mechanics_case_id=(slot.case_id
                                   if slot.case_id in MechanicsModelBindings.CASES else None))
            gate = adapter.validate_slot(slot, capture, prior_captures,
                                         timeout_s=within_budget(f"validate {slot.case_id}/{slot.study_tag}", 300.0))
            if not isinstance(gate, Mapping) or gate.get("status") != "PASS":
                row.update({"capture": verified_capture,
                            "acceptance": dict(gate) if isinstance(gate, Mapping) else repr(gate),
                            "dispatch_status": "SOLVED_BUT_ACCEPTANCE_GATE_FAILED"})
                result["slots"].append(row)
                result["actual_study_run_submissions"] = len(read_solve_ledger(ledger_path))
                raise CampaignError(f"native acceptance gate failed at {slot.case_id}/{slot.study_tag}; later solves stopped")
            row["capture"] = verified_capture
            row["acceptance"] = dict(gate)
            prior_captures[f"{slot.case_id}:{slot.study_tag}"] = dict(capture)
            result["slots"].append(row)

            if slot == SOLVE_PLAN[4]:
                # Saving occurs inside the Java Study.run action immediately on
                # return, before any field/energy-derived acceptance readback.
                if not call_result.get("immediate_save_path"):
                    raise CampaignError("staged baseline Java solve returned without its immediate save path")
                reopen = adapter.reopen_staged_baseline(
                    save_path, timeout_s=within_budget("fresh sequential Worker baseline reopen", 600.0))
                if not isinstance(reopen, Mapping) or reopen.get("status") != "NATIVE_REOPEN_READBACK_PASS":
                    raise CampaignError("fresh sequential Worker did not verify the solved staged baseline")
                if len(read_solve_ledger(ledger_path)) != actual_count:
                    raise CampaignError("fresh Worker staged-baseline reopen unexpectedly submitted another solve")
                result["staged_baseline_reopen"] = dict(reopen)

        final_ledger = read_solve_ledger(ledger_path)
        if len(final_ledger) != MAX_STUDY_RUN_SUBMISSIONS:
            raise CampaignError("campaign ended without all ten durable Study.run submissions")
        result.update({"status": "NATIVE_SCIENCE_EXECUTION_COMPLETE_ACCEPTANCE_RECEIPTS_RECORDED",
                       "actual_study_run_submissions": len(final_ledger),
                       "study_run_ledger": str(ledger_path),
                       "acceptance_scope": "per-slot adapter receipts; independent overall review remains required"})
        _append_fsynced_jsonl(evidence / "campaign_events.jsonl", {
            "event": "campaign_execution_complete", "at_epoch_s": clock(),
            "study_run_submissions": len(final_ledger),
            "status": result["status"],
        })
        return result
    except BaseException as exc:
        result.update({"status": "FAIL_OR_INCOMPLETE_NO_RETRY",
                       "actual_study_run_submissions": len(read_solve_ledger(ledger_path)),
                       "study_run_ledger": str(ledger_path),
                       "error": f"{type(exc).__name__}: {exc}",
                       "budget_at_failure": birth_budget.receipt(now_epoch_s=clock())})
        _append_fsynced_jsonl(evidence / "campaign_events.jsonl", {
            "event": "campaign_stopped_no_retry", "at_epoch_s": clock(),
            "study_run_submissions": result["actual_study_run_submissions"],
            "error": result["error"],
        })
        return result


def freeze_campaign(receipt: Mapping[str, Any], *, source_manifest: Mapping[str, Mapping[str, str]],
                    equation_view_inventory: Mapping[str, Any]) -> dict[str, Any]:
    """Create an offline campaign manifest bound to exact template/code bytes."""
    inventory = validate_equation_view_inventory(equation_view_inventory)
    if receipt.get("native_solver_submissions") != []:
        raise CampaignError("science campaign requires an unsolved source template")
    return {
        "status": "FROZEN_BEFORE_SCIENCE_ENGINE_BIRTH",
        "candidate_id": "W24-CURE-HISTORY-AXISYM-SCIENCE-01",
        "frozen_campaign": "W24 cure history, independent controls, and two mechanics benchmarks",
        "created_by": str(Path(__file__).resolve()),
        "limits": {
            "owned_servers": 1,
            "max_seconds_from_exact_birth_including_cleanup": MAX_BIRTH_BUDGET_S,
            "max_study_run_submissions": MAX_STUDY_RUN_SUBMISSIONS,
            "max_sequential_workers": MAX_SEQUENTIAL_WORKERS,
            "cleanup_reserve_seconds": CLEANUP_RESERVE_S,
        },
        "template": {"path": receipt["path"], "size_bytes": receipt["size_bytes"],
                     "sha256": receipt["sha256"], "setup_freeze_sha256": receipt["freeze_sha256"],
                     "project_id": receipt.get("project_id"),
                     "project_workspace": receipt.get("project_workspace"),
                     "project_create_receipt_sha256": receipt.get("registered_project", {}).get("project_create_receipt_sha256")},
        "plan_path": str(PLAN),
        "plan_sha256": sha256(PLAN),
        "equation_view_inventory": inventory,
        "solve_slots": [slot.__dict__ for slot in SOLVE_PLAN],
        "source_manifest": {name: dict(value) for name, value in source_manifest.items()},
        "actual_submission_count_source": "fsynced native Java ledger event written immediately before Study.run()",
        "no_implicit_retry": True,
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    """Run one separately approved, exact frozen W24 science campaign."""
    required = ("candidate_freeze", "expected_candidate_sha256", "approval",
                "template_receipt", "work", "evidence")
    missing = [name for name in required if not getattr(args, name, None)]
    if missing:
        raise PrebirthRefusal("science execution requires candidate freeze, exact SHA-256, root approval, setup receipt, work and evidence paths; missing " + ", ".join(missing))
    return execute_campaign_lifecycle(
        template_receipt=Path(args.template_receipt),
        candidate_path=Path(args.candidate_freeze),
        expected_candidate_sha256=args.expected_candidate_sha256,
        approval_path=Path(args.approval), work=Path(args.work),
        evidence=Path(args.evidence), runtime_factory=_production_runtime_factory)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="freeze the unsolved setup receipt for root review")
    prepare.add_argument("--template-receipt", required=True)
    prepare.add_argument("--candidate-out", required=True)
    execute = commands.add_parser("execute", help="execute one exact root-approved frozen campaign")
    execute.add_argument("--template-receipt", required=True)
    execute.add_argument("--candidate-freeze", required=True)
    execute.add_argument("--expected-candidate-sha256", required=True)
    execute.add_argument("--approval", required=True)
    execute.add_argument("--work", required=True)
    execute.add_argument("--evidence", required=True)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            try:
                result = prepare_campaign_candidate(Path(args.template_receipt), Path(args.candidate_out))
            except Exception as exc:
                raise PrebirthRefusal(f"candidate preparation refused before native execution: {type(exc).__name__}: {exc}") from exc
        else:
            result = run(args)
    except PrebirthRefusal as exc:
        result = {"status": "PREBIRTH_REFUSED_NO_ENGINE",
                  "error": f"{type(exc).__name__}: {exc}",
                  "engine_births": 0, "study_run_submissions": 0,
                  "native_acceptance": "NOT_RUN"}
    except Exception as exc:
        result = {"status": "OUTCOME_UNKNOWN",
                  "error": f"{type(exc).__name__}: {exc}",
                  "outcome_note": "unclassified execution exception; no zero-birth or zero-solve claim is made",
                  "engine_births": None, "study_run_submissions": None,
                  "native_acceptance": "NOT_ACCEPTED"}
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str, allow_nan=False))
    if result.get("status") in {"CANDIDATE_FROZEN_AWAITING_ROOT_APPROVAL",
                                 "NATIVE_SCIENCE_EXECUTION_COMPLETE_CLEANUP_VERIFIED"}:
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
