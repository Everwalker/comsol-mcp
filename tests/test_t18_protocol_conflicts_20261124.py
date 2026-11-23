"""Independent in-memory negative controls for the v4 review findings."""
import hashlib

import tests.test_t18_java_diagnostic_20261124 as base


def test_success_conflict_flags_and_top_level_identities_fail_closed():
    contradictory = base.success_reply()
    contradictory["success"] = False
    receipts = [contradictory]
    for name, value in (
        ("source_sha256", "f" * 64),
        ("entrypoint", "OtherTask"),
        ("model_tag", "other-model"),
    ):
        receipt = base.success_reply()
        receipt[name] = value
        receipts.append(receipt)
    for receipt in receipts:
        fixture = base.make_fixture(receipt)
        base.assert_unknown(fixture, base.invoke_fixture(fixture))


def test_matching_optional_top_level_identity_and_success_flag_are_accepted():
    receipt = base.success_reply()
    receipt.update({
        "success": True,
        "source_sha256": hashlib.sha256(base.SOURCE).hexdigest(),
        "entrypoint": "NewTask",
        "model_tag": base.MODEL_TAG,
    })
    fixture = base.make_fixture(receipt)
    result = base.invoke_fixture(fixture)
    assert result["success"] is True
    assert result["data"]["execution_success"] is True
    assert fixture.adapter.calls == [
        ("model_snapshot", base.MODEL_TAG), ("model_snapshot", base.MODEL_TAG),
    ]
    assert fixture.worker.calls == [
        ("backend_snapshot", base.MODEL_TAG), ("execute_java", base.WORKER_REQUEST_ID),
        ("backend_snapshot", base.MODEL_TAG),
    ]


def test_compile_and_policy_rejections_fail_closed_on_top_level_identity_conflicts():
    receipts = []
    for make_receipt in (base.compile_failure, base.trusted_code_rejected):
        for name, value in (
            ("source_sha256", "f" * 64),
            ("entrypoint", "OtherTask"),
            ("model_tag", "other-model"),
        ):
            receipt = make_receipt()
            receipt[name] = value
            receipts.append(receipt)
    for receipt in receipts:
        fixture = base.make_fixture(receipt)
        base.assert_unknown(fixture, base.invoke_fixture(fixture))


def test_trusted_policy_marker_must_match_exact_java_protocol_value():
    missing = base.trusted_code_rejected()
    del missing["failure"]["policy"]
    wrong = base.trusted_code_rejected()
    wrong["failure"]["policy"] = "different_policy"
    for receipt in (missing, wrong):
        fixture = base.make_fixture(receipt)
        base.assert_unknown(fixture, base.invoke_fixture(fixture))


def test_python_source_binding_exception_reply_remains_unknown():
    error = base.attach_reply(
        base.JavaWorkerError("source-binding refusal is attached to an exception"),
        base.source_binding_failure(),
    )
    fixture = base.make_fixture(error)
    base.assert_unknown(fixture, base.invoke_fixture(fixture))


if __name__ == "__main__":
    tests = (
        test_success_conflict_flags_and_top_level_identities_fail_closed,
        test_matching_optional_top_level_identity_and_success_flag_are_accepted,
        test_compile_and_policy_rejections_fail_closed_on_top_level_identity_conflicts,
        test_trusted_policy_marker_must_match_exact_java_protocol_value,
        test_python_source_binding_exception_reply_remains_unknown,
    )
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
