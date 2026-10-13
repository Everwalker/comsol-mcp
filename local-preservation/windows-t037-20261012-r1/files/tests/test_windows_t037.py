import hashlib
import json

import pytest

from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp import _windows_t037 as t037


def profile_document(**overrides):
    row = {
        "schema": t037.PROFILE_SCHEMA,
        "profile_id": "comsol-t037-test",
        "host_fingerprint_sha256": "a" * 64,
        "service_account_sid": "S-1-5-21-1000",
        "interactive_account_sid": "S-1-5-21-1001",
        "appcontainer_sid": "S-1-15-2-123456789",
        "capability_sids": ["S-1-15-3-123456789"],
        "server_image_sha256": "b" * 64,
        "worker_image_sha256": "c" * 64,
        "workspace_root": "C:\\COMSOL\\private",
        "require_hard_quota": False,
    }
    row.update(overrides)
    return row


def parsed_profile(**overrides):
    encoded = json.dumps(profile_document(**overrides), sort_keys=True).encode()
    return t037.parse_profile_document(
        json.loads(encoded), source_sha256=hashlib.sha256(encoded).hexdigest(),
        source_identity={"device": 4, "inode": 9}, security_semantics="TEST_INPUT_ONLY",
    )


def test_profile_parser_accepts_only_closed_intent_schema():
    profile = parsed_profile()
    assert profile.profile_id == "comsol-t037-test"
    assert profile.require_hard_quota is False
    assert profile.security_semantics == "TEST_INPUT_ONLY"
    assert profile.as_safe_dict()["verification_state"] == "NOT_VERIFIED"
    assert "service_account_sid" not in profile.as_safe_dict()
    assert "workspace_root" not in profile.as_safe_dict()


@pytest.mark.parametrize("override", [
    {"verification_state": "VERIFIED"},
    {"wfp_verified": True},
    {"loopback_policy_verified": True},
    {"service_account_sid": "S-1-5-21-1001"},
    {"capability_sids": ["S-1-15-3-123", "S-1-15-3-123"]},
    {"workspace_root": "\\\\server\\share\\root"},
    {"require_hard_quota": 1},
])
def test_profile_parser_rejects_claimed_proof_malformed_sid_or_unsupported_path(override):
    with pytest.raises(ExecutionContractError):
        parsed_profile(**override)


def test_profile_hashes_bind_each_launch_image_and_host():
    profile = parsed_profile()
    assert profile.server_image_sha256 != profile.worker_image_sha256
    assert profile.host_fingerprint_sha256 == "a" * 64
    assert len(profile.source_sha256) == 64


def test_all_claimed_observation_fields_still_cannot_make_policy_verified():
    profile = parsed_profile()
    assessment = t037.assess_t037_readiness(
        profile,
        current_host_fingerprint=profile.host_fingerprint_sha256,
        service_account_sid=profile.service_account_sid,
        interactive_account_sid=profile.interactive_account_sid,
        appcontainer_sid=profile.appcontainer_sid,
        appcontainer_capabilities=profile.capability_sids,
        job_membership_verified=True,
        loopback_policy_verified=True,
        wfp_policy_verified=True,
        service_identity_verified=True,
        vendor_compatibility_verified=True,
        quota_required=False,
        hard_quota_verified=False,
    )
    assert assessment.state == "UNVERIFIED"
    assert assessment.verified is False
    assert "NATIVE_POLICY_ATTESTATION_NOT_IMPLEMENTED" in assessment.reasons


def test_missing_profile_and_wfp_scm_vendor_evidence_are_typed_refusals():
    assessment = t037.assess_t037_readiness(
        None, current_host_fingerprint=None, service_account_sid=None,
        interactive_account_sid=None, appcontainer_sid=None,
        appcontainer_capabilities=None, job_membership_verified=False,
        loopback_policy_verified=False, wfp_policy_verified=False,
        service_identity_verified=False, vendor_compatibility_verified=False,
        quota_required=False, hard_quota_verified=False,
    )
    assert assessment.state == "UNVERIFIED"
    assert {
        "TRUSTED_PROFILE_NOT_CONFIGURED", "WFP_POLICY_VERIFIER_NOT_IMPLEMENTED",
        "SCM_SERVICE_IDENTITY_VERIFIER_NOT_IMPLEMENTED",
        "VENDOR_RUNTIME_COMPATIBILITY_UNVERIFIED", "LOOPBACK_POLICY_UNVERIFIED",
    }.issubset(set(assessment.reasons))


def test_job_object_memory_semantics_are_not_described_as_rss_or_sandbox():
    profile = parsed_profile(require_hard_quota=True)
    manifest = t037.build_changes_manifest(profile)
    assert manifest["mode"] == "PLAN_ONLY"
    rows = {row["id"]: row for row in manifest["changes"]}
    assert rows["storage-quota"]["required_by_profile"] is True
    assert rows["job-memory"]["state"] == "NOT_CONFIGURED"
    assert all(row["state"] != "VERIFIED" for row in manifest["changes"])
    assert manifest["apply_state"] == manifest["restore_state"] == "NOT_RUN"


@pytest.mark.parametrize("operation", [
    t037.apply_changes_manifest,
    t037.restore_changes_manifest,
])
def test_host_policy_apply_and_restore_refuse_without_mutation(operation):
    with pytest.raises(ExecutionContractError) as captured:
        operation({"mode": "PLAN_ONLY"}, _approval={"approved": True})
    assert captured.value.code == t037.ERROR_UNSUPPORTED


class NeverCalledApi:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def fail(*args, **kwargs):
            self.calls.append(name)
            raise AssertionError(f"native API must not run before policy proof: {name}")
        return fail


def test_session_factory_has_no_caller_proof_argument_or_popen_fallback():
    profile = parsed_profile()
    native = NeverCalledApi()
    factory = t037.T037SessionProcessFactory(profile, "session-123", api=native)
    with pytest.raises(ExecutionContractError) as captured:
        factory(["C:\\COMSOL\\bin\\mphserver.exe"], cwd="C:\\COMSOL")
    assert captured.value.code == t037.ERROR_POLICY_UNVERIFIED
    assert native.calls == []
    with pytest.raises(TypeError):
        t037.T037SessionProcessFactory(profile, "session-123", proof=object())


def test_server_launcher_preflights_before_creating_directories_or_falling_back(tmp_path):
    from comsol_mcp._session_server import OwnedServerLauncher

    factory = t037.T037SessionProcessFactory(parsed_profile(), "session-123", api=NeverCalledApi())
    launcher = OwnedServerLauncher(platform_name="darwin")
    with pytest.raises(ExecutionContractError) as captured:
        launcher.start(None, "project", "session-123", process_factory=factory)
    assert captured.value.code == t037.ERROR_POLICY_UNVERIFIED


def test_worker_preflights_before_runtime_or_javac_process_creation(tmp_path):
    from comsol_mcp._java_worker import PersistentJavaWorker

    class UninspectablePaths:
        def validate(self):
            pytest.fail("T037 refusal must precede runtime/classpath work and javac")

    factory = t037.T037SessionProcessFactory(parsed_profile(), "session-123", api=NeverCalledApi())
    worker = PersistentJavaWorker(UninspectablePaths(), state_dir=tmp_path / "worker",
                                  process_factory=factory)
    with pytest.raises(ExecutionContractError) as captured:
        worker.start()
    assert captured.value.code == t037.ERROR_POLICY_UNVERIFIED
    assert not (tmp_path / "worker").exists()


def test_configured_t037_blocks_trusted_code_before_durable_dispatch(tmp_path, monkeypatch):
    from comsol_mcp._control_daemon import ControlDaemon

    profile = parsed_profile()
    monkeypatch.setattr(t037, "load_trusted_profile", lambda _path: profile)

    class UnverifiedObserver:
        def __init__(self):
            pass

        def assess_profile(self, selected, *, workspace_root=None):
            assert selected is profile
            assert workspace_root == profile.workspace_root
            return t037.T037PolicyAssessment(
                "UNVERIFIED", ("WFP_POLICY_VERIFIER_NOT_IMPLEMENTED",),
                profile.source_sha256, profile.host_fingerprint_sha256,
            )

    monkeypatch.setattr(t037, "WindowsT037Observer", UnverifiedObserver)
    daemon = ControlDaemon(tmp_path / "control", windows_t037_profile="C:\\private\\t037.json")
    monkeypatch.setattr(daemon.store, "begin", lambda **_kwargs: pytest.fail("job was durably admitted"))
    try:
        result = daemon.dispatch({"operation": "code.execute_java", "arguments": {}, "execution": {}})
        assert result["success"] is False
        assert result["error"]["code"] == t037.ERROR_POLICY_UNVERIFIED
        assert result["error"]["stage"] == "validation"
        assert result["error"]["details"]["dispatch_performed"] is False
        assert result["error"]["details"]["reason_codes"] == ["WFP_POLICY_VERIFIER_NOT_IMPLEMENTED"]
    finally:
        daemon.close()


def test_configured_t037_blocks_owned_server_start_before_durable_dispatch(tmp_path, monkeypatch):
    from comsol_mcp._control_daemon import ControlDaemon

    profile = parsed_profile()
    monkeypatch.setattr(t037, "load_trusted_profile", lambda _path: profile)

    class UnverifiedObserver:
        def assess_profile(self, selected, *, workspace_root=None):
            assert selected is profile
            assert workspace_root == profile.workspace_root
            return t037.T037PolicyAssessment(
                "UNVERIFIED", ("SCM_SERVICE_IDENTITY_VERIFIER_NOT_IMPLEMENTED",),
                profile.source_sha256, profile.host_fingerprint_sha256,
            )

    monkeypatch.setattr(t037, "WindowsT037Observer", UnverifiedObserver)
    daemon = ControlDaemon(tmp_path / "control", windows_t037_profile="C:\\private\\t037.json")
    monkeypatch.setattr(daemon, "_authorize_server_control", lambda *_args: None)
    monkeypatch.setattr(daemon.store, "begin", lambda **_kwargs: pytest.fail("job was durably admitted"))
    monkeypatch.setattr(daemon, "_resolve_session_runtime", lambda *_args: pytest.fail("runtime was resolved"))
    try:
        with pytest.raises(ExecutionContractError) as captured:
            daemon._dispatch_session_start(
                {"project_id": "project-a", "runtime_id": "runtime-a", "idempotency_key": "start-a"},
                {},
            )
        assert captured.value.code == t037.ERROR_POLICY_UNVERIFIED
        assert captured.value.details["dispatch_performed"] is False
    finally:
        daemon.close()


def test_configured_t037_blocks_unbound_session_connect_before_durable_dispatch(tmp_path, monkeypatch):
    from comsol_mcp._control_daemon import ControlDaemon

    profile = parsed_profile()
    monkeypatch.setattr(t037, "load_trusted_profile", lambda _path: profile)

    class UnverifiedObserver:
        def assess_profile(self, selected, *, workspace_root=None):
            assert selected is profile
            return t037.T037PolicyAssessment(
                "UNVERIFIED", ("WFP_POLICY_VERIFIER_NOT_IMPLEMENTED",),
                profile.source_sha256, profile.host_fingerprint_sha256,
            )

    monkeypatch.setattr(t037, "WindowsT037Observer", UnverifiedObserver)
    daemon = ControlDaemon(tmp_path / "control", windows_t037_profile="C:\\private\\t037.json")
    monkeypatch.setattr(daemon.store, "begin", lambda **_kwargs: pytest.fail("job was durably admitted"))
    try:
        with pytest.raises(ExecutionContractError) as captured:
            daemon._dispatch_session_connect(
                {"project_id": "project-a", "runtime_id": "runtime-a",
                 "session_id": "session-a", "idempotency_key": "connect-a",
                 "endpoint": {"host": "127.0.0.1", "port": 12345}},
                {},
            )
        assert captured.value.code == t037.ERROR_POLICY_UNVERIFIED
        assert captured.value.details["dispatch_performed"] is False
    finally:
        daemon.close()


def test_control_cli_accepts_profile_option_and_serve_environment(monkeypatch, tmp_path):
    from comsol_mcp import _control_daemon as control

    real_serve = control.serve
    seen = {}
    monkeypatch.setattr(control, "serve", lambda home, **kwargs: seen.update(home=home, **kwargs) or 23)
    assert control.main(["--home", str(tmp_path / "control"),
                         "--windows-t037-profile", "C:\\private\\t037.json"]) == 23
    assert seen["windows_t037_profile"] == "C:\\private\\t037.json"
    monkeypatch.setattr(control, "serve", real_serve)

    captured = {}

    class FakeLock:
        def __init__(self, _path):
            pass

        def close(self):
            pass

    class FakeDaemon:
        def __init__(self, home, **kwargs):
            captured.update(home=home, **kwargs)

    class FakeHttpServer:
        server_port = 43210
        daemon_threads = False

        def __init__(self, *_args):
            pass

        def serve_forever(self):
            pass

    from comsol_mcp import _state_backup
    monkeypatch.setattr(control, "ProcessLock", FakeLock)
    monkeypatch.setattr(_state_backup, "assert_no_pending_restore", lambda _home: None)
    monkeypatch.setattr(control, "ControlDaemon", FakeDaemon)
    monkeypatch.setattr(control, "ThreadingHTTPServer", FakeHttpServer)
    monkeypatch.setenv("COMSOL_MCP_WINDOWS_T037_PROFILE", "C:\\private\\env-t037.json")
    monkeypatch.delenv("COMSOL_MCP_HOST_PROVIDER_CONFIG", raising=False)
    monkeypatch.delenv("COMSOL_MCP_DISK_RESOURCE_CONFIG", raising=False)
    control.serve(tmp_path / "serve-home")
    assert captured["windows_t037_profile"] == "C:\\private\\env-t037.json"


def test_process_factory_rejects_noncanonical_session_identity():
    with pytest.raises(ExecutionContractError):
        t037.T037SessionProcessFactory(parsed_profile(), "../wrong", api=NeverCalledApi())


def test_native_observer_fails_closed_outside_windows():
    with pytest.raises(ExecutionContractError) as captured:
        t037.WindowsT037Observer(platform_name="darwin")
    assert captured.value.code == t037.ERROR_UNSUPPORTED


def test_feasibility_cli_snapshot_on_nonwindows_is_safe_and_not_run():
    report = t037.feasibility_snapshot()
    assert report["state"] == "UNSUPPORTED_HOST"
    assert report["native_policy"] == report["host_changes"] == "NOT_RUN"
    assert report["sensitive_values_included"] is False
    assert "workspace_root" not in json.dumps(report)
