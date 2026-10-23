"""Original AppContainer Delete API-return-only lifecycle through real software owners.

No Windows API, service, ACL, COMSOL, or native AppContainer mutation runs here.
The original SCM broker/runtime/Gate, Controller, HostChange, OperationStore,
and AppContainer helper execute normally; only their documented SDK seam is synthetic.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import hashlib
import os
import json
import queue
import stat
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from comsol_mcp import _windows_appcontainer_lifecycle as lifecycle
from comsol_mcp import _host_scoped_provider as host_provider
from comsol_mcp import _windows_host_change as changes
from comsol_mcp import _windows_t037 as windows_t037
from comsol_mcp import _windows_t037_targets as targets
from comsol_mcp import _windows_t037_wfp as wfp
from comsol_mcp import _windows_t037_broker as broker
from comsol_mcp import _w26_os_resources as resources
from comsol_mcp._windows_owned_resources import registry_for
from comsol_mcp._execution_contract import ExecutionContractError
from tests.test_windows_appcontainer import environment
from tests.test_windows_t037_broker import _ScmApi


@contextmanager
def running_original_controller(env, *, tmp_path):
    profile, plan, _sdk, _files, _api, store, provider, authority, _executor, daemon, wrapper = env
    daemon._windows_t037_profile = wrapper
    if not getattr(daemon, "_windows_t037_profile_path", None):
        daemon._windows_t037_profile_path = "public-profile"
    daemon._scoped_host_provider = provider
    daemon._closing = False
    daemon._session_t037_process_factories = {}
    daemon._dispatch_condition = threading.Condition(threading.RLock())
    daemon._worker_admission_inflight = 0
    daemon._retained_host_control_executors = {}
    requests: queue.Queue = queue.Queue()
    responses: queue.Queue = queue.Queue()
    ready = threading.Event()
    errors = []
    service_holder = {}
    state = {}

    def issue_request(runtime, phase, receipt=None, ref="apply-once"):
        issuer = broker.WindowsT037HostControlIssuer(
            authority=authority, provider=provider, profile=profile,
            profile_source_sha256=state.get(
                "profile_source_override", wrapper.source_sha256),
        )
        request = issuer.issue(
            runtime, phase=phase, plan=state.get("plan_override", plan),
            authorization_ref=ref, receipt=receipt,
        )
        request.consume(
            service_name=profile.windows_isolation.service_name,
            project_id=profile.project_id,
            profile_sha256=profile.sha256,
        )
        return request

    def service_main(runtime):
        runtime.mark_running()
        daemon._retain_t037_scm_runtime(runtime)
        factory = daemon._new_t037_session_factory(profile.project_id, "appc-delete-test")
        state.update(runtime=runtime, factory=factory)
        ready.set()
        while True:
            item = requests.get(timeout=10)
            if item is None:
                break
            phase, receipt, ref = item
            try:
                state["phase"] = phase
                request = issue_request(runtime, phase, receipt, ref)
                state["last_request"] = request
                value = daemon._execute_internal_host_control(request)
                responses.put((request, value, None))
            except BaseException as exc:
                responses.put((state.get("last_request"), None, exc))
        service_holder["api"].handler(broker.SERVICE_CONTROL_STOP, 0, None, None)

    service_api = _ScmApi()
    service_holder["api"] = service_api
    service = broker.WindowsScmServiceBroker._for_software_test(
        profile.windows_isolation.service_name, service_main,
        api=service_api, token=broker._SCM_TEST_API_TOKEN,
    )
    service_holder["service"] = service

    def run_service():
        try:
            service.run()
        except BaseException as exc:
            errors.append(exc)
            ready.set()

    thread = threading.Thread(target=run_service, name="software-appc-scm")
    thread.start()
    try:
        assert ready.wait(5), "synthetic SCM runtime did not become ready"
        assert not errors, [(repr(error), repr(error.__cause__)) for error in errors]

        def submit(phase, receipt=None, ref="apply-once"):
            requests.put((phase, receipt, ref))
            request, value, error = responses.get(timeout=10)
            if error is not None:
                return request, None, error
            return request, value, None

        def direct_submit(phase, receipt=None, ref="apply-once"):
            request = None
            try:
                state["phase"] = phase
                request = issue_request(state["runtime"], phase, receipt, ref)
                state["direct_last_request"] = request
                hook = state.get("direct_request_hook")
                if callable(hook):
                    hook(request)
                value = daemon._execute_internal_host_control(request)
                return request, value, None
            except BaseException as exc:
                return request, None, exc

        state["direct_submit"] = direct_submit

        yield state, submit, daemon
    finally:
        factory = state.get("factory")
        if (factory is not None and not factory._closed
                and getattr(state.get("runtime"), "_profile_gate", None) is not None
                and state["runtime"]._profile_gate.state == "ACTIVE"):
            factory.close()
        requests.put(None)
        thread.join(10)
        assert not thread.is_alive(), "synthetic SCM service did not stop"
        assert not errors, [(repr(error), repr(error.__cause__)) for error in errors]
        assert store.get_operation is not None


def _delete_sdk(sdk, *, fault=None, callback=None):
    calls = []

    def delete(name_buffer):
        name = name_buffer.value
        calls.append(name)
        sdk.calls.append(("delete", name))
        if callback is not None:
            callback(name)
        if fault == "after_effect_raise":
            raise OSError("software Delete may have taken effect")
        if fault == "nonzero":
            return -2147024891
        if fault == "bool":
            return False
        return 0

    sdk.userenv.DeleteAppContainerProfile = __import__(
        "tests.test_windows_host_preflight_owner", fromlist=["Function"]
    ).Function(delete)
    return calls


def _journal(store, job_id):
    return [
        json.loads(row[0])
        for row in store.db.execute(
            "SELECT metadata FROM job_events WHERE job_id=? AND event=? ORDER BY id",
            (job_id, "HostControlMutationJournalV2"),
        ).fetchall()
    ]


def test_original_api_s_ok_is_one_call_and_cached_ack_never_reenters(tmp_path, monkeypatch):
    env, sdk = environment(tmp_path, monkeypatch, with_restore=True)
    profile, plan, _sdk, _files, api, store, _provider, _authority, _executor, _daemon, _wrapper = env
    delete_calls = _delete_sdk(sdk)
    try:
        with running_original_controller(env, tmp_path=tmp_path) as (state, submit, daemon):
            apply_request, receipt, error = submit("APPLY")
            assert error is None, repr(error)
            assert receipt.receipt_sha256
            factory = state["factory"]
            assert factory._profile_participant is not None

            # A live original factory refuses before restore admission or SDK entry.
            _, refused, error = submit("RESTORE", receipt, "restore-once")
            assert refused is None
            assert isinstance(error, ExecutionContractError)
            assert error.code == "T037_POLICY_UNVERIFIED"
            assert delete_calls == []
            assert store.db.execute(
                "SELECT count(*) FROM host_control_admissions WHERE phase='RESTORE'"
            ).fetchone()[0] == 0
            assert state["runtime"]._profile_gate.state == "ACTIVE"

            # Only the exact original factory's real close acknowledgement permits pause.
            factory.close()
            restore_request, result, error = submit("RESTORE", receipt, "restore-once")
            assert error is None, repr(error)
            assert result["success"] is False
            assert result["execution_state"] == "UNKNOWN"
            assert result["restore_state"] == "POSTIMAGE_UNVERIFIED"
            assert result["postimage_state"] == "UNVERIFIED"
            assert result["native_delete_state"] == "API_CONFIRMED"
            assert result["api_return_hresult"] == 0
            assert result["safe_retry"] is False
            assert delete_calls == [profile.windows_isolation.appcontainer_profile_name]
            assert api._appcontainer_profile_effect is next(
                iter(daemon._retained_appcontainer_profile_effects.values())
            )
            assert state["runtime"]._profile_gate.state == "PAUSED"
            admission = store.db.execute(
                "SELECT admission_id,operation_id,job_id,state,baseline_json "
                "FROM host_control_admissions WHERE phase='RESTORE'"
            ).fetchone()
            assert admission["state"] == "RECOVERY_REQUIRED"
            baseline = json.loads(admission["baseline_json"])
            body = baseline["appcontainer_restore"]["restore_body"]
            assert body["operation"] == "DELETE"
            assert body["acceptance_target"] == "NATIVE_API_RETURN_ONLY"
            assert body["source_plan_sha256"] == plan.sha256
            journal = _journal(store, admission["job_id"])
            assert [row["event"] for row in journal] == [
                "RESTORE_STEP_INTENT", "RECOVERY_REQUIRED"
            ]
            assert not any(row["event"] == "RESTORE_STEP_READBACK_VERIFIED"
                           for row in journal)
            ack_rows = store.db.execute(
                "SELECT id,metadata FROM job_events WHERE job_id=? "
                "AND event='AppContainerProfileDeleteApiReturnCaptured'",
                (admission["job_id"],),
            ).fetchall()
            assert len(ack_rows) == 1
            ack = json.loads(ack_rows[0]["metadata"])
            assert ack["payload"]["native_delete_state"] == "API_CONFIRMED"
            assert ack["payload"]["postimage_state"] == "UNVERIFIED"
            assert ack["payload"]["safe_retry"] is False
            assert "restore-once" not in json.dumps(ack)

            _, denied, error = submit("RESTORE", receipt, "different-ref")
            assert denied is None
            assert isinstance(error, ExecutionContractError)
            assert error.code == "HOST_CONTROL_AUTHORIZATION_REQUIRED"
            assert delete_calls == [profile.windows_isolation.appcontainer_profile_name]

            # Same scoped ref and same original effect only acknowledge cached S_OK.
            _, cached, error = submit("RESTORE", receipt, "restore-once")
            assert error is None, repr(error)
            assert cached["native_delete_state"] == "API_CONFIRMED"
            assert cached["api_return_acknowledgment"] == "CACHED"
            assert delete_calls == [profile.windows_isolation.appcontainer_profile_name]
            assert store.db.execute(
                "SELECT count(*) FROM host_control_admissions WHERE phase='RESTORE'"
            ).fetchone()[0] == 1
            assert store.db.execute(
                "SELECT count(*) FROM job_events WHERE job_id=? "
                "AND event='AppContainerProfileDeleteApiReturnCaptured'",
                (admission["job_id"],),
            ).fetchone()[0] == 1
    finally:
        store.close()


def test_delete_runs_outside_runtime_lock_and_concurrent_unknown_restore_never_reenters(
        tmp_path, monkeypatch):
    env, sdk = environment(tmp_path, monkeypatch, with_restore=True)
    store = env[5]
    runtime_holder = {}
    delete_entered = threading.Event()
    allow_delete_return = threading.Event()
    lock_probe_done = threading.Event()
    lock_probe = {}
    probe_threads = []

    def probe_runtime_lock(_name):
        runtime_lock = runtime_holder["runtime"]._lock

        def acquire_and_release():
            acquired = runtime_lock.acquire(timeout=2)
            lock_probe["acquired"] = acquired
            if acquired:
                runtime_lock.release()
            lock_probe_done.set()

        probe_thread = threading.Thread(target=acquire_and_release,
                                        name="appc-runtime-lock-probe")
        probe_threads.append(probe_thread)
        probe_thread.start()
        assert lock_probe_done.wait(3), "runtime lock stayed held during Delete SDK call"
        assert lock_probe.get("acquired") is True
        delete_entered.set()
        assert allow_delete_return.wait(5), "test did not release the bounded SDK fixture"

    delete_calls = _delete_sdk(sdk, fault="after_effect_raise", callback=probe_runtime_lock)
    try:
        with running_original_controller(env, tmp_path=tmp_path) as (state, submit, _daemon):
            _, receipt, error = submit("APPLY")
            assert error is None, repr(error)
            state["factory"].close()
            runtime_holder["runtime"] = state["runtime"]

            first_result = []
            first_thread = threading.Thread(
                target=lambda: first_result.append(
                    state["direct_submit"]("RESTORE", receipt, "restore-once")),
                name="appc-first-restore",
            )
            first_thread.start()
            assert delete_entered.wait(5), "original Delete SDK fixture was not entered"
            assert lock_probe["acquired"] is True

            contender_ready = threading.Event()
            state["direct_request_hook"] = lambda _request: contender_ready.set()
            second_result = []
            second_thread = threading.Thread(
                target=lambda: second_result.append(
                    state["direct_submit"]("RESTORE", receipt, "restore-once")),
                name="appc-concurrent-restore",
            )
            second_thread.start()
            assert contender_ready.wait(5), "concurrent RESTORE did not reach public request admission"
            assert len(delete_calls) == 1
            allow_delete_return.set()
            first_thread.join(10)
            second_thread.join(10)
            assert not first_thread.is_alive()
            assert not second_thread.is_alive()
            assert len(first_result) == 1 and len(second_result) == 1

            _, first, first_error = first_result[0]
            assert first_error is None, repr(first_error)
            assert first["execution_state"] == "UNKNOWN"
            assert first["native_delete_state"] == "UNKNOWN"
            assert first["safe_retry"] is False
            _, _, second_error = second_result[0]
            assert isinstance(second_error, ExecutionContractError)
            assert second_error.code == "HOST_CHANGE_APPCONTAINER_DELETE_REPLAY_REFUSED"
            assert len(delete_calls) == 1

            # A later request sees the durable UNKNOWN and cannot reenter Delete.
            _, _, retry_error = submit("RESTORE", receipt, "restore-once")
            assert isinstance(retry_error, ExecutionContractError)
            assert retry_error.code == "HOST_CHANGE_APPCONTAINER_DELETE_REPLAY_REFUSED"
            assert len(delete_calls) == 1
            restore_admissions = store.db.execute(
                "SELECT count(*) FROM host_control_admissions WHERE phase='RESTORE'"
            ).fetchone()[0]
            assert restore_admissions == 1
    finally:
        allow_delete_return.set()
        for probe_thread in probe_threads:
            probe_thread.join(5)
        store.close()


@pytest.mark.parametrize("fault", ["nonzero", "bool", "after_effect_raise"])
def test_ambiguous_or_nonzero_delete_is_unknown_and_never_replayed(tmp_path, monkeypatch, fault):
    env, sdk = environment(tmp_path, monkeypatch, with_restore=True)
    _delete_sdk(sdk, fault=fault)
    store = env[5]
    try:
        with running_original_controller(env, tmp_path=tmp_path) as (state, submit, _daemon):
            _, receipt, error = submit("APPLY")
            assert error is None, repr(error)
            state["factory"].close()
            _, result, error = submit("RESTORE", receipt, "restore-once")
            assert error is None, repr(error)
            assert result["execution_state"] == "UNKNOWN"
            assert result["native_delete_state"] == "UNKNOWN"
            assert result["safe_retry"] is False
            assert sum(isinstance(call, tuple) and call[0] == "delete"
                       for call in sdk.calls) == 1
            restore_job = store.db.execute(
                "SELECT j.job_id FROM jobs j JOIN host_control_admissions a "
                "ON a.job_id=j.job_id WHERE a.phase='RESTORE'"
            ).fetchone()["job_id"]
            assert store.db.execute(
                "SELECT count(*) FROM job_events WHERE job_id=? "
                "AND event='AppContainerProfileDeleteApiReturnCaptured'",
                (restore_job,),
            ).fetchone()[0] == 0
            _, refused, error = submit("RESTORE", receipt, "restore-once")
            assert refused is None
            assert isinstance(error, ExecutionContractError)
            assert error.code == "HOST_CHANGE_APPCONTAINER_DELETE_REPLAY_REFUSED"
            assert sum(isinstance(call, tuple) and call[0] == "delete"
                       for call in sdk.calls) == 1
            assert store.db.execute(
                "SELECT count(*) FROM host_control_admissions WHERE phase='RESTORE'"
            ).fetchone()[0] == 1
            assert state["runtime"]._profile_gate.state == "PAUSED"
    finally:
        store.close()


def test_restore_refuses_wrong_scope_before_pause_and_store_admission(tmp_path, monkeypatch):
    env, sdk = environment(tmp_path, monkeypatch, with_restore=True)
    store = env[5]
    try:
        with running_original_controller(env, tmp_path=tmp_path) as (state, submit, _daemon):
            _, receipt, error = submit("APPLY")
            assert error is None, repr(error)
            _, result, error = submit("RESTORE", receipt, "wrong-scope")
            assert result is None and isinstance(error, ExecutionContractError)
            assert error.code == "HOST_CONTROL_AUTHORIZATION_REQUIRED"
            assert not sdk.calls.count("delete")
            assert store.db.execute(
                "SELECT count(*) FROM host_control_admissions WHERE phase='RESTORE'"
            ).fetchone()[0] == 0
            assert state["runtime"]._profile_gate.state == "ACTIVE"
    finally:
        store.close()


@pytest.mark.parametrize("fault", [
    "source", "receipt", "action", "guid", "public_root", "copied_sid",
    "api_owner", "service_subject", "unknown_apply_job", "unresolved_allocator",
])
def test_changed_original_create_binding_refuses_before_delete_and_admission(
        tmp_path, monkeypatch, fault):
    env, sdk = environment(tmp_path, monkeypatch, with_restore=True)
    profile, plan, _sdk, _files, api, store = env[:6]
    delete_calls = _delete_sdk(sdk)
    leaked_entry = None
    try:
        with running_original_controller(env, tmp_path=tmp_path) as (state, submit, daemon):
            _, receipt, error = submit("APPLY")
            assert error is None, repr(error)
            state["factory"].close()
            effect = api._appcontainer_profile_effect

            if fault == "source":
                state["profile_source_override"] = "f" * 64
            elif fault == "receipt":
                receipt = replace(receipt, receipt_sha256="f" * 64)
            elif fault in {"action", "guid"}:
                payload = plan.as_dict()
                if fault == "action":
                    payload["change_manifest"][0]["resource_name"] = "foreign-profile"
                else:
                    payload["change_manifest"][0]["ownership_guid"] = (
                        "{00000000-0000-4000-8000-000000000002}"
                    )
                encoded = resources._canonical_json(payload)
                state["plan_override"] = resources.HostChangePlan(
                    encoded, hashlib.sha256(encoded.encode("utf-8")).hexdigest())
            elif fault == "public_root":
                daemon._retained_appcontainer_profile_effects.clear()
            elif fault == "copied_sid":
                effect._sid_text = "S-1-15-3-999999999"
            elif fault == "api_owner":
                api._appcontainer_userenv = SimpleNamespace(
                    DeleteAppContainerProfile=sdk.userenv.DeleteAppContainerProfile)
            elif fault == "service_subject":
                original = api._service_sid_observe(profile.windows_isolation.service_name)
                changed = json.loads(json.dumps(original))
                changed["status"]["process_id"] += 1
                monkeypatch.setattr(api, "_service_sid_observe", lambda _name: changed)
            elif fault == "unknown_apply_job":
                store.db.execute(
                    "UPDATE operations SET status='UNKNOWN' WHERE operation_id=?",
                    (effect.admission.operation_id,),
                )
                store.db.execute(
                    "UPDATE jobs SET status='UNKNOWN' WHERE operation_id=?",
                    (effect.admission.operation_id,),
                )
            elif fault == "unresolved_allocator":
                leaked_entry = registry_for(api).acquire(
                    api, 123456, lambda _handle: None, mode="VOID",
                    label="test-unresolved-before-delete", domain="test_only",
                )

            _, result, error = submit("RESTORE", receipt, "restore-once")
            assert result is None
            assert isinstance(error, ExecutionContractError), repr(error)
            assert delete_calls == []
            assert store.db.execute(
                "SELECT count(*) FROM host_control_admissions WHERE phase='RESTORE'"
            ).fetchone()[0] == 0
            assert state["runtime"]._profile_gate.state == "ACTIVE"
    finally:
        if leaked_entry is not None and leaked_entry.state != "RELEASED":
            leaked_entry.release_owned(api)
        store.close()


def test_tampered_cached_api_ack_refuses_without_reentering_delete(tmp_path, monkeypatch):
    env, sdk = environment(tmp_path, monkeypatch, with_restore=True)
    profile, _plan, _sdk, _files, _api, store = env[:6]
    delete_calls = _delete_sdk(sdk)
    try:
        with running_original_controller(env, tmp_path=tmp_path) as (state, submit, _daemon):
            _, receipt, error = submit("APPLY")
            assert error is None, repr(error)
            state["factory"].close()
            _, result, error = submit("RESTORE", receipt, "restore-once")
            assert error is None, repr(error)
            assert result["native_delete_state"] == "API_CONFIRMED"
            job_id = store.db.execute(
                "SELECT job_id FROM host_control_admissions WHERE phase='RESTORE'"
            ).fetchone()["job_id"]
            row = store.db.execute(
                "SELECT id,metadata FROM job_events WHERE job_id=? "
                "AND event='AppContainerProfileDeleteApiReturnCaptured'",
                (job_id,),
            ).fetchone()
            metadata = json.loads(row["metadata"])
            metadata["payload"]["hresult"] = 1
            store.db.execute(
                "UPDATE job_events SET metadata=? WHERE id=?",
                (resources._canonical_json(metadata), row["id"]),
            )

            _, refused, error = submit("RESTORE", receipt, "restore-once")
            assert refused is None
            assert isinstance(error, ExecutionContractError)
            assert error.code == "HOST_CONTROL_APPCONTAINER_DELETE_ACK_CONFLICT"
            assert delete_calls == [profile.windows_isolation.appcontainer_profile_name]
            assert store.db.execute(
                "SELECT count(*) FROM host_control_admissions WHERE phase='RESTORE'"
            ).fetchone()[0] == 1
    finally:
        store.close()


def test_cached_ack_store_failure_keeps_api_fact_and_recovers_without_delete(tmp_path, monkeypatch):
    env, sdk = environment(tmp_path, monkeypatch, with_restore=True)
    store = env[5]
    delete_calls = _delete_sdk(sdk)
    original = store.record_appcontainer_delete_api_return
    calls = {"count": 0}

    def fail_first_ack(attempt):
        calls["count"] += 1
        if calls["count"] == 1:
            raise OSError("software diagnostic write failure")
        return original(attempt)

    monkeypatch.setattr(store, "record_appcontainer_delete_api_return", fail_first_ack)
    try:
        with running_original_controller(env, tmp_path=tmp_path) as (state, submit, _daemon):
            _, receipt, error = submit("APPLY")
            assert error is None, repr(error)
            state["factory"].close()
            _, first, error = submit("RESTORE", receipt, "restore-once")
            assert error is None, repr(error)
            assert first["native_delete_state"] == "API_CONFIRMED"
            assert first["api_return_acknowledgment"] == "PENDING"
            assert delete_calls == [env[0].windows_isolation.appcontainer_profile_name]
            _, second, error = submit("RESTORE", receipt, "restore-once")
            assert error is None, repr(error)
            assert second["api_return_acknowledgment"] == "RECORDED"
            assert calls["count"] == 2
            assert delete_calls == [env[0].windows_isolation.appcontainer_profile_name]
            restore_rows = store.db.execute(
                "SELECT operation_id,job_id FROM host_control_admissions WHERE phase='RESTORE'"
            ).fetchall()
            assert len(restore_rows) == 1
            assert store.db.execute(
                "SELECT count(*) FROM job_events WHERE job_id=? "
                "AND event='AppContainerProfileDeleteApiReturnCaptured'",
                (restore_rows[0]["job_id"],),
            ).fetchone()[0] == 1
            operation = store.get_operation(restore_rows[0]["operation_id"])
            assert operation["status"] == "UNKNOWN"
            assert operation["result"]["native_delete_state"] == "API_CONFIRMED"
    finally:
        monkeypatch.setattr(store, "record_appcontainer_delete_api_return", original)
        store.close()
