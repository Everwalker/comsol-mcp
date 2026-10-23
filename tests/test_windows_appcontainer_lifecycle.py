"""Private persistent AppContainer owner; all fixtures are SOFTWARE_ONLY."""
import copy
import json
import os
from dataclasses import replace
from types import SimpleNamespace

import pytest

from comsol_mcp import _windows_appcontainer_lifecycle as lifecycle
from comsol_mcp import _windows_appcontainer_native as native
from comsol_mcp._windows_host_change import _SOFTWARE_TEST_TOKEN
from tests.test_windows_appcontainer import dispatch, environment


def test_successful_create_root_survives_sdk_cleanup_store_and_reconcile(tmp_path, monkeypatch):
    env, sdk = environment(tmp_path, monkeypatch)
    daemon, store, api, executor = env[9], env[5], env[4], env[8]
    try:
        result = dispatch(env)
        assert "error" not in result, repr(result.get("error"))
        roots = daemon._retained_appcontainer_profile_effects
        assert len(roots) == 1
        effect = next(iter(roots.values()))
        assert effect.evidence_classification == "SOFTWARE_ONLY"
        with pytest.raises(AttributeError):
            effect._software_only = False
        with pytest.raises(AttributeError):
            effect.evidence_classification = "PRIVATE_NATIVE_OWNER_BINDING"
        assert effect.request is result["request"]
        assert effect.executor is executor and effect.api is api
        assert effect.runtime is result["request"].runtime
        assert effect._hresult == 0 and effect._sid_bytes is not None
        assert effect._native_attempt.entered and effect._native_attempt.finished
        assert effect._original_call is api._appcontainer_original_call
        assert effect._store_result == (store, effect.admission.operation_id, "SUCCEEDED")
        assert not sdk.calls.count("DeleteAppContainerProfile")
        daemon._reconcile_original_host_control_executor(result["request"], executor)
        assert daemon._retained_appcontainer_profile_effects[id(effect)] is effect
        assert effect._last_reconcile == "SOFTWARE_ONLY"
    finally:
        store.close()


def test_result_persistence_failure_keeps_the_original_root_and_unknown(tmp_path, monkeypatch):
    env, sdk = environment(tmp_path, monkeypatch)
    store, daemon = env[5], env[9]
    original_finish = store.finish

    def fail_success(*args, **kwargs):
        if kwargs.get("status") == "SUCCEEDED":
            return False, "UNKNOWN"
        return original_finish(*args, **kwargs)

    monkeypatch.setattr(store, "finish", fail_success)
    try:
        result = dispatch(env)
        assert "error" in result
        row = json.loads(store.db.execute("SELECT result FROM operations").fetchone()[0])
        assert row["execution_state"] == "UNKNOWN" and row["safe_retry"] is False
        assert sdk.calls.count("create") == 1
        roots = daemon._retained_appcontainer_profile_effects
        assert len(roots) == 1
        effect = next(iter(roots.values()))
        assert effect.request is result["request"]
        assert effect._hresult == 0 and effect._sid_bytes is not None
        assert effect._store_result is None
        assert effect.evidence_classification == "SOFTWARE_ONLY"
    finally:
        store.close()


def test_pre_create_failure_is_reconciled_only_as_no_dispatch(tmp_path, monkeypatch):
    env, sdk = environment(tmp_path, monkeypatch)
    store, daemon, api, executor = env[5], env[9], env[4], env[8]
    sdk.fault = "impersonation"
    original_thread = sdk.thread
    calls = []

    def fail_only_inside_native_create(*args):
        calls.append(1)
        if len(calls) > 1:
            sdk.fault = "impersonation"
        else:
            sdk.fault = None
        return original_thread(*args)

    api._advapi.OpenThreadToken.callback = fail_only_inside_native_create
    try:
        result = dispatch(env)
        assert "error" in result and sdk.calls.count("create") == 0
        row_before = json.loads(store.db.execute("SELECT result FROM operations").fetchone()[0])
        roots = daemon._retained_appcontainer_profile_effects
        assert len(roots) == 1
        effect = next(iter(roots.values()))
        assert effect.evidence_classification == "SOFTWARE_ONLY"
        assert effect._native_attempt.finished and not effect._native_attempt.entered
        daemon._reconcile_original_host_control_executor(result["request"], executor)
        assert not daemon._retained_appcontainer_profile_effects
        row_after = json.loads(store.db.execute("SELECT result FROM operations").fetchone()[0])
        assert row_after == row_before
        assert row_after["execution_state"] == "UNKNOWN"
    finally:
        store.close()


def test_possible_create_failure_keeps_original_root_after_reconcile_refusal(tmp_path, monkeypatch):
    env, sdk = environment(tmp_path, monkeypatch)
    store, daemon, executor = env[5], env[9], env[8]
    sdk.fault = "io"
    try:
        result = dispatch(env)
        assert "error" in result and sdk.calls.count("create") == 1
        roots = daemon._retained_appcontainer_profile_effects
        assert len(roots) == 1
        effect = next(iter(roots.values()))
        assert effect._native_attempt.entered and effect._native_attempt.finished
        with pytest.raises(Exception):
            daemon._reconcile_original_host_control_executor(result["request"], executor)
        assert daemon._retained_appcontainer_profile_effects[id(effect)] is effect
        row = json.loads(store.db.execute("SELECT result FROM operations").fetchone()[0])
        assert row["execution_state"] == "UNKNOWN" and row["safe_retry"] is False
    finally:
        store.close()


def test_same_namespace_is_fenced_but_other_profile_remains_usable(tmp_path, monkeypatch):
    env, _sdk = environment(tmp_path, monkeypatch)
    store, daemon = env[5], env[9]
    try:
        result = dispatch(env)
        assert "error" not in result
        original = next(iter(daemon._retained_appcontainer_profile_effects.values()))

        def candidate(*, name, sid, user=original.user_sid):
            policy = replace(original.policy, appcontainer_profile_name=name,
                             appcontainer_sid=sid, service_logon_sid=user)
            executor = SimpleNamespace(
                profile=SimpleNamespace(windows_isolation=policy),
                api=SimpleNamespace(), store=store, software_only=True,
                _software_test_seal=_SOFTWARE_TEST_TOKEN)
            record = {"request": original.request, "executor": executor}
            return lifecycle.create_software_test_profile_effect(
                daemon=daemon, request=original.request, record=record,
                executor=executor, admission=original.admission,
                action={"operation": "CREATE"}, subject=original.subject,
                test_token=_SOFTWARE_TEST_TOKEN)

        # Either durable identity blocks same-user reacquisition even when the
        # other expected identity changes.
        for candidate_effect in (
            candidate(name=original.profile_name.upper(), sid="S-1-15-2-998"),
            candidate(name="different-profile", sid=original.appcontainer_sid.lower()),
        ):
            with pytest.raises(Exception) as refused:
                lifecycle.root_profile_effect(daemon, candidate_effect)
            assert refused.value.code == "HOST_CHANGE_APPCONTAINER_NAMESPACE_BUSY"

        # A different profile/SID for the same user and an identical name/SID
        # for a different actual user are independent namespaces.
        other = candidate(name="independent-profile", sid="S-1-15-2-998")
        different_user = candidate(name=original.profile_name,
                                   sid=original.appcontainer_sid,
                                   user="S-1-5-21-9000")
        lifecycle.root_profile_effect(daemon, other)
        lifecycle.root_profile_effect(daemon, different_user)
        assert len(daemon._retained_appcontainer_profile_effects) == 3
        assert original.namespace != other.namespace
        assert original.evidence_classification == other.evidence_classification == "SOFTWARE_ONLY"
    finally:
        store.close()


def test_native_create_requires_root_and_off_windows_owner_source_refuses(tmp_path, monkeypatch):
    env, sdk = environment(tmp_path, monkeypatch)
    try:
        with pytest.raises(Exception) as no_root:
            native.create(env[4], env[0].windows_isolation, {})
        assert no_root.value.code == "HOST_CHANGE_APPCONTAINER_OWNER_REQUIRED"
        assert "create" not in sdk.calls
        with pytest.raises(Exception) as no_source:
            lifecycle._native_process_identity(os.getpid(), "S-1-5-18", r"C:\\Windows\\service.exe")
        assert no_source.value.code == "HOST_CHANGE_APPCONTAINER_OWNER_SOURCE_UNAVAILABLE"
    finally:
        env[5].close()


@pytest.mark.parametrize(("binding_fault", "expected_code"), [
    ("wrong_loader_origin", "HOST_CHANGE_APPCONTAINER_PROFILE_LOAD_ORIGIN_REQUIRED"),
    ("profile_path_copy", "HOST_CHANGE_APPCONTAINER_PROFILE_LOAD_ORIGIN_REQUIRED"),
    ("manual_executor", "HOST_CHANGE_APPCONTAINER_PROFILE_LOAD_ORIGIN_REQUIRED"),
    ("profile_copy", "HOST_CHANGE_APPCONTAINER_OWNER_BINDING_MISMATCH"),
    ("source_copy", "HOST_CHANGE_APPCONTAINER_PROFILE_SOURCE_CHANGED"),
    ("current_file_bytes_drift", "HOST_CHANGE_APPCONTAINER_PROFILE_SOURCE_CHANGED"),
    ("current_file_protection_drift", "HOST_CHANGE_APPCONTAINER_PROFILE_SOURCE_CHANGED"),
    ("current_host_policy_drift", "HOST_CHANGE_APPCONTAINER_PROFILE_SOURCE_CHANGED"),
    ("runtime_copy", "HOST_CHANGE_APPCONTAINER_OWNER_BINDING_MISMATCH"),
    ("subject_copy", "HOST_CHANGE_APPCONTAINER_OWNER_BINDING_MISMATCH"),
])
def test_native_constructor_rejects_fresh_loader_binding_substitutions(
        tmp_path, monkeypatch, binding_fault, expected_code):
    from comsol_mcp import _windows_host_change as changes, _windows_t037 as windows_t037
    from comsol_mcp import _windows_t037_admission as admission
    from comsol_mcp._windows_t037_broker import (
        WindowsScmServiceBroker, WindowsT037HostControlIssuer, _SCM_TEST_API_TOKEN,
    )
    from tests.test_windows_t037_broker import _ScmApi

    original_factory = changes.WindowsHostChangeExecutor.from_trusted_profile_path.__func__
    env, sdk = environment(tmp_path, monkeypatch)
    profile, plan, _scm, _files, api, store, provider, authority, _executor, daemon, wrapper = env
    daemon._scoped_host_provider = provider
    daemon._windows_t037_scm_runtime = None
    daemon._windows_t037_scm_binding = None
    daemon._closing = False
    loaded = []

    def reload_profile(_path):
        fresh = copy.deepcopy(wrapper)
        loaded.append(fresh)
        assert fresh.host_resource_profile is not wrapper.host_resource_profile
        if len(loaded) > 1 and binding_fault == "current_file_bytes_drift":
            fresh = replace(fresh, source_sha256="e" * 64)
        elif len(loaded) > 1 and binding_fault == "current_file_protection_drift":
            identity = dict(fresh.source_identity)
            identity["security_sha256"] = "f" * 64
            fresh = replace(fresh, source_identity=identity)
        elif len(loaded) > 1 and binding_fault == "current_host_policy_drift":
            changed_policy = replace(fresh.host_resource_profile.windows_isolation,
                                     host_fingerprint_sha256="f" * 64)
            changed_profile = replace(fresh.host_resource_profile,
                                      windows_isolation=changed_policy)
            fresh = replace(fresh, host_resource_profile=changed_profile)
        loaded[-1] = fresh
        return fresh

    monkeypatch.setattr(windows_t037, "load_trusted_profile", reload_profile)
    monkeypatch.setattr(changes, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(changes, "_current_windows_binding", lambda _profile: {})
    monkeypatch.setattr(changes.CtypesWindowsWfpMutationApi, "__init__", lambda _self: None)
    monkeypatch.setattr(changes.CtypesWindowsWfpMutationApi,
                        "_appcontainer_no_impersonation", lambda _self: None)
    monkeypatch.setattr(lifecycle, "_native_process_identity",
                        lambda _pid, user, _image: (134371833262561295, user))
    subject = api._service_sid_observe(profile.windows_isolation.service_name)
    subject["status"]["process_id"] = os.getpid()
    candidate_subject = subject
    outcome = []
    service = _ScmApi()

    def service_main(runtime):
        runtime.mark_running()
        admission._bind_daemon_runtime(daemon, runtime)
        issuer = WindowsT037HostControlIssuer(
            authority=authority, provider=provider, profile=profile,
            profile_source_sha256=wrapper.source_sha256,
        )
        request = issuer.issue(runtime, phase="APPLY", plan=plan,
                               authorization_ref="apply-once")
        request.consume(service_name=profile.windows_isolation.service_name,
                        project_id=profile.project_id, profile_sha256=profile.sha256)
        executor = original_factory(
            changes.WindowsHostChangeExecutor, store=store, authority=authority,
            trusted_profile_path=daemon._windows_t037_profile_path,
        )
        record = {"key": ("loader-negative", 1), "request": request,
                  "executor": executor, "first_cause": None}
        daemon._retained_host_control_executors[record["key"]] = record
        accepted = changes._ExecutionAdmission("loader-operation", "loader-job", "loader-admission")
        action = changes._normalize_supported_actions(plan, executor.profile)[1][0]
        actual_executor = executor
        actual_subject = candidate_subject
        if binding_fault == "wrong_loader_origin":
            executor._trusted_profile_load_origin = object()
        elif binding_fault == "profile_path_copy":
            origin = executor._trusted_profile_load_origin
            executor._trusted_profile_load_origin = changes._TrustedProfileLoadOrigin(
                wrapper=origin.profile_wrapper,
                profile_path="different-trusted-profile",
                seal=changes._NATIVE_PROFILE_LOADER_TOKEN,
            )
        elif binding_fault == "manual_executor":
            actual_executor = changes.WindowsHostChangeExecutor._for_software_test(
                store=store, authority=authority, profile=executor.profile,
                api=api, token=changes._SOFTWARE_TEST_TOKEN,
            )
            record["executor"] = actual_executor
        elif binding_fault == "profile_copy":
            executor.profile = copy.deepcopy(executor.profile)
        elif binding_fault == "source_copy":
            origin = executor._trusted_profile_load_origin
            copied_wrapper = replace(origin.profile_wrapper, source_sha256="f" * 64)
            executor._trusted_profile_load_origin = changes._TrustedProfileLoadOrigin(
                wrapper=copied_wrapper,
                profile_path=daemon._windows_t037_profile_path,
                seal=changes._NATIVE_PROFILE_LOADER_TOKEN,
            )
        elif binding_fault == "runtime_copy":
            object.__setattr__(request, "runtime", object())
        elif binding_fault == "subject_copy":
            actual_subject = copy.deepcopy(subject)
            actual_subject["status"]["process_id"] += 1
        try:
            lifecycle.create_native_profile_effect(
                daemon=daemon, request=request, record=record,
                executor=actual_executor, admission=accepted, action=action,
                subject=actual_subject,
            )
        except Exception as error:
            outcome.append(error)
        service.handler(1, 0, None, None)

    try:
        WindowsScmServiceBroker._for_software_test(
            profile.windows_isolation.service_name, service_main,
            api=service, token=_SCM_TEST_API_TOKEN,
        ).run()
        assert len(outcome) == 1
        assert getattr(outcome[0], "code", None) == expected_code
        assert "create" not in sdk.calls
    finally:
        provider.close()
        store.close()
