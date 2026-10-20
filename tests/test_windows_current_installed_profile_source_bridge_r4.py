from __future__ import annotations

import json
import subprocess

import pytest

from tools import windows_current_installed_profile_source_bridge_r4 as bridge


def test_current_latest_profile_fails_closed_without_reviewed_reader_or_app():
    readiness = bridge.current_source_readiness()

    latest = bridge.application.LATEST_SOURCE_PROFILE
    assert latest in {"current175-r8", "current175-r9"}
    assert readiness["status"].startswith("NOT_READY_")
    assert readiness["latest_source_profile"] == latest
    if latest == bridge.R8_PROFILE:
        assert readiness["source_lock"]["source_profile"] == latest
        assert readiness["r8_reader"]["exists"] is False
        assert readiness["r8_actual_app_artifact"] == "NOT_SUPPLIED_TO_THIS_BRIDGE"
        assert readiness["live_source_membership"]["status"] == "DIFFERS_FROM_PREPARED_LOCK"
    else:
        assert readiness["source_lock_candidate"]["source_profile"] == latest
        assert readiness["source_lock_candidate"]["reviewed_sha256"] is None
        assert readiness["reader"]["exists"] is False
        assert readiness["current_actual_app_artifact"] == "NOT_SUPPLIED_TO_THIS_BRIDGE"
        if readiness["source_lock_candidate"]["exists"]:
            assert readiness["source_lock_candidate"]["status"].startswith("UNPINNED_GENERATION")
            assert readiness["source_lock_candidate"]["declared_profile"] == latest
            assert readiness["source_lock_candidate"]["observed_member_record_count"] == 175
            assert len(readiness["source_lock_candidate"]["observed_member_fingerprint_sha256"]) == 64
            assert type(readiness["source_lock_candidate"]["live_source_matches_observed_lock"]) is bool
    assert readiness["current_runtime_binding_created"] is False

    with pytest.raises(bridge.CurrentSourceNotReady) as captured:
        bridge.require_current_source_ready({"source_profile": latest})
    assert captured.value.readiness["status"] == readiness["status"]
    assert captured.value.readiness["current_runtime_binding_created"] is False


def test_current_latest_route_refuses_a_historical_r7_label():
    with pytest.raises(bridge.CurrentSourceNotReady) as captured:
        bridge.require_current_source_ready({"source_profile": "current175-r7"})
    assert captured.value.readiness["latest_source_profile"] == bridge.application.LATEST_SOURCE_PROFILE
    assert captured.value.readiness["requested_source_profile"] == "current175-r7"
    assert captured.value.readiness["current_runtime_binding_created"] is False


def test_profile_names_are_bound_to_exact_lock_and_member_fingerprints():
    r7 = bridge.source_lock_binding("current175-r7")
    r8 = bridge.source_lock_binding("current175-r8")
    assert r7["sha256"] == bridge.R7_LOCK_SHA256
    assert r7["member_fingerprint_sha256"] == bridge.R7_MEMBER_FINGERPRINT_SHA256
    assert r7["source_profile"] == "current175-r7"
    assert r8["sha256"] == bridge.R8_PREPARED_LOCK_SHA256
    assert r8["member_fingerprint_sha256"] == bridge.R8_MEMBER_FINGERPRINT_SHA256
    assert r8["source_profile"] == "current175-r8"

    lock = json.loads(bridge.application.CURRENT175_R7_SOURCE_LOCK.read_text())
    wrong_profile = dict(lock, profile="current175-r6")
    with pytest.raises(RuntimeError, match="profile/schema/counts"):
        bridge._validate_lock_value(wrong_profile, "current175-r7", bridge.R7_MEMBER_FINGERPRINT_SHA256)
    changed_members = dict(lock["members"])
    first_name = sorted(changed_members)[0]
    changed_members[first_name] = dict(changed_members[first_name], sha256="0" * 64)
    tampered = dict(lock, members=changed_members)
    with pytest.raises(RuntimeError, match="member fingerprint"):
        bridge._validate_lock_value(tampered, "current175-r7", bridge.R7_MEMBER_FINGERPRINT_SHA256)


def test_r6_remains_historical_and_is_not_routed_through_the_successor_bridge():
    with pytest.raises(bridge.CurrentSourceNotReady) as captured:
        bridge.source_lock_binding("current175-r6")
    assert captured.value.readiness["status"] == "NOT_READY_UNSUPPORTED_SOURCE_PROFILE"
    assert captured.value.readiness["r6_is_preserved_by"] == (
        "windows_current_installed_profile_runtime_binding_r3"
    )


def test_unmocked_r7_reader_audits_actual_app7a_bytes_without_process_creation(monkeypatch):
    def forbid_popen(*_args, **_kwargs):
        pytest.fail("source-profile verification must not create a process")

    monkeypatch.setattr(subprocess, "Popen", forbid_popen)
    report = bridge.verify_exact_r7_app7a_artifact()

    assert report["source_lock"]["sha256"] == bridge.R7_LOCK_SHA256
    assert report["source_lock"]["member_fingerprint_sha256"] == bridge.R7_MEMBER_FINGERPRINT_SHA256
    assert report["result"]["sha256"] == bridge.R7_APP7A_RESULT_SHA256
    assert report["wheel"]["sha256"] == bridge.R7_APP7A_WHEEL_SHA256
    assert report["archive_review"]["sha256"] == bridge.R7_ARCHIVE_REVIEW_SHA256
    assert report["reader"] == "tools.w26_sealed_data_r7.current_application_profile"
    assert report["current_source_profile"] == bridge.application.LATEST_SOURCE_PROFILE
    assert report["current_binding_eligible"] is False
    assert report["current_runtime_binding_created"] is False
    assert report["status"] == "REFUSED_R7_READER_DETECTED_SOURCE_DRIFT"
    assert report["reader_error"].endswith("fixed regular checkpoint changed")
    assert {row["member"] for row in report["original_source_differences"]} == {
        "comsol_mcp/_desktop_macos.py",
        "comsol_mcp/_desktop_service.py",
        "comsol_mcp/data/science_recipes/SOURCE_ORIGINS.json",
        "comsol_mcp/data/science_recipes/java/W24CureCouponFixture.java",
    }
