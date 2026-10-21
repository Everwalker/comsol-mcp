from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
WRAPPER = ROOT / "repository/_state/windows-primary-owner-r6d-readonly-inspector-wrapper-r4-20261015/source/windows_primary_owner_r6d_readonly_ssh_wrapper.py"
_spec = importlib.util.spec_from_file_location("windows_primary_owner_r6d_readonly_ssh_wrapper_r4", WRAPPER)
assert _spec is not None and _spec.loader is not None
wrapper = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wrapper)


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def refuse_process_birth(*_args, **_kwargs):
    raise AssertionError("source validation must not create a process")


def test_actual_main_source_review_passes_then_stops_at_missing_execution_authorization() -> None:
    actual = wrapper.read_regular(wrapper.DIAGNOSTIC_SOURCE_REVIEW_PATH)
    actual_record = wrapper.strict_json(actual, "actual R3 Main source review")
    assert wrapper.sha(actual) == wrapper.DIAGNOSTIC_SOURCE_REVIEW_SHA256
    assert actual_record["schema"] == "windows-r6d-diag-r3-main-source-review/1"
    assert actual_record["candidate_path"] == str(wrapper.INSPECTOR_PATH.relative_to(wrapper.ROOT))
    assert actual_record["candidate_sha256"] == wrapper.INSPECTOR_SHA256
    assert not wrapper.MAIN_REVIEW_PATH.exists()
    assert not wrapper.RUN_ROOT.exists()

    with patch.object(wrapper.subprocess, "Popen", side_effect=refuse_process_birth):
        try:
            wrapper.validate_inputs()
        except RuntimeError as exc:
            assert str(wrapper.MAIN_REVIEW_PATH) in str(exc)
            assert "ordinary local file" in str(exc)
        else:
            raise AssertionError("validate_inputs must stop at the absent exact Main execution authorization")

    assert not wrapper.MAIN_REVIEW_PATH.exists()
    assert not wrapper.RUN_ROOT.exists()


def test_wrong_candidate_in_real_producer_record_is_refused_before_execution_authorization() -> None:
    original = wrapper.read_regular

    def altered_read(path: Path, *, maximum: int = 32 * 1024 * 1024) -> bytes:
        raw = original(path, maximum=maximum)
        if path == wrapper.DIAGNOSTIC_SOURCE_REVIEW_PATH:
            record = wrapper.strict_json(raw, "actual R3 Main source review")
            record["candidate_sha256"] = "0" * 64
            return canonical(record)
        return raw

    with patch.object(wrapper, "read_regular", side_effect=altered_read),          patch.object(wrapper.subprocess, "Popen", side_effect=refuse_process_birth):
        try:
            wrapper.validate_inputs()
        except RuntimeError as exc:
            assert "exact producer record" in str(exc)
        else:
            raise AssertionError("wrong candidate hash must be refused")
    assert not wrapper.RUN_ROOT.exists()


def test_modified_review_bytes_with_valid_candidate_are_refused_by_fixed_record_hash() -> None:
    original = wrapper.read_regular

    def altered_read(path: Path, *, maximum: int = 32 * 1024 * 1024) -> bytes:
        raw = original(path, maximum=maximum)
        if path == wrapper.DIAGNOSTIC_SOURCE_REVIEW_PATH:
            record = wrapper.strict_json(raw, "actual R3 Main source review")
            record["review"] += " tamper"
            return canonical(record)
        return raw

    with patch.object(wrapper, "read_regular", side_effect=altered_read),          patch.object(wrapper.subprocess, "Popen", side_effect=refuse_process_birth):
        try:
            wrapper.validate_inputs()
        except RuntimeError as exc:
            assert "exact Main-reviewed record" in str(exc)
        else:
            raise AssertionError("changed review bytes must be refused")
    assert not wrapper.RUN_ROOT.exists()
