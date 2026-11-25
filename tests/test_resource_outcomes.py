"""Focused AST/fake tests for T35 resource transaction outcomes; no disk DBs."""
from __future__ import annotations

import ast
import copy
import importlib.util
import json
import sqlite3
import sys
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from types import MethodType, SimpleNamespace
from typing import Any


ROOT = Path(__file__).parents[1]
SOURCE = ROOT / "comsol_mcp"
IDENTITY = {key: key for key in ("job_id", "operation_id", "request_id",
                                 "request_hash", "idempotency_key")}
TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED", "EXPIRED", "LOST"}


def load_contract():
    spec = importlib.util.spec_from_file_location(
        "t35_v4_execution_contract", SOURCE / "_execution_contract.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CONTRACT = load_contract()


class ResourcePolicyError(CONTRACT.ExecutionContractError):
    def __init__(self, code, message="synthetic resource refusal"):
        super().__init__(code, message, safe_retry=True, stage="pre_dispatch")


class MemoryResourceError(CONTRACT.ExecutionContractError):
    def __init__(self, code, message="synthetic memory refusal"):
        super().__init__(code, message, safe_retry=True, stage="pre_dispatch")


def extract_method(path, class_name, method_name, namespace):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    owner = next(node for node in tree.body
                 if isinstance(node, ast.ClassDef) and node.name == class_name)
    method = copy.deepcopy(next(node for node in owner.body
                                if isinstance(node, ast.FunctionDef)
                                and node.name == method_name))
    wrapper = ast.Module(
        body=[ast.ClassDef(name="Harness", bases=[], keywords=[], body=[method],
                           decorator_list=[])], type_ignores=[])
    exec(compile(ast.fix_missing_locations(wrapper), str(path), "exec"), namespace)
    return namespace["Harness"].__dict__[method_name]


class TransactionDB:
    """SQLite connection fixed to :memory:, with explicit ACK fault injection."""
    def __init__(self, fault=None):
        self.connection = sqlite3.connect(":memory:")
        self.connection.execute("CREATE TABLE reservations (value TEXT NOT NULL)")
        self.fault = fault
        self.statements = []

    @property
    def in_transaction(self):
        return self.connection.in_transaction

    def execute(self, sql, parameters=()):
        self.statements.append(sql)
        if sql == "ROLLBACK" and self.fault == "rollback_fails":
            raise sqlite3.OperationalError("synthetic rollback failed")
        if sql == "COMMIT" and self.fault == "commit_ack_lost":
            self.connection.execute(sql, parameters)
            raise ResourcePolicyError("COMMIT_ACK_LOST", "commit applied; ACK lost")
        if sql == "ROLLBACK" and self.fault == "rollback_ack_lost":
            self.connection.execute(sql, parameters)
            raise sqlite3.OperationalError("rollback applied; ACK lost")
        return self.connection.execute(sql, parameters)

    def count_reservations(self):
        return self.connection.execute("SELECT count(*) FROM reservations").fetchone()[0]

    def begin_external(self):
        self.statements.append("BEGIN IMMEDIATE (external)")
        self.connection.execute("BEGIN IMMEDIATE")

    def close(self):
        self.connection.close()


class FakeStore:
    def __init__(self, *, db_fault=None, fail_event=None, finish_fails_in_transaction=False):
        self.db = TransactionDB(db_fault)
        self.lock = threading.RLock()
        self.status = "QUEUED"
        self.persisted_result = None
        self.events = []
        self.fail_event = fail_event
        self.event_hook = None
        self.finish_fails_in_transaction = finish_fails_in_transaction

    def job(self, _job_id):
        return {"job_id": IDENTITY["job_id"], "operation_id": IDENTITY["operation_id"],
                "status": self.status, "operation": {"operation_id": IDENTITY["operation_id"]},
                "result": copy.deepcopy(self.persisted_result)}

    def get_operation(self, _operation_id):
        return {**IDENTITY, "status": self.status, "result": copy.deepcopy(self.persisted_result)}

    def unresolved_jobs(self):
        return []

    def update_job(self, _job_id, status, _metadata):
        if self.status not in TERMINAL:
            self.status = status

    def transition_status(self, _job_id, **_kwargs):
        if self.status != "QUEUED":
            return False, self.status, self.job(IDENTITY["job_id"])
        self.status = "RUNNING"
        return True, "RUNNING", self.job(IDENTITY["job_id"])

    def add_event(self, _job_id, event, metadata=None):
        if self.event_hook:
            self.event_hook(self, event, metadata)
        if event == self.fail_event:
            raise OSError("synthetic trace sink failure")
        self.events.append((event, copy.deepcopy(metadata)))

    def finish(self, _operation_id, *, status, result):
        if self.finish_fails_in_transaction and self.db.in_transaction:
            raise sqlite3.OperationalError("synthetic nested finish transaction")
        if self.status in TERMINAL:
            return False, self.status
        self.status = status
        self.persisted_result = copy.deepcopy(result)
        return True, status

    def set_terminal(self, status):
        data = {"status": status, "engine_dispatched": False}
        if status == "CANCELLED":
            data["cancel_accepted"] = True
        self.status = status
        self.persisted_result = {
            "success": status == "SUCCEEDED", "data": data,
            "execution": {"job_id": IDENTITY["job_id"],
                          "operation_id": IDENTITY["operation_id"],
                          "request_id": IDENTITY["request_id"],
                          "request_hash": IDENTITY["request_hash"],
                          "idempotency_key": IDENTITY["idempotency_key"]},
        }


class FakeBackend:
    def __init__(self):
        self.calls = 0

    def invoke(self, *_args):
        self.calls += 1
        return {"success": True, "data": {}}


def control_method(name):
    path = SOURCE / "_control_daemon.py"
    namespace = {
        "ExecutionContractError": CONTRACT.ExecutionContractError,
        "ResourceReservationOutcome": CONTRACT.ResourceReservationOutcome,
        "Mapping": Mapping, "sqlite3": sqlite3, "TERMINAL": TERMINAL,
        "time": time,
    }
    return extract_method(path, "ControlDaemon", name, namespace)


def make_control(store):
    control = SimpleNamespace()
    control.store = store
    control.backend = FakeBackend()
    control.lock = threading.RLock()
    control.running = {}
    control._disk_resource_coordinator = None
    control._memory_resource_coordinator = None
    control._desktop_before_model_dispatch = lambda *_args: None
    control._authorize_project_execution = lambda *_args: None
    control._attach_confirmed_project_id = lambda *_args: None
    control.log_attempts = 0
    control._log_exception = lambda: setattr(control, "log_attempts", control.log_attempts + 1)
    control.release_calls = []
    control._release_terminal_resource_reservations = lambda job_id: (
        control.release_calls.append((store.status, job_id)) or {
            "state": "COMPLETE", "reservation_state": "SYNTHETIC_RELEASED"})
    control._error = lambda code, message, *, data=None, **details: {
        "success": False, "data": data or {},
        "error": {"code": code, "message": message,
                  "safe_retry": details.pop("safe_retry", False), **details}}
    control._exception = lambda exc: {
        "success": False, "data": {}, "error": exc.as_dict()}
    control._finish = control_method("_finish").__get__(control, type(control))
    control._execute = control_method("_execute").__get__(control, type(control))
    return control


def run_execute(store, disk=None, memory=None, *, log_failure=False):
    control = make_control(store)
    control._disk_resource_coordinator = disk
    control._memory_resource_coordinator = memory
    if log_failure:
        def failed_logger():
            control.log_attempts += 1
            raise OSError("synthetic control-errors.log failure")
        control._log_exception = failed_logger
    result = control._execute(IDENTITY, "synthetic.operation", {}, {},
                              {"queue_timeout_s": None}, time.monotonic())
    return result, control


def disk_globals(behavior):
    profile = SimpleNamespace(
        project_id="project", allowed_operations={"synthetic.operation"},
        profile_id="disk-profile",
        sha256="0" * 64, host_id="host", storage_id="storage",
        estimate_for=lambda _operation: 8)

    def durable_context(_db, job_id):
        return {"job_id": job_id, "job_status": "QUEUED", "operation_status": "QUEUED",
                "operation_id": IDENTITY["operation_id"],
                "canonical_operation_id": "synthetic.operation", "project_id": "project"}

    def reserve_disk(*, store, **_kwargs):
        return behavior(store)

    return {
        "Any": Any, "ExecutionContractError": CONTRACT.ExecutionContractError,
        "ResourceReservationOutcome": CONTRACT.ResourceReservationOutcome,
        "_nonempty": lambda value: isinstance(value, str) and bool(value),
        "_fail": lambda code, message, **_details: (_ for _ in ()).throw(
            ResourcePolicyError(code, message)),
        "CapabilityState": SimpleNamespace(UNSUPPORTED=SimpleNamespace(value="UNSUPPORTED")),
        "_durable_job_context": durable_context,
        "_permission_for_disk_operation": lambda _operation: "project_write",
        "_authorize_profile_project": lambda *_args, **_kwargs: None,
        "_bind_disk_root_metadata": lambda *_args: None,
        "_reserve_disk_for_job_in_transaction": reserve_disk,
        "time": time,
        "profile": profile,
    }


def make_disk(store, behavior, *, provider=True):
    namespace = disk_globals(behavior)
    reserve = extract_method(SOURCE / "_w26_os_resources.py", "DiskResourceCoordinator",
                            "reserve_queued_job", namespace)
    disk = SimpleNamespace(
        store=store, storage_provider=(SimpleNamespace(read_observation=lambda _p: object())
                                      if provider else None),
        config=SimpleNamespace(resolve=lambda _project: namespace["profile"]),
        authority=object(), max_observation_age_ns=1,
        requires_reservation=lambda **_kwargs: True)
    disk.reserve_queued_job = MethodType(reserve, disk)
    return disk


def memory_method():
    return extract_method(
        SOURCE / "_w26_memory_resources.py", "MemoryResourceCoordinator",
        "reserve_queued_job", {
            "Any": Any, "Mapping": Mapping,
            "ExecutionContractError": CONTRACT.ExecutionContractError,
            "ResourceReservationOutcome": CONTRACT.ResourceReservationOutcome,
        })


def make_memory(store, refusal):
    def reserve_prepared(*args):
        if callable(refusal):
            return refusal(store, *args)
        raise refusal

    memory = SimpleNamespace(
        store=store, requires_reservation=lambda **_kwargs: True,
        prepare_queued_job=lambda _job_id: {
            "context": {"canonical_operation_id": "synthetic.operation"},
            "estimate_bytes": 4},
        reserve_prepared_in_transaction=reserve_prepared)
    memory.reserve_queued_job = MethodType(memory_method(), memory)
    return memory


def disk_result():
    return {"operation_id": IDENTITY["operation_id"], "reservation_id": "disk-res",
            "requested_bytes": 8, "trusted_estimate_bytes": 8,
            "disk_resource_profile_id": "disk-profile",
            "disk_resource_profile_sha256": "0" * 64, "host_id": "host",
            "storage_id": "storage", "enforcement": "synthetic",
            "evidence_classification": "synthetic"}


def store_reservation(store):
    store.db.execute("INSERT INTO reservations(value) VALUES ('disk')")
    return disk_result()


def memory_result():
    return {"operation_id": IDENTITY["operation_id"], "reservation_id": "memory-res",
            "requested_bytes": 4, "trusted_estimate_bytes": 4,
            "memory_resource_profile_id": "memory-profile",
            "memory_resource_profile_sha256": "1" * 64, "host_id": "host",
            "boot_id": "boot", "available_bytes": 8,
            "evidence_classification": "synthetic", "hard_cap_state": "WITHIN_CAP"}


def store_memory_reservation(store, *_args):
    store.db.execute("INSERT INTO reservations(value) VALUES ('memory')")
    return memory_result()


def store_then_refuse_memory(store, *_args):
    store.db.execute("INSERT INTO reservations(value) VALUES ('memory-uncommitted')")
    raise MemoryResourceError("INSUFFICIENT_MEMORY_BUDGET")


def test_disk_without_provider_returns_original_refusal_before_begin():
    store = FakeStore()
    result, control = run_execute(store, make_disk(store, store_reservation, provider=False))
    assert store.status == "FAILED"
    assert result["error"]["code"] == "DISK_STORAGE_PROVIDER_UNSUPPORTED"
    assert result["error"]["safe_retry"] is True
    assert result["data"]["resource_admission"] == "REFUSED"
    assert store.db.statements == []
    assert control.backend.calls == 0
    store.db.close()


def test_disk_capacity_refusal_returns_original_after_confirmed_rollback():
    def refuse(_store):
        raise ResourcePolicyError("INSUFFICIENT_DISK_BUDGET")

    store = FakeStore()
    result, control = run_execute(store, make_disk(store, refuse))
    assert store.status == "FAILED"
    assert result["error"]["code"] == "INSUFFICIENT_DISK_BUDGET"
    assert result["error"]["safe_retry"] is True
    assert store.db.statements == ["BEGIN IMMEDIATE", "ROLLBACK"]
    assert store.db.count_reservations() == 0
    assert control.backend.calls == 0
    store.db.close()


def test_memory_only_refusal_returns_original_after_confirmed_rollback():
    store = FakeStore()
    result, control = run_execute(
        store, memory=make_memory(store, MemoryResourceError("INSUFFICIENT_MEMORY_BUDGET")))
    assert store.status == "FAILED"
    assert result["error"]["code"] == "INSUFFICIENT_MEMORY_BUDGET"
    assert result["error"]["safe_retry"] is True
    assert store.db.statements == ["BEGIN IMMEDIATE", "ROLLBACK"]
    assert control.backend.calls == 0
    store.db.close()


def test_joint_disk_memory_refusal_rolls_back_disk_and_returns_memory_error():
    store = FakeStore()
    memory = make_memory(store, MemoryResourceError("INSUFFICIENT_MEMORY_BUDGET"))
    result, control = run_execute(store, make_disk(store, store_reservation), memory)
    assert store.status == "FAILED"
    assert result["error"]["code"] == "INSUFFICIENT_MEMORY_BUDGET"
    assert result["error"]["safe_retry"] is True
    assert store.db.statements == ["BEGIN IMMEDIATE", "INSERT INTO reservations(value) VALUES ('disk')",
                                   "ROLLBACK"]
    assert store.db.count_reservations() == 0
    assert control.backend.calls == 0
    store.db.close()


def test_successful_disk_memory_and_joint_reservations_commit_before_dispatch():
    for mode in ("disk", "memory", "joint"):
        store = FakeStore()
        disk = make_disk(store, store_reservation) if mode in {"disk", "joint"} else None
        memory = (make_memory(store, store_memory_reservation)
                  if mode in {"memory", "joint"} else None)
        result, control = run_execute(store, disk, memory)
        expected_rows = 2 if mode == "joint" else 1
        assert store.status == "SUCCEEDED"
        assert result["success"] is True
        assert store.db.statements.count("BEGIN IMMEDIATE") == 1
        assert store.db.statements.count("COMMIT") == 1
        assert store.db.statements.count("ROLLBACK") == 0
        assert store.db.count_reservations() == expected_rows
        assert control.backend.calls == 1
        events = {event for event, _metadata in store.events}
        if mode in {"disk", "joint"}:
            assert "W26DiskAdmission" in events
        if mode in {"memory", "joint"}:
            assert "W26MemoryAdmission" in events
        store.db.close()


def test_commit_applied_but_ack_lost_stays_unknown_even_for_retryable_contract_error():
    store = FakeStore(db_fault="commit_ack_lost")
    result, control = run_execute(store, make_disk(store, store_reservation))
    assert store.db.statements == ["BEGIN IMMEDIATE", "INSERT INTO reservations(value) VALUES ('disk')",
                                   "COMMIT"]
    assert store.db.in_transaction is False
    assert store.db.count_reservations() == 1
    assert store.status == "UNKNOWN"
    assert result["error"]["code"] == "RESOURCE_ADMISSION_STATE_UNKNOWN"
    assert result["error"]["safe_retry"] is False
    assert result["data"]["cause_type"] == "ResourcePolicyError"
    assert control.log_attempts == 0
    assert control.backend.calls == 0
    assert control.release_calls == []
    store.db.close()


def test_memory_only_commit_applied_but_ack_lost_stays_unknown():
    store = FakeStore(db_fault="commit_ack_lost")
    result, control = run_execute(store, memory=make_memory(store, store_memory_reservation))
    assert store.db.statements == ["BEGIN IMMEDIATE",
                                   "INSERT INTO reservations(value) VALUES ('memory')",
                                   "COMMIT"]
    assert store.db.in_transaction is False
    assert store.db.count_reservations() == 1
    assert store.status == "UNKNOWN"
    assert result["error"]["code"] == "RESOURCE_ADMISSION_STATE_UNKNOWN"
    assert result["error"]["safe_retry"] is False
    assert control.backend.calls == 0
    assert control.release_calls == []
    store.db.close()


def test_reused_exception_commit_ack_unknown_clears_old_memory_outcome():
    reused = CONTRACT.ExecutionContractError(
        "INSUFFICIENT_MEMORY_BUDGET", "synthetic reused refusal",
        safe_retry=True, stage="pre_dispatch")
    refused_store = FakeStore()
    refused, _ = run_execute(
        refused_store, memory=make_memory(refused_store, reused))
    assert refused_store.status == "FAILED"
    assert refused["error"]["code"] == "INSUFFICIENT_MEMORY_BUDGET"
    assert refused["error"]["safe_retry"] is True
    assert reused.resource_reservation_outcome is CONTRACT.ResourceReservationOutcome.NOT_COMMITTED
    store = FakeStore()
    execute = store.db.execute

    def commit_applied_ack_lost(sql, parameters=()):
        if sql == "COMMIT":
            execute(sql, parameters)
            raise reused
        return execute(sql, parameters)

    store.db.execute = commit_applied_ack_lost
    result, control = run_execute(
        store, memory=make_memory(store, store_memory_reservation))
    assert not hasattr(reused, "resource_reservation_outcome")
    assert store.db.in_transaction is False
    assert store.db.count_reservations() == 1
    assert store.status == "UNKNOWN"
    assert result["error"]["code"] == "RESOURCE_ADMISSION_STATE_UNKNOWN"
    assert result["error"]["safe_retry"] is False
    assert result["data"]["resource_admission"] == "UNKNOWN"
    assert control.backend.calls == 0
    assert control.release_calls == []
    refused_store.db.close()
    store.db.close()


def refusal_with_insert(store):
    store.db.execute("INSERT INTO reservations(value) VALUES ('uncommitted')")
    raise ResourcePolicyError("INSUFFICIENT_DISK_BUDGET")


def test_rollback_ack_lost_stays_unknown_after_rollback_was_applied():
    store = FakeStore(db_fault="rollback_ack_lost")
    result, control = run_execute(store, make_disk(store, refusal_with_insert))
    assert store.db.statements == ["BEGIN IMMEDIATE",
                                   "INSERT INTO reservations(value) VALUES ('uncommitted')",
                                   "ROLLBACK"]
    assert store.db.in_transaction is False
    assert store.db.count_reservations() == 0
    assert store.status == "UNKNOWN"
    assert result["error"]["code"] == "RESOURCE_ADMISSION_STATE_UNKNOWN"
    assert result["error"]["safe_retry"] is False
    assert control.backend.calls == 0
    assert control.release_calls == []
    store.db.close()


def test_memory_only_rollback_ack_lost_stays_unknown():
    store = FakeStore(db_fault="rollback_ack_lost")
    result, control = run_execute(store, memory=make_memory(store, store_then_refuse_memory))
    assert store.db.statements == ["BEGIN IMMEDIATE",
                                   "INSERT INTO reservations(value) VALUES ('memory-uncommitted')",
                                   "ROLLBACK"]
    assert store.db.in_transaction is False
    assert store.db.count_reservations() == 0
    assert store.status == "UNKNOWN"
    assert result["error"]["safe_retry"] is False
    assert result["data"]["resource_admission"] == "UNKNOWN"
    assert control.backend.calls == 0
    assert control.release_calls == []
    store.db.close()


def test_rollback_failure_stays_unknown_and_keeps_job_nonterminal_in_memory():
    store = FakeStore(db_fault="rollback_fails", finish_fails_in_transaction=True)
    result, control = run_execute(store, make_disk(store, refusal_with_insert))
    assert store.db.statements == ["BEGIN IMMEDIATE",
                                   "INSERT INTO reservations(value) VALUES ('uncommitted')",
                                   "ROLLBACK"]
    assert store.db.in_transaction is True
    assert store.db.count_reservations() == 1
    assert store.status == "QUEUED"
    assert result["error"]["safe_retry"] is False
    assert result["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
    assert control.backend.calls == 0
    assert control.release_calls == []
    store.db.close()


def test_coordinators_do_not_rollback_a_transaction_they_do_not_own():
    for resource in ("disk", "memory"):
        store = FakeStore(finish_fails_in_transaction=True)
        store.db.begin_external()
        if resource == "disk":
            result, control = run_execute(store, make_disk(store, store_reservation))
        else:
            result, control = run_execute(
                store, memory=make_memory(
                    store, MemoryResourceError("INSUFFICIENT_MEMORY_BUDGET")))
        assert store.db.statements == ["BEGIN IMMEDIATE (external)"]
        assert store.db.in_transaction is True
        assert store.status == "QUEUED"
        assert result["error"]["safe_retry"] is False
        assert control.backend.calls == 0
        assert control.release_calls == []
        store.db.close()


def test_cancelled_terminal_readback_still_releases_confirmed_reservation_on_log_fault():
    store = FakeStore(fail_event="W26DiskAdmission")
    store.event_hook = lambda current, event, _metadata: (
        current.set_terminal("CANCELLED") if event == "W26DiskAdmission" else None)
    reserved = {"value": False}
    disk = SimpleNamespace(
        requires_reservation=lambda **_kwargs: True,
        reserve_queued_job=lambda *_args, **_kwargs: (reserved.__setitem__("value", True) or disk_result()))
    result, control = run_execute(store, disk, log_failure=True)
    assert store.status == "CANCELLED"
    assert result["data"]["status"] == "CANCELLED"
    assert result["execution"]["pre_dispatch_resource_release_observation"]["state"] == "COMPLETE"
    assert control.release_calls == [("CANCELLED", IDENTITY["job_id"])]
    assert reserved["value"] is True
    assert control.backend.calls == 0
    assert control.log_attempts == 1
    assert store.persisted_result["execution"].get(
        "pre_dispatch_resource_release_observation") is None
    assert "synthetic logger failure" not in json.dumps(result)
    assert "synthetic trace sink failure" not in json.dumps(result)
    store.db.close()


def main():
    tests = [value for name, value in globals().items()
             if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"PASS total={len(tests)} sqlite=:memory:")


if __name__ == "__main__":
    main()
