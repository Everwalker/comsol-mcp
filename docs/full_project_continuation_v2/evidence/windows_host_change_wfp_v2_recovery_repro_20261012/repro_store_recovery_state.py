from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

import pytest


def main() -> int:
    repository = Path(sys.argv[1]).resolve()
    test_source = repository / "tests" / "test_windows_host_change.py"
    spec = importlib.util.spec_from_file_location("test_windows_host_change_repro", test_source)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load existing production-class test helpers")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    temp_root = Path(tempfile.mkdtemp(prefix="host-control-recovery-repro-", dir="/private/tmp"))
    monkeypatch = pytest.MonkeyPatch()
    fixture = module.host_env.__wrapped__(temp_root, monkeypatch)
    profile, _old_plan, _old_action, _old_api, store, old_provider, authority, _old_executor = next(fixture)
    try:
        old_provider.close()
        plan = module._provider_sublayer_plan(profile, with_filter=True)
        authorization_ref = "recovery-required-crash-repro"
        provider = module._write_provider(
            temp_root / "provider-config-repro",
            profile,
            [module._apply_grant(profile, plan, authorization_ref)],
        )
        authority.authorization_verifier = provider
        api = module.FakeWfpSequence(profile)
        api.fail_after = "wfp_sublayer"
        executor = module.WindowsHostChangeExecutor._for_software_test(
            store=store,
            authority=authority,
            profile=profile,
            api=api,
            token=module._SOFTWARE_TEST_TOKEN,
        )

        original_finish = store.finish

        def crash_before_unknown_is_durable(operation_id, *, status, result):
            if status == "UNKNOWN":
                raise OSError("repro: simulated process/store failure before terminal UNKNOWN write")
            return original_finish(operation_id, status=status, result=result)

        store.finish = crash_before_unknown_is_durable
        try:
            executor.apply(plan, approval=module.resources.HostChangeApproval(plan.sha256, authorization_ref))
        except module.WindowsHostChangeError as exc:
            assert exc.code == "HOST_CHANGE_RECOVERY_REQUIRED", exc.code
            apply_error = {"code": exc.code, "safe_retry": exc.safe_retry}
        else:
            raise AssertionError("expected the injected post-effect failure to fence the apply")

        admission = store.db.execute(
            "SELECT * FROM host_control_admissions WHERE phase='APPLY'"
        ).fetchone()
        assert admission is not None
        operation = store.get_operation(admission["operation_id"])
        job = store.job(admission["job_id"])
        events = module._events_v2(store, admission["job_id"])
        assert admission["state"] == "RECOVERY_REQUIRED"
        assert operation["status"] == job["status"] == "RUNNING"
        assert [item["event"] for item in events] == [
            "APPLY_STEP_INTENT", "APPLY_STEP_READBACK_VERIFIED",
            "APPLY_STEP_INTENT", "RECOVERY_REQUIRED",
        ]

        pending = events[-2]["payload"]
        assert pending["step_index"] == 1
        forged_readback = {
            **pending,
            "created_resource_sha256": "a" * 64,
        }
        verified = store.append_host_control_mutation_step_event(
            admission_id=admission["admission_id"],
            event="APPLY_STEP_READBACK_VERIFIED",
            payload=forged_readback,
        )
        state_after_readback = store.host_control_admission_state(admission["admission_id"])

        baseline = json.loads(admission["baseline_json"])
        third = baseline["mutation_sequence"]["steps"][2]
        next_intent = {
            "step_index": 2,
            "action_id": third["action_id"],
            "resource_kind": third["resource_kind"],
            "resource_key": third["resource_key"],
            "desired_identity_sha256": third["identity_sha256"],
            "dependency_sha256": third["dependency_sha256"],
        }
        continued = store.append_host_control_mutation_step_event(
            admission_id=admission["admission_id"],
            event="APPLY_STEP_INTENT",
            payload=next_intent,
        )

        result = {
            "classification": "SOFTWARE_ONLY_PRODUCTION_STORE_AND_EXECUTOR_REPRO",
            "native_windows": "NOT_RUN",
            "terminal_unknown_write_injected_failure": True,
            "apply_error": apply_error,
            "state_before_untrusted_store_call": {
                "admission": admission["state"],
                "operation": operation["status"],
                "job": job["status"],
                "journal_events": [item["event"] for item in events],
            },
            "store_accepted_readback_after_recovery_required": True,
            "state_after_readback": state_after_readback,
            "store_accepted_next_step_intent": True,
            "state_after_next_intent": continued["state"],
            "actual_fake_api_calls_before_direct_store_calls": [item[:2] for item in api.calls],
            "direct_store_calls_are_not_native_or_os_mutations": True,
        }
        print(json.dumps(result, sort_keys=True, indent=2))
        assert state_after_readback != "RECOVERY_REQUIRED"
        assert continued["state"] == "APPLY_STEP_INTENT:2"
        return 0
    finally:
        try:
            fixture.close()
        finally:
            monkeypatch.undo()
            import shutil

            shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
