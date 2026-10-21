#!/usr/bin/env python3
"""Prepare or execute one source-bound, task-owned macOS COMSOL GUI observation.

This is an actor-only R9 over the immutable installed current175-r9 product
caller. It adds narrowly gated recoveries for the reviewed R7 product's
literal pre-AX identity-loss error after a real transient AXWindows readiness
error and for one proven owned-child exit before the first visible window. It
does not change the 175-member product source, lock, wheel, or venv.
Preparation never starts a process. Execution still requires a separate Main
review, one-use binding, fresh signed W24/T044 lane check, and exact installed
R9 caller. Its only COMSOL action is one blank standalone launch followed by
bounded AX metadata inspection; it never controls a window or binds a
model/server.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import math
import os
import plistlib
import re
import signal
import stat
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


REPOSITORY = Path(__file__).resolve().parents[1]
STATE = REPOSITORY / "docs/full_project_continuation_v3/state"
INSTALL_ACTUAL_REPORT = STATE / "W25_CURRENT175_R9_OFFLINE_INSTALL_ACTUAL_REPORT_R1.json"
APP_REPORT = Path("/private/tmp/cvw26-application-4cab1cfc/SOURCE_EXTERNAL_R9_READER_RESULT.json")
APP_ACTUAL_REVIEW = STATE / "W26_R9_MAIN_ACTUAL_APP_ARTIFACTS_REVIEW_R1.json"
INSTALL_ACTUAL_REVIEW = STATE / "W25_R9_INSTALL_R1_MAIN_ACTUAL_EVIDENCE_REVIEW_R1.json"
R9_LOCK = REPOSITORY / "tools/w26_application_sources_175_r9_prepared.lock.json"
INSTALL_ACTOR = REPOSITORY / "tools/w25_current_r9_offline_install_actor.py"
RESUME = STATE / "RESUME.json"
LAUNCHER = Path("/Applications/COMSOL64/Multiphysics/bin/comsol")
BUNDLE_INFO = Path("/Applications/COMSOL64/Multiphysics/COMSOL Multiphysics.app/Contents/Info.plist")
BUILDER_PYTHON = Path("/private/tmp/cvw26-dependencies-2cd91a47/builder/bin/python")
HELP_SHA256 = "36a9fa631e932121c1d6332d43dd86b433d80f237b88463d54544c310788500b"
LICENSE_HELP_SHA256 = "b8633b7b8e5ce46eb12489830e6c50ba07364d0a616886da0e6fab06ec2df474"
CURRENT_R9 = "current175-r9"
R7_GUI_R2_ROOT = Path("/private/tmp/cvw25-macos-198c6c77")
R7_GUI_R2_SOURCE_CANDIDATE = STATE / "W25_CURRENT175_R7_GUI_SOURCE_CANDIDATE_R2.json"
R7_GUI_R2_ACTUAL_REPORT = STATE / "W25_CURRENT175_R7_MACOS_GUI_R2_ACTUAL_REPORT_R1.json"
R7_GUI_R2_MAIN_ACTUAL_REVIEW = STATE / "W25_R7_GUI_R2_MAIN_ACTUAL_FAILED_EVIDENCE_REVIEW_R1.json"
APP_VERSION = "0.1.10"
APP_SOURCE_HASHES = {
    "comsol_mcp._desktop_macos": "99e2128d6a28cabed58bb455ef74da5b0cd45c0e46c419eb2a65c682b37e156b",
    "comsol_mcp._desktop_platforms": "4c50c4596cbc8d79dd610d66cada1deb38e400c607a5bb04b59f1766a9c76059",
    "comsol_mcp._desktop_service": "00691307ad6b2c780f7086b4a306b4adbb5360e986754c3e9e97e2a4ea38dcf1",
    "comsol_mcp._desktop_selector_profile": "c29d6a439e3d2fb1e2b0e55b04486335d3ec22c15527e16d3e72ef67855b6c6c",
}
CURRENT_DESKTOP_SOURCE_HASHES = dict(APP_SOURCE_HASHES)
DESKTOP_MODULES = {
    "comsol_mcp._desktop_macos": "_desktop_macos.py",
    "comsol_mcp._desktop_platforms": "_desktop_platforms.py",
    "comsol_mcp._desktop_service": "_desktop_service.py",
    "comsol_mcp._desktop_selector_profile": "_desktop_selector_profile.py",
}
DESKTOP_OVERLAY_SCHEMA = "w25-current175-r9-desktop-source-overlay/1"
BOUNDED_RUNTIME_SECONDS = 600
CLEANUP_SECONDS = 60
ACTIVE_RUNTIME_SECONDS = BOUNDED_RUNTIME_SECONDS - CLEANUP_SECONDS
MAX_OUTPUT_BYTES = 64 * 1024 * 1024
MAX_AX_DEPTH = 2
MAX_AX_NODES = 64
MAX_PROCESS_ROWS = 64
LANE_MAX_AGE_SECONDS = 300
AX_READINESS_SECONDS = 60
MAX_AX_WINDOW_RESELECTIONS = 2
MAX_STARTUP_CHILD_EXIT_RECOVERIES = 8
PREAX_REVALIDATION_SAMPLE_SECONDS = 1.0
PREAX_REVALIDATION_MAX_SAMPLES = AX_READINESS_SECONDS
MAX_OWNED_QUARTZ_ROWS_PER_SAMPLE = 16
PREAX_IDENTITY_LOSS_LITERAL = "target process/window identity changed before AX observation"
STARTUP_CHILD_EXIT_ERROR_MESSAGES = frozenset({
    "task-owned process identity is unavailable",
    "task-owned process identity could not be revalidated",
})
APP_ACTUAL_REVIEW_STATUS = "PASS_SCOPED_R9_ACTUAL_APP_ARCHIVES_AND_UNMOCKED_READER_NOT_RELEASE"
R9_APP_ARCHIVE_MEMBER_COUNTS = {"macos_arm64": 58, "macos_x86_64": 60, "win_amd64": 47}
R9_APP_TARGET_WHEEL_COUNTS = {"macos_arm64": 42, "macos_x86_64": 42, "win_amd64": 35}


def _bounded_owned_window_plan_contract() -> tuple[dict[str, Any], list[str]]:
    """Return the generated-plan contract for the implemented bounded transition."""
    ownership = {
        "owned_window_collector": {
            "api": "shared internal TaskOwnedMacOSWindowObserver._collect_owned_window_observations",
            "max_owned_rows_per_inventory": MAX_OWNED_QUARTZ_ROWS_PER_SAMPLE,
            "exact_original_popen_and_full_process_tree_revalidated_before_and_after_each_inventory": True,
            "foreign_quartz_fields": "read OwnerPID only, then discard",
        },
        "owned_window_transition": {
            "approved_triggers": [
                "exact transient AXWindows -25204 or -25212 readiness error",
                "exact initial AXPosition/AXSize -25202 invalid_ui_element error in window_association scope",
                "exact pre-AX identity-loss literal after the last AXWindows readiness retry",
            ],
            "maximum_samples": PREAX_REVALIDATION_MAX_SAMPLES,
            "sample_interval_seconds": PREAX_REVALIDATION_SAMPLE_SECONDS,
            "fixed_shared_deadline_seconds": AX_READINESS_SECONDS,
            "deadline_is_not_reset_between_readiness_and_transition": True,
            "global_max_total_refresh_or_reselection_actions": MAX_AX_WINDOW_RESELECTIONS,
            "zero_or_multiple_owned_rows": "persist identity-only receipt and wait only after an approved trigger; do not inspect AX nodes or select a target while ambiguous",
            "initial_ambiguity_without_approved_trigger": "terminal without AX observation or target selection",
            "one_unique_same_native_id": "refresh only after exact same-owner identity is revalidated",
            "one_unique_new_native_id": "accept only when the old native ID is absent and the exact same owner PID, birth, UID, session, version, and process-tree identity are proven",
            "other_identity_change_or_ambiguity_at_deadline": "terminal",
        },
        "foreign_quartz_rows": "read OwnerPID only then discard; no foreign number/title/bounds/owner-name/AX/pixels",
        "cleanup": "revalidate each observed task-owned identity; SIGTERM owned members only; allow up to 30s within the original total deadline; SIGKILL only still-matching owned members while reserve remains; do not reset the deadline; verify owned PGID/SID absent; if owner state remains unknown past budget, retain it and record budget_exceeded",
    }
    stop_conditions = [
        "active launch/window/AX observation stops at total deadline minus the fixed cleanup reserve",
        "cleanup uses only the original total deadline; no cleanup timer reset",
        "permission/status unavailable or prompt needed",
        "lane report stale/held/mismatched",
        "candidate/source/root drift",
        "process identity drift or PID reuse",
        "owned-window ambiguity remains unresolved at the fixed shared deadline",
        "new native ID lacks an exact approved trigger, unique same-owner proof, or old-ID-absent proof",
        "more than two total bounded owned-window refresh/reselection actions",
        "AX error outside the exact readiness scopes/codes or narrowly gated initial AXREF refresh",
        "empty/nonfinite AX or Quartz window geometry",
        "AX tree truncated or over bound",
        "output exceeds cap",
        "COMSOL/license/setup prompt (observe metadata only, never answer)",
        "any cleanup identity cannot be revalidated",
    ]
    return ownership, stop_conditions


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _write_new(path: Path, value: Any, mode: int = 0o600) -> dict[str, Any]:
    raw = value if isinstance(value, bytes) else (json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2) + "\n").encode()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, mode)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(fd)
    return file_record(path)


def file_record(path: Path) -> dict[str, Any]:
    path = Path(path)
    st = path.lstat()
    if not stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode):
        raise RuntimeError(f"regular non-symlink file required: {path}")
    raw = path.read_bytes()
    after = path.lstat()
    first = [st.st_dev, st.st_ino, st.st_uid, stat.S_IMODE(st.st_mode), st.st_nlink, st.st_size, st.st_mtime_ns]
    current = [after.st_dev, after.st_ino, after.st_uid, stat.S_IMODE(after.st_mode), after.st_nlink, after.st_size, after.st_mtime_ns]
    if first != current or len(raw) != st.st_size:
        raise RuntimeError(f"file changed during read: {path}")
    return {"path": str(path), "device": st.st_dev, "inode": st.st_ino, "uid": st.st_uid,
            "mode": stat.S_IMODE(st.st_mode), "nlink": st.st_nlink, "bytes": len(raw), "sha256": _sha(raw)}


def _record_matches(record: Mapping[str, Any]) -> bool:
    actual = file_record(Path(str(record["path"])))
    return all(actual.get(k) == record.get(k) for k in ("path", "device", "inode", "uid", "mode", "nlink", "bytes", "sha256"))


def _read_json(path: Path) -> dict[str, Any]:
    record = file_record(path)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def _secure_root(root: Path) -> dict[str, int]:
    root = Path(root)
    if root.parent != Path("/private/tmp") or not re.fullmatch(r"cvw25-macos-[0-9a-f]{8}", root.name):
        raise RuntimeError("W25 root must use the fresh /private/tmp/cvw25-macos-<8hex> namespace")
    st = root.lstat()
    if not stat.S_ISDIR(st.st_mode) or stat.S_ISLNK(st.st_mode) or st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o700:
        raise RuntimeError("W25 root must be an owned, non-symlink 0700 directory")
    if root.resolve(strict=True) != root:
        raise RuntimeError("W25 root path must not resolve through aliases")
    return {"device": st.st_dev, "inode": st.st_ino}


def _capture_static(path: Path, *, maximum: int = 16 * 1024 * 1024) -> dict[str, Any]:
    record = file_record(path)
    if record["bytes"] > maximum:
        raise RuntimeError(f"static input exceeds cap: {path}")
    return record


def _capture_external_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """Validate W26 helper records (dev/inode schema) and return W25 records."""
    actual = file_record(Path(str(record["path"])))
    aliases = {"device": "dev", "inode": "inode", "uid": "uid", "mode": "mode",
               "nlink": "nlink", "bytes": "bytes", "sha256": "sha256", "path": "path"}
    for field, alias in aliases.items():
        expected = record.get(field, record.get(alias))
        if expected != actual[field]:
            raise RuntimeError(f"external first-record mismatch for {actual['path']}: {field}")
    return actual


def _capture_r9_source_lock(record: Mapping[str, Any]) -> dict[str, Any]:
    """Rehash only the exact R9 lock, allowing its canonical repository-relative record spelling."""
    relative = R9_LOCK.relative_to(REPOSITORY).as_posix()
    supplied_path = record.get("path")
    if supplied_path not in (str(R9_LOCK), relative):
        raise RuntimeError("current175-r9 source-lock record path is not the exact locked file")
    normalized = dict(record)
    normalized["path"] = str(R9_LOCK)
    return _capture_external_record(normalized)


def _capture_r7_gui_r2_failure_evidence() -> dict[str, dict[str, Any]]:
    """Bind the failed R2 authority, failure, post-terminal proof, and preserved R7 actor bytes."""
    candidates = {
        "candidate": R7_GUI_R2_ROOT / "ACTOR_CANDIDATE_macos1.json",
        "source_map": R7_GUI_R2_ROOT / "CURRENT_ACTOR_SOURCE_macos1.json",
        "main_source_review": R7_GUI_R2_ROOT / "MAIN_SOURCE_REVIEW.json",
        "main_once_binding": R7_GUI_R2_ROOT / "MAIN_ONCE_BINDING.json",
        "consumed_once": R7_GUI_R2_ROOT / "UNIT_ONCE_GUARD.json",
        "lane_check": R7_GUI_R2_ROOT / "W25_LANE_CHECK_CURRENT.json",
        "first_failure": R7_GUI_R2_ROOT / "FIRST_FAILURE.json",
        "post_terminal_check": R7_GUI_R2_ROOT / "POST_TERMINAL_OWNER_CHECK_R1.json",
        "preservation_receipt": R7_GUI_R2_ROOT / "source-evidence/ACTOR_SOURCE_PRESERVATION_RECEIPT_R1.json",
        "preserved_r7_actor": R7_GUI_R2_ROOT / "source-evidence/w25_macos_owned_desktop_actor_r7.py",
        "source_candidate_report": R7_GUI_R2_SOURCE_CANDIDATE,
        "actual_report": R7_GUI_R2_ACTUAL_REPORT,
        "main_actual_review": R7_GUI_R2_MAIN_ACTUAL_REVIEW,
    }
    records = {name: _capture_static(path) for name, path in candidates.items()}
    expected_hashes = {
        "candidate": "c4f68b784a70015880b50b0dfc3d95c1ef7e83f0128280d8aecf04de69b03748",
        "source_candidate_report": "c7e245bb2b06e5fe60db0a92e8a8a96e1773a27accf81cbc6aa9b0dbdd844edc",
        "actual_report": "253c23fc2f64dc4737aa9104d84ebbdb162cd46339d0a22f3dc588d2b7fee3d7",
        "main_actual_review": "18b437a15f089e78e95ed3588e13848b6971c63ffe6569c15ed4ecfe699331e0",
        "consumed_once": "b70e506cb52a6a1aaf99e07de2425242651d893e45558437dbe4436a61e32e01",
        "first_failure": "97725770741870e51f91cb929ae3afbdbf235eb5d0ec9fb8a93e9fa564ac22da",
        "post_terminal_check": "b7576c38e2504bbbc48b8af29f1d09ce314387ac9b677ba97744f2b97c865e3e",
        "preservation_receipt": "c1a913da30951b5d3a72e217bb4efc72c5ac2f99f84e28c9a8a894206284f46c",
        "preserved_r7_actor": "77734d50d23704a1bd65284a356d4e187ae3925f5a58cb0c02be2940021b21ae",
    }
    for name, digest in expected_hashes.items():
        if records[name]["sha256"] != digest:
            raise RuntimeError(f"preserved R7 GUI R2 evidence digest changed: {name}")

    candidate = _read_json(candidates["candidate"])
    review = _read_json(candidates["main_source_review"])
    binding = _read_json(candidates["main_once_binding"])
    consumed = _read_json(candidates["consumed_once"])
    failure = _read_json(candidates["first_failure"])
    post_terminal = _read_json(candidates["post_terminal_check"])
    preservation = _read_json(candidates["preservation_receipt"])
    actual = _read_json(candidates["actual_report"])
    actual_review = _read_json(candidates["main_actual_review"])
    source_map = _read_json(candidates["source_map"])
    backup = preservation.get("source_backup_record")
    source_entry = source_map.get(preservation.get("source_map_entry"))
    if candidate.get("root") != str(R7_GUI_R2_ROOT) or candidate.get("root_identity") != actual.get("candidate_root_identity") or \
            review.get("status") != "PASS_W25_MACOS_OWNED_DESKTOP_SOURCE_FOR_EXACT_CANDIDATE" or \
            review.get("candidate_sha256") != records["candidate"]["sha256"] or \
            binding.get("candidate_sha256") != records["candidate"]["sha256"] or \
            binding.get("review_sha256") != records["main_source_review"]["sha256"] or \
            consumed.get("candidate_sha256") != records["candidate"]["sha256"] or \
            consumed.get("binding_sha256") != records["main_once_binding"]["sha256"] or \
            actual.get("status") != "FAIL_PRESERVED_NATIVE_ATTEMPT" or \
            actual.get("candidate", {}).get("sha256") != records["candidate"]["sha256"] or \
            actual.get("consumed_once", {}).get("sha256") != records["consumed_once"]["sha256"] or \
            actual.get("actual_result", {}).get("sha256") != records["first_failure"]["sha256"] or \
            failure.get("status") != "FAIL_PRESERVED_NATIVE_ATTEMPT" or \
            post_terminal.get("result_sha256") != records["first_failure"]["sha256"] or \
            post_terminal.get("owned_group_probe", {}).get("status") != "ABSENT" or \
            any(row.get("status") != "ABSENT" for row in post_terminal.get("owned_pids", [])) or \
            actual_review.get("status") != "PASS_SCOPED_ACTUAL_FAILURE_AND_OWNED_CLEANUP_GUI_NOT_ACCEPTED" or \
            actual_review.get("raw_result_sha256") != records["first_failure"]["sha256"] or \
            actual_review.get("report_sha256") != records["actual_report"]["sha256"] or \
            not isinstance(backup, Mapping) or backup.get("sha256") != records["preserved_r7_actor"]["sha256"] or \
            not isinstance(source_entry, Mapping) or source_entry.get("sha256") != records["preserved_r7_actor"]["sha256"] or \
            records["preserved_r7_actor"]["mode"] != 0o600 or records["preserved_r7_actor"]["nlink"] != 1 or \
            preservation.get("actual_failure_sha256") != records["first_failure"]["sha256"] or \
            preservation.get("source_bytes_equal") is not True:
        raise RuntimeError("preserved R7 GUI R2 failure chain or append-only R9 source archive does not close")
    return records


def _exact_argv(root: Path) -> list[str]:
    return [str(LAUNCHER), "-prefsdir", str(root / "prefs"), "-configuration", str(root / "configuration"),
            "-recoverydir", str(root / "recovery"), "-tmpdir", str(root / "tmp"), "-np", "1"]


def _verify_r9_app_actual_review(review: Mapping[str, Any], *, result_sha256: str, wheel_sha256: str) -> None:
    expected = {
        "status": APP_ACTUAL_REVIEW_STATUS,
        "actual_source_profile": CURRENT_R9,
        "root": "/private/tmp/cvw26-application-4cab1cfc",
        "result_sha256": result_sha256,
        "wheel_sha256": wheel_sha256,
        "source_members": 175,
        "package_payload_members": 172,
        "archive_member_counts": R9_APP_ARCHIVE_MEMBER_COUNTS,
        "per_target_wheels": R9_APP_TARGET_WHEEL_COUNTS,
        "actual_OS_network_denial_proofs": 2,
        "exact_outputs_hashed": 21,
        "wheel_RECORD_members": 178,
        "all_embedded_wheels_metadata_RECORD_verified": 119,
    }
    if any(review.get(key) != value for key, value in expected.items()):
        raise RuntimeError("Main R9 App archive review does not bind the exact result, wheel, profile, archive, and audit evidence")
    limitations = review.get("limitations")
    if not isinstance(limitations, dict) or limitations.get("release") != "NOT_ACCEPTED" or \
            limitations.get("COMSOL_JVM_GUI") != "NOT_RUN":
        raise RuntimeError("Main R9 App archive review scope limitations differ")


def _verify_r9_inputs(app_root: Path) -> dict[str, Any]:
    """Bind App4cab through the strict R9 reader and separately capture current desktop overlay sources."""
    from tools import w26_application_lifecycle_r9_actor as lifecycle_r9
    from tools import w26_sealed_data_r9 as sealed_r9

    app_root = Path(app_root)
    if app_root != Path("/private/tmp/cvw26-application-4cab1cfc") or app_root.resolve(strict=True) != app_root:
        raise RuntimeError("R9 GUI source candidate requires the exact Main-reviewed App4cab producer root")
    loader_sources = lifecycle_r9._loader_records()
    profile = sealed_r9.build_current_application_profile(app_root, loader_sources, sealed_r9.BASE)
    verified = sealed_r9.current_application_profile(profile, sealed_r9.BASE)
    producers = sealed_r9._producer_records(app_root, sealed_r9.BASE)
    result_value, result_record = producers["result"]
    candidate_value, candidate_record = producers["candidate"]
    _once_value, once_record = producers["once"]
    _binding_value, _binding_record = producers["binding"]
    source_review_value, source_review_record = producers["review"]
    result_path = app_root / "APPLICATION_BUNDLE_RESULT.json"
    reader_record = file_record(APP_REPORT)
    reader = _read_json(APP_REPORT)
    archive_review_record = file_record(APP_ACTUAL_REVIEW)
    archive_review = _read_json(APP_ACTUAL_REVIEW)
    lock_record = file_record(R9_LOCK)
    lock = _read_json(R9_LOCK)
    wheel = verified["wheel"]
    proof = reader.get("verified", {}).get("source_profile_proof", {})
    if reader.get("status") != "PASS_CURRENT175_R9_SEMANTIC_READER_AND_TARGET_INPUTS_ONLY" or             reader.get("application_root") != str(app_root) or proof.get("status") != "PASS_W26_R9_SEMANTIC_PROFILE_READER" or             proof.get("producer_root") != str(app_root) or proof.get("source_profile") != CURRENT_R9 or             proof.get("source_lock_sha256") != lock_record["sha256"]:
        raise RuntimeError("saved R9 source-external reader proof does not bind App4cab and the exact current175-r9 lock")
    if lock.get("profile") != CURRENT_R9 or lock.get("member_count") != 175 or len(lock.get("members", {})) != 175:
        raise RuntimeError("current175-r9 source lock membership differs")
    if profile.get("source_profile") != CURRENT_R9 or profile.get("root") != str(app_root) or             candidate_value.get("status") != "READY_APPLICATION_BUNDLE_MAIN_REVIEW_NOT_EXECUTION_APPROVED" or             candidate_value.get("native_Popen") != 0 or result_value.get("COMSOL_JVM_GUI") != "NOT_RUN":
        raise RuntimeError("exact R9 App profile/candidate/no-native evidence differs")
    if result_value.get("status") != "PASS_CURRENT_APPLICATION_BUNDLE_SCOPED_NOT_RELEASE" or             result_value.get("version") != APP_VERSION or result_value.get("target") != "macos_arm64" or             result_value.get("build", {}).get("source_file_count") != 175:
        raise RuntimeError("R9 App result is not the exact completed 175-source macOS artifact")
    if source_review_value.get("status") != "PASS_W26_APPLICATION_BUNDLE_SOURCE_FOR_EXACT_CANDIDATE" or             source_review_value.get("candidate_sha256") != candidate_record["sha256"] or             source_review_value.get("source_profile") != CURRENT_R9 or source_review_value.get("source_lock_sha256") != lock_record["sha256"]:
        raise RuntimeError("Main R9 App source review is missing or differs from the strict source profile")
    if wheel.get("sha256") != archive_review.get("wheel_sha256") or result_record["sha256"] != archive_review.get("result_sha256"):
        raise RuntimeError("Main R9 App archive review does not bind the exact App result and wheel")
    _verify_r9_app_actual_review(archive_review, result_sha256=result_record["sha256"], wheel_sha256=wheel["sha256"])
    if archive_review.get("source_external_reader_receipt_sha256") != reader_record["sha256"]:
        raise RuntimeError("Main R9 App archive review does not bind the exact App reader receipt")
    for module, expected in APP_SOURCE_HASHES.items():
        rel = "comsol_mcp/" + module.removeprefix("comsol_mcp.").replace(".", "_") + ".py"
        if rel not in lock["members"]:
            raise RuntimeError("current R9 desktop source is absent from the App source profile: " + module)
    launcher_record = _capture_static(LAUNCHER, maximum=2 * 1024 * 1024)
    bundle_record = _capture_static(BUNDLE_INFO, maximum=1024 * 1024)
    builder_record = _capture_static(BUILDER_PYTHON, maximum=64 * 1024 * 1024)
    with BUNDLE_INFO.open("rb") as stream:
        info = plistlib.load(stream)
    if info.get("CFBundleIdentifier") != "com.comsol.comsol" or info.get("CFBundleShortVersionString") != "6.4.0":
        raise RuntimeError("installed COMSOL bundle identifier/version differs")
    return {"app_reader_report": reader_record, "app_candidate": candidate_record, "app_source_review": source_review_record,
            "app_actual_review": archive_review_record, "app_result": result_record, "app_wheel": wheel,
            "r9_lock": lock_record, "r9_profile": profile, "desktop_source_hashes": dict(APP_SOURCE_HASHES),
            "builder_python": builder_record, "launcher": launcher_record, "bundle_info": bundle_record,
            "bundle_identifier": info["CFBundleIdentifier"], "bundle_version": info["CFBundleShortVersionString"],
            "exact_patch_build": "UNVERIFIED"}


def _secure_installed_root(install_root: Path) -> tuple[Path, dict[str, int]]:
    root = Path(install_root)
    if root.parent != Path("/private/tmp") or not re.fullmatch(r"cvw26-r9install-[0-9a-f]{8}", root.name):
        raise RuntimeError("installed r9 root must be a fresh /private/tmp/cvw26-r9install-<8hex> directory")
    st = root.lstat()
    if not stat.S_ISDIR(st.st_mode) or stat.S_ISLNK(st.st_mode) or st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o700:
        raise RuntimeError("installed r9 root must be an owned, non-symlink 0700 directory")
    if root.resolve(strict=True) != root:
        raise RuntimeError("installed r9 root path must not resolve through aliases")
    return root, {"device": st.st_dev, "inode": st.st_ino}


def _canonical_nosymlink(path: Path) -> Path:
    path = Path(path)
    if not path.is_absolute():
        raise RuntimeError("R9 source overlay paths must be absolute")
    cursor = Path(path.anchor)
    for part in path.parts[1:]:
        cursor /= part
        try:
            mode = cursor.lstat().st_mode
        except OSError as exc:
            raise RuntimeError("R9 source overlay path component is missing") from exc
        if stat.S_ISLNK(mode):
            raise RuntimeError("R9 source overlay path contains a symlink")
    if path.resolve(strict=True) != path:
        raise RuntimeError("R9 source overlay path is not canonical")
    return path


def _verify_current_r9_install(install_root: Path) -> dict[str, Any]:
    """Verify the retained R9 install, package wheel, and separate current Desktop overlay read-only."""
    root, root_identity = _secure_installed_root(install_root)
    candidate_path = root / "W25_R9_INSTALL_CANDIDATE.json"
    plan_path = root / "W25_R9_INSTALL_PLAN.json"
    source_path = root / "W25_R9_INSTALL_SOURCE.json"
    prepared_path = root / "LIFECYCLE_INPUT_r9install.json"
    review_path, binding_path = root / "MAIN_SOURCE_REVIEW.json", root / "MAIN_ONCE_BINDING.json"
    once_path, result_path = root / "UNIT_ONCE_GUARD.json", root / "CURRENT_R9_OFFLINE_INSTALL_RESULT.json"
    candidate_record, plan_record, source_record, prepared_record = map(file_record, (candidate_path, plan_path, source_path, prepared_path))
    review_record, binding_record, once_record, result_record = map(file_record, (review_path, binding_path, once_path, result_path))
    candidate, plan, install_source, prepared, review, binding, once, result = map(
        _read_json, (candidate_path, plan_path, source_path, prepared_path, review_path, binding_path, once_path, result_path))
    if candidate.get("schema") != "w25-current-r9-offline-install-candidate/1" or \
            candidate.get("status") != "READY_W25_CURRENT175_R9_OFFLINE_INSTALL_MAIN_SOURCE_REVIEW_NOT_EXECUTION_APPROVED" or \
            candidate.get("root") != str(root) or candidate.get("root_identity") != root_identity or candidate.get("native_Popen") != 0:
        raise RuntimeError("retained r9 install candidate identity/status differs")
    for expected, row in ((plan_record, candidate["plan"]), (source_record, candidate["source"]),
                          (prepared_record, candidate["prepared"])):
        _capture_external_record(row)
        if _capture_external_record(row) != expected:
            raise RuntimeError("retained r9 candidate first-record differs")
    for row in (candidate["w26_lifecycle_candidate"], candidate["w26_lifecycle_plan"]):
        _capture_external_record(row)
    nested_candidate_record = _capture_external_record(candidate["w26_lifecycle_candidate"])
    nested_candidate = _read_json(Path(nested_candidate_record["path"]))
    nested_source_record = nested_candidate.get("source")
    if not isinstance(nested_source_record, dict):
        raise RuntimeError("retained R9 nested lifecycle source closure record is missing")
    nested_source_record = _capture_external_record(nested_source_record)
    nested_source = _read_json(Path(nested_source_record["path"]))
    overlay_record = candidate.get("current_desktop_overlay")
    if not isinstance(overlay_record, dict) or candidate.get("application", {}).get("current_desktop_overlay") != overlay_record:
        raise RuntimeError("retained R9 candidate does not bind one exact current Desktop overlay")
    overlay_record = _capture_external_record(overlay_record)
    overlay_path = _canonical_nosymlink(root / "CURRENT_DESKTOP_OVERLAY.json")
    if overlay_record["path"] != str(overlay_path):
        raise RuntimeError("retained R9 Desktop overlay record names a different manifest path")
    overlay = _read_json(overlay_path)
    if overlay.get("schema") != DESKTOP_OVERLAY_SCHEMA or \
            overlay.get("status") != "READY_CURRENT_SOURCE_OVERLAY_NOT_PRODUCT_WHEEL_OR_EXECUTION" or \
            overlay.get("source_profile") != CURRENT_R9 or overlay.get("module_filenames") != DESKTOP_MODULES or \
            set(overlay.get("source_records", {})) != set(DESKTOP_MODULES) or \
            set(overlay.get("overlay_records", {})) != set(DESKTOP_MODULES) or \
            overlay.get("package_path") != str(root / "current-desktop-overlay" / "comsol_mcp") or \
            overlay.get("app_wheel", {}).get("sha256") != candidate.get("application", {}).get("wheel", {}).get("sha256"):
        raise RuntimeError("retained R9 current Desktop overlay schema or App wheel binding differs")
    expected_paths = set(nested_source)
    expected_paths.add(str(INSTALL_ACTOR))
    expected_paths.add(str(overlay_path))
    for module, filename in DESKTOP_MODULES.items():
        source_row = overlay["source_records"][module]
        copy_row = overlay["overlay_records"][module]
        expected_source_path = REPOSITORY / "comsol_mcp" / filename
        expected_overlay_path = root / "current-desktop-overlay" / "comsol_mcp" / filename
        for row, path in ((source_row, expected_source_path), (copy_row, expected_overlay_path)):
            _canonical_nosymlink(path)
            actual = _capture_external_record(row)
            if actual["path"] != str(path) or actual["sha256"] != CURRENT_DESKTOP_SOURCE_HASHES[module]:
                raise RuntimeError("retained R9 exact current Desktop source/copy changed: " + module)
            if install_source.get(actual["path"]) != row:
                raise RuntimeError("R9 Desktop overlay source/copy is absent from its install closure: " + module)
            expected_paths.add(actual["path"])
        if source_row["sha256"] != copy_row["sha256"]:
            raise RuntimeError("R9 Desktop source overlay copy differs from its source: " + module)
    if set(install_source) != expected_paths:
        raise RuntimeError("retained R9 install source closure has missing or unexpected paths")
    for row in install_source.values():
        _capture_external_record(row)
    app_lock_record = _capture_r9_source_lock(candidate.get("application", {}).get("r9_lock", {}))
    if candidate.get("application", {}).get("source_profile") != CURRENT_R9 or \
            prepared.get("current_application_profile", {}).get("source_profile") != CURRENT_R9 or \
            app_lock_record["sha256"] != candidate.get("application", {}).get("r9_lock", {}).get("sha256"):
        raise RuntimeError("retained r9 install prepared input profile differs")
    if review.get("schema") != "w25-current-r9-offline-install-source-review/1" or \
            review.get("status") != "PASS_W25_CURRENT175_R9_OFFLINE_INSTALL_SOURCE_FOR_EXACT_CANDIDATE" or \
            review.get("candidate_sha256") != candidate_record["sha256"] or \
            review.get("plan_sha256") != candidate["plan"]["sha256"] or review.get("source_sha256") != candidate["source"]["sha256"]:
        raise RuntimeError("retained r9 install Main source review does not bind the exact candidate")
    if binding.get("status") != "MAIN_APPROVED_ONCE_W25_CURRENT175_R9_OFFLINE_INSTALL" or \
            binding.get("candidate_sha256") != candidate_record["sha256"] or binding.get("review_sha256") != review_record["sha256"] or \
            binding.get("plan_sha256") != candidate["plan"]["sha256"] or binding.get("source_sha256") != candidate["source"]["sha256"] or \
            binding.get("root") != str(root) or binding.get("root_identity") != root_identity:
        raise RuntimeError("retained r9 install Main once binding does not bind the exact candidate")
    if once.get("schema") != "w25-current-r9-offline-install-once/1" or \
            once.get("candidate_first_record", {}).get("sha256") != candidate_record["sha256"] or \
            once.get("binding_first_record", {}).get("sha256") != binding_record["sha256"] or \
            once.get("activity_kind") != "W25_CURRENT175_R9_OFFLINE_INSTALL":
        raise RuntimeError("retained r9 install consumed-once receipt differs")
    if result.get("schema") != "w25-current-r9-offline-install-result/1" or \
            result.get("status") != "PASS_W25_CURRENT175_R9_OFFLINE_INSTALL_ACTUAL_CALLER_SCOPED_NOT_RELEASE" or \
            result.get("candidate_sha256") != candidate_record["sha256"] or result.get("binding_sha256") != binding_record["sha256"] or \
            result.get("source_profile") != CURRENT_R9 or result.get("venv") != str(root / "current-venv") or \
            result.get("venv_retained_for_fresh_W25_GUI_candidate") is not True:
        raise RuntimeError("retained r9 actual install result is missing or differs")
    if any(result.get(k) != "NOT_RUN" for k in ("COMSOL", "JVM", "GUI", "AX_tree", "pixels", "rollback", "uninstall", "data_transition")):
        raise RuntimeError("retained r9 install result exceeds its bounded install-only scope")
    if len(result.get("original_captures", [])) != 4 or any(
            row.get("original_capture", {}).get("status") != "PASS" or row["original_capture"].get("rc") != 0 or
            row["original_capture"].get("birth", {}).get("status") != "OBSERVED" or
            row["original_capture"].get("owned_process_terminal") is not True or row["original_capture"].get("owned_group_absent") is not True
            for row in result["original_captures"]):
        raise RuntimeError("retained r9 install original-capture terminal evidence is incomplete")
    net = result.get("network_denial_result", {})
    if net.get("status") != "PASS_PROCESS_ISOLATED_INSTALL_AND_IMPORT" or net.get("network_probe") != {
            "status": "PASS", "errno": 1, "detail": "[Errno 1] Operation not permitted"} or \
            net.get("listener_accepted") is not False or net.get("pip_download") != "DISABLED_NO_INDEX":
        raise RuntimeError("retained r9 product network-denial evidence differs")
    if result.get("wheelhouse_count") != 42 or result.get("wheelhouse_sha256") != candidate["application"]["wheelhouse_sha256"] or \
            result.get("app_wheel", {}).get("sha256") != candidate["application"]["wheel"]["sha256"] or \
            _capture_external_record(candidate.get("application", {}).get("current_desktop_overlay", {})) != overlay_record:
        raise RuntimeError("retained r9 wheelhouse or App wheel identity differs")
    venv = root / "current-venv"; venv_stat = venv.lstat()
    if not stat.S_ISDIR(venv_stat.st_mode) or stat.S_ISLNK(venv_stat.st_mode) or venv_stat.st_uid != os.getuid() or stat.S_IMODE(venv_stat.st_mode) != 0o700:
        raise RuntimeError("retained r9 venv must be an owned non-symlink 0700 directory")
    python = venv / "bin/python"; python_stat = python.lstat()
    if not (stat.S_ISREG(python_stat.st_mode) or stat.S_ISLNK(python_stat.st_mode)):
        raise RuntimeError("retained r9 venv interpreter is not a regular file or venv symlink")
    python_resolved = python.resolve(strict=True); python_record = file_record(python_resolved)
    package_root = venv / "lib/python3.12/site-packages/comsol_mcp"
    package_rows = result.get("package", {}).get("files", [])
    expected_package = [{"path": row["path"].removeprefix("comsol_mcp/"), "sha256": row["sha256"]}
                        for row in prepared["new_input"]["package_members"]]
    if package_rows != expected_package or len(package_rows) != 172:
        raise RuntimeError("retained r9 product package fingerprint differs from reviewed wheel members")
    package_records = []
    for row in package_rows:
        member = file_record(package_root / row["path"])
        if member["sha256"] != row["sha256"]: raise RuntimeError("retained r9 installed package changed: " + row["path"])
        package_records.append(member)
    expected_versions = {name: pin["version"] for name, pin in prepared["new_input"]["applicable_pins"].items()}; expected_versions["pip"] = "25.0.1"
    versions = {row["name"]: row["version"] for row in result.get("distributions", {}).get("distributions", [])}
    if versions != expected_versions: raise RuntimeError("retained r9 distribution versions differ from exact lock")
    caller = result.get("current_product_caller_status", {}); status = caller.get("status", {})
    if caller.get("installed_version") != APP_VERSION or caller.get("executable") != str(python) or caller.get("prompt_requested") is not False or \
            caller.get("jpype_started_before_after") != [False, False] or caller.get("window_tree_pixels_enumerated") is not False or \
            caller.get("GUI_certification") != "NOT_RUN" or status.get("permission_status") != "AVAILABLE" or \
            status.get("accessibility_trusted") is not True or status.get("screen_capture_allowed") is not True or \
            status.get("selector_profile") != "UNOBSERVED" or status.get("control_status") != "UNSUPPORTED_CONTROL" or status.get("model_binding") != "UNVERIFIED":
        raise RuntimeError("retained current r9 caller status is incomplete or overclaims")
    package_file_records = []
    for module, expected in APP_SOURCE_HASHES.items():
        relative_member = module.removeprefix("comsol_mcp.").replace(".", "_") + ".py"
        member_path = package_root / relative_member
        if file_record(member_path)["sha256"] != expected:
            raise RuntimeError("retained R9 embedded wheel Desktop member differs: " + module)
        package_file_records.append(file_record(member_path))
    if set(caller.get("source_hashes", {})) != set(CURRENT_DESKTOP_SOURCE_HASHES):
        raise RuntimeError("retained current R9 caller source list is not the exact four-module overlay")
    for module, expected in CURRENT_DESKTOP_SOURCE_HASHES.items():
        observed = caller.get("source_hashes", {}).get(module, {})
        expected_path = root / "current-desktop-overlay" / "comsol_mcp" / DESKTOP_MODULES[module]
        if observed.get("sha256") != expected or observed.get("path") != str(expected_path):
            raise RuntimeError("retained current R9 source-external Desktop caller differs: " + module)
        if _capture_static(expected_path)["sha256"] != expected:
            raise RuntimeError("retained R9 overlay Desktop module changed: " + module)
    lock = _read_json(R9_LOCK); lock_record = file_record(R9_LOCK)
    if app_lock_record["sha256"] != lock_record["sha256"]:
        raise RuntimeError("retained R9 App source lock differs from the installed candidate binding")
    if lock.get("profile") != CURRENT_R9 or lock.get("member_count") != 175 or len(lock.get("members", {})) != 175:
        raise RuntimeError("retained r9 current source lock membership differs")
    for module, expected in APP_SOURCE_HASHES.items():
        member = "comsol_mcp/" + module.removeprefix("comsol_mcp.").replace(".", "_") + ".py"
        if lock.get("members", {}).get(member, {}).get("sha256") != expected:
            raise RuntimeError("R9 App lock does not bind exact embedded wheel source: " + module)
    install_report_record = file_record(INSTALL_ACTUAL_REPORT); install_report = _read_json(INSTALL_ACTUAL_REPORT)
    if install_report.get("status") != "PASS_W25_CURRENT175_R9_OFFLINE_INSTALL_ACTUAL_CALLER_SCOPED_NOT_RELEASE" or \
            install_report.get("actual_result", {}).get("sha256") != result_record["sha256"] or \
            install_report.get("candidate", {}).get("sha256") != candidate_record["sha256"] or \
            install_report.get("main_once_binding", {}).get("sha256") != binding_record["sha256"]:
        raise RuntimeError("R9 actual install report does not bind retained result and once")
    install_actual_review_record = file_record(INSTALL_ACTUAL_REVIEW); install_actual_review = _read_json(INSTALL_ACTUAL_REVIEW)
    if install_actual_review.get("status") != "PASS_SCOPED_W25_R9_INSTALL_R1_MAIN_ACTUAL_REVIEW" or \
            install_actual_review.get("result_sha256") != result_record["sha256"] or install_actual_review.get("venv_retained") != str(venv) or \
            install_actual_review.get("package_files_rehashed_on_disk") != 172 or install_actual_review.get("distributions") != 43 or \
            install_actual_review.get("original_captures") != 4 or install_actual_review.get("actual_EPERM") != 4 or \
            install_actual_review.get("birth_terminal_group_absent") is not True:
        raise RuntimeError("Main actual install evidence review does not bind this retained r9 venv")
    report_captures = install_report.get("captures", [])
    if len(report_captures) != 4:
        raise RuntimeError("R9 actual report capture evidence is incomplete")
    for row in report_captures:
        receipt = _capture_external_record(row["receipt"])
        terminal = _read_json(Path(row["receipt"]["path"]))
        if terminal.get("status") != "PASS" or terminal.get("owned_process_terminal") is not True or \
                terminal.get("owned_group_absent") is not True or terminal.get("birth", {}).get("status") != "OBSERVED":
            raise RuntimeError("R9 actual report terminal capture receipt differs")
    for row in install_report.get("network_denial", {}).get("same_pid_probe_records", []):
        probe = _read_json(Path(row["path"]))
        if _capture_external_record(row)["sha256"] != file_record(Path(row["path"]))["sha256"] or \
                probe.get("status") != "PASS_ACTUAL_EPERM" or probe.get("errno") != 1:
            raise RuntimeError("R9 actual report network-denial probe differs")
    venv_identity = {"device": venv_stat.st_dev, "inode": venv_stat.st_ino, "uid": venv_stat.st_uid, "mode": stat.S_IMODE(venv_stat.st_mode)}
    if install_report.get("installed_application", {}).get("venv_identity") != venv_identity:
        raise RuntimeError("R9 report retained venv identity changed")
    interpreter = {"path": str(python), "link_target": os.readlink(python) if stat.S_ISLNK(python_stat.st_mode) else None,
                   "resolved_path": str(python_resolved), "resolved_record": python_record}
    install_source_records = {str(row["path"]): _capture_external_record(row) for row in install_source.values()}
    extra = (candidate_record, candidate["plan"], candidate["source"], candidate["prepared"], candidate["w26_lifecycle_candidate"],
             candidate["w26_lifecycle_plan"], review_record, binding_record, once_record, result_record, install_report_record,
             install_actual_review_record, lock_record, *package_records, python_record)
    install_source_records.update({str(row["path"]): _capture_external_record(row) for row in extra})
    application_root = Path(candidate.get("application", {}).get("root", ""))
    if application_root.parent != Path("/private/tmp") or not re.fullmatch(r"cvw26-application-[0-9a-f]{8}", application_root.name):
        raise RuntimeError("retained r9 install does not bind a canonical App producer root")
    app_review_record = _capture_external_record(candidate["application"]["review"])
    return {"root":str(root),"root_identity":root_identity,"candidate":candidate_record,"plan":_capture_external_record(candidate["plan"]),
            "source":_capture_external_record(candidate["source"]),"source_members":{key:_capture_external_record(row) for key,row in install_source.items()},
            "review":review_record,"binding":binding_record,"once":once_record,"result":result_record,"report":install_report_record,
            "actual_review":install_actual_review_record,"venv":str(venv),"venv_identity":venv_identity,"interpreter":interpreter,
            "package_digest":result["package"]["digest"],"package_records":package_records,
            "distribution_digest":result["distributions"]["digest"],"caller":caller,"install_source_records":install_source_records,
            "install_report":install_report_record,"app_result":install_report["application"]["build_result"],
            "app_wheel":install_report["application"]["wheel"],"app_actual_review":install_report["application"]["app_archive_main_actual_review"],
            "r9_lock":_capture_r9_source_lock(install_report["application"]["source_lock"]),
            "desktop_overlay":overlay_record,"desktop_overlay_manifest":overlay,
            "app_root":str(application_root), "app_source_review":app_review_record}


def _capture_lane_preparation() -> dict[str, Any]:
    """Read only the process names/PIDs relevant to W24/W25; never inspect windows."""
    ps_argv = ["/bin/ps", "-axo", "pid=,ppid=,uid=,stat=,comm="]
    ps = subprocess.run(ps_argv, capture_output=True, text=True, encoding="utf-8", errors="strict", timeout=10, check=False)
    if ps.returncode != 0:
        raise RuntimeError("read-only native lane process inventory failed")
    protected = {2266, 2777, 2579}
    matches = []
    for line in ps.stdout.splitlines():
        fields = line.split(None, 4)
        if len(fields) != 5:
            continue
        pid, ppid, uid = map(int, fields[:3])
        comm = Path(fields[4]).name
        if pid in protected or "comsol" in comm.lower() or "mphserver" in comm.lower():
            matches.append({"pid": pid, "ppid": ppid, "uid": uid, "state": fields[3], "comm_basename": comm})
    port_argv = ["/usr/sbin/lsof", "-nP", "-iTCP:49435", "-sTCP:LISTEN"]
    port = subprocess.run(port_argv, capture_output=True, text=True, encoding="utf-8", errors="strict", timeout=10, check=False)
    if port.returncode not in (0, 1):
        raise RuntimeError("read-only W24 listener inventory failed")
    listener_rows = max(0, len(port.stdout.splitlines()) - 1) if port.returncode == 0 else 0
    status = "PREPARATION_CLEAR_NOT_NATIVE_AUTHORITY" if not matches and listener_rows == 0 else "PREPARATION_HOLD_OR_AMBIGUOUS"
    return {"schema": "w25-t044-macos-lane-check-preparation/1", "status": status,
            "checked_utc": datetime.now(timezone.utc).isoformat(), "scope": "read-only relevant process-name/PID and TCP listener query; no window inventory",
            "process_query": {"argv": ps_argv, "exit_code": ps.returncode, "matching_processes": matches,
                              "matching_process_count": len(matches)},
            "protected_t044_pids": sorted(protected), "listener_query": {"argv": port_argv, "exit_code": port.returncode,
                                                                          "port": 49435, "listener_present": listener_rows > 0,
                                                                          "listener_row_count": listener_rows},
            "native_Popen": 0, "COMSOL_GUI": "NOT_RUN", "window_inventory": "NOT_RUN"}


def prepare(root: Path, installed_root: Path) -> dict[str, Any]:
    """Create the exact future W25 candidate; this function never launches COMSOL."""
    root = Path(root)
    if root.exists() or root.is_symlink():
        raise RuntimeError("fresh W25 private root already exists")
    if root.parent != Path("/private/tmp") or not re.fullmatch(r"cvw25-macos-[0-9a-f]{8}", root.name):
        raise RuntimeError("fresh W25 root namespace is invalid")
    resume_record = file_record(RESUME)
    current_install = _verify_current_r9_install(installed_root)
    source_inputs = _verify_r9_inputs(Path(current_install["app_root"]))
    r2_failure_evidence = _capture_r7_gui_r2_failure_evidence()
    # Reader and installation reports are separate evidence domains. Bind the
    # same underlying App result, wheel, and source lock across both domains;
    # retain each report under its own path and provenance record.
    for key, value in (("app_result", source_inputs["app_result"]),
                       ("app_wheel", source_inputs["app_wheel"]), ("r9_lock", source_inputs["r9_lock"])):
        if value.get("sha256") != current_install.get(key, {}).get("sha256"):
            raise RuntimeError("R9 App artifact and retained R9 install records disagree: " + key)
    w25_source_review_record = source_inputs["app_source_review"]
    lane_preparation = _capture_lane_preparation()
    root.mkdir(mode=0o700)
    os.chmod(root, 0o700)
    root_identity = _secure_root(root)
    for name in ("prefs", "configuration", "recovery", "tmp", "home", "logs", "evidence"):
        (root / name).mkdir(mode=0o700)
        os.chmod(root / name, 0o700)
    caller = current_install["caller"]
    runner_python = current_install["interpreter"]["path"]
    owned_window_ownership, owned_window_stop_conditions = _bounded_owned_window_plan_contract()
    plan = {
        "schema": "w25-macos-owned-desktop-observation-plan/1",
        "scope": "one blank standalone COMSOL 6.4.0 Desktop launch; one owner-only bounded AX metadata read; no model, server endpoint, controls, screenshots, or pixels",
        "actor_revision": "W25 GUI actor-only R9; an unchanged current175-r9 App wheel/lock with a separate exact four-module current Desktop source overlay in the new test venv",
        "source_scope": "R8 actor/test sources plus exact current175-r9 macOS adapter/window-metadata sources; immutable App wheel provenance is kept distinct from the current-source overlay; R2 failure provenance is preserved; this does not certify the complete current scientific package or release",
        "runner": str(Path(__file__).resolve()),
        "runner_interpreter_required": runner_python,
        "runner_interpreter_state_at_prepare": "PRESENT; bound to separately Main-source-reviewed and Main-actual-reviewed retained r9 install root",
        "source_profile": CURRENT_R9,
        "source_artifacts": {"app_result": source_inputs["app_result"], "wheel": source_inputs["app_wheel"],
                             "bundle_report": source_inputs["app_reader_report"], "app_candidate": source_inputs["app_candidate"],
                             "app_source_review": source_inputs["app_source_review"],
                             "app_archive_actual_review": source_inputs["app_actual_review"], "r9_source_lock": source_inputs["r9_lock"],
                             "install_actual_report": current_install["install_report"],
                             "install_main_actual_review": current_install["actual_review"],
                             "install_result": current_install["result"],
                             "current_desktop_overlay": current_install["desktop_overlay"],
                             "prior_r7_gui_r2_failure_evidence": r2_failure_evidence},
        "retained_r9_install": {"root": current_install["root"], "root_identity": current_install["root_identity"],
                                "candidate": current_install["candidate"], "source_review": current_install["review"],
                                "once_binding": current_install["binding"], "consumed_once": current_install["once"],
                                "main_actual_review": current_install["actual_review"], "result": current_install["result"],
                                "venv": current_install["venv"], "venv_identity": current_install["venv_identity"],
                                "interpreter": current_install["interpreter"],
                                "package_digest": current_install["package_digest"],
                                "package_file_count": len(current_install["package_records"]),
                                "distribution_digest": current_install["distribution_digest"],
                                "current_desktop_overlay": current_install["desktop_overlay"],
                                "current_product_caller": caller,
                                "install_again_in_this_candidate": False},
        "fresh_product_caller_precondition": {
            "fresh_installed_current_caller_record": caller,
            "qualification": "fresh four-capture actual source-external 0.1.10 product-caller status from the retained current175-r9 venv; Main actual evidence review passed",
            "execute_rechecks": "exact retained install-root venv interpreter, installed comsol-mcp 0.1.10, immutable App wheel hashes, exact current Desktop overlay hashes, lazy permissions, no JPype JVM before or after; permission check is nonprompt",
            "selector_profile": "UNAVAILABLE; no profile file is read or loaded",
        },
        "lane_preparation": {"record": lane_preparation, "qualification": "fresh preparation inventory only; Main must create and sign W25_LANE_CHECK_CURRENT.json immediately before any GUI authorization"},
        "command": {"exact_argv": _exact_argv(root), "shell": False, "stdin": "DEVNULL",
                     "stdout": "bounded pipe drain to " + str(root / "logs/comsol.stdout"),
                     "stderr": "bounded pipe drain to " + str(root / "logs/comsol.stderr"),
                     "cwd": str(root), "start_new_session": True,
                     "argv_sha256": _sha(_json_bytes(_exact_argv(root)))},
        "environment": {"HOME": str(root / "home"), "TMPDIR": str(root / "tmp"),
                        "PATH": "/Applications/COMSOL64/Multiphysics/bin:/usr/bin:/bin:/usr/sbin:/sbin",
                        "locale": "C", "pass_through_license_variable_names_only": ["LMCOMSOL_LICENSE_FILE", "LM_LICENSE_FILE"],
                        "license_values_persisted": False},
        "official_same_version_help": [
            {"title": "COMSOL 6.4 - COMSOL Commands on macOS", "doc_id": 6232,
             "path": "doc/help/wtpwebapps/ROOT/doc/com.comsol.help.comsol/comsol_ref_running.38.33.html",
             "sha256": HELP_SHA256, "verified_flags": ["-prefsdir", "-configuration", "-recoverydir", "-tmpdir", "-np"]},
            {"title": "COMSOL 6.4 - The License File and The License Features", "doc_id": 4711,
             "path": "doc/help/wtpwebapps/ROOT/doc/com.comsol.help.comsol/comsol_installation.02.030.html",
             "sha256": LICENSE_HELP_SHA256, "verified_license_environment": ["LMCOMSOL_LICENSE_FILE", "LM_LICENSE_FILE"]},
        ],
        "budgets": {"launch_deadline_seconds": ACTIVE_RUNTIME_SECONDS,
                    "active_observation_budget_seconds": ACTIVE_RUNTIME_SECONDS,
                    "total_budget_seconds": BOUNDED_RUNTIME_SECONDS,
                    "cleanup_reserve_seconds": CLEANUP_SECONDS,
                    "max_output_bytes": MAX_OUTPUT_BYTES, "max_owned_processes": MAX_PROCESS_ROWS,
                    "max_ax_depth": MAX_AX_DEPTH, "max_ax_nodes": MAX_AX_NODES,
                    "max_ax_readiness_seconds_total": AX_READINESS_SECONDS,
                    "max_ax_window_reselections": MAX_AX_WINDOW_RESELECTIONS,
                    "lane_check_max_age_seconds": LANE_MAX_AGE_SECONDS},
        "ownership": {"root": "exact subprocess.Popen returned by this runner, shell=false, start_new_session=true",
                      "window_provider": "TaskOwnedMacOSWindowObserver with exact retained Popen; root/descendants must match PID birth UID PPID PGID SID executable and argv hash",
                      **owned_window_ownership},
        "stop_conditions": owned_window_stop_conditions,
        "control_transport_error_observability": {
            "source_plan_path": "repository/_state/t044-thermal-r5/MAIN_READONLY_TRANSPORT_DIAGNOSIS_PLAN_R1.json",
            "source_plan_sha256": "6b18cbfa45bfa31a23eb6287707eb547fc3f461352dbb7b7a16fd5238b6eaa8e",
            "change": "best-effort existing private daemon._log_exception on generic loopback Handler failure before unchanged HTTP 500",
            "scope": "software-only diagnostic improvement; no request/header/token/body logging or replay",
            "focused_real_loopback_controls": "dispatch exception, strict JSON response failure, diagnostic write failure, and client disconnect",
            "T044_first_cause": "UNRESOLVED",
            "native_replay": "NOT_RUN",
            "T044_acceptance": "NOT_ACCEPTED",
        },
        "endpoint_and_model": {"model_path": None, "active_endpoint": "NOT_OBSERVED", "endpoint_challenge": "NOT_RUN",
                               "model_binding": "UNVERIFIED", "controls": "NOT_RUN"},
    }
    plan_path = root / "CURRENT_BUILD_PLAN_macos1.json"
    plan_record = _write_new(plan_path, plan)
    first_record = _write_new(root / "FIRST_RECORD.json", {"root": str(root), "root_identity": root_identity,
                                                           "uid": os.getuid(), "mode": 0o700, "created_utc": datetime.now(timezone.utc).isoformat()})
    evidence = {"root_identity": root_identity, "resume": resume_record, "app_source_review": w25_source_review_record,
                "current_source_records": source_inputs, "retained_r9_install": current_install,
                "runner": _capture_static(Path(__file__)),
                "lane_preparation": lane_preparation,
                "official_document_hashes": {"macos_commands": HELP_SHA256, "license_environment": LICENSE_HELP_SHA256}}
    evidence_record = _write_new(root / "PREPARATION_EVIDENCE.json", evidence)
    app_review_path = Path(current_install["app_root"]) / "MAIN_SOURCE_REVIEW.json"
    # W26 producer records use dev/inode aliases; normalize these four imported
    # App records at the producer boundary before the strict W25 source reader.
    app_source_review_record = _capture_external_record(w25_source_review_record)
    app_result_record = _capture_external_record(source_inputs["app_result"])
    app_candidate_record = _capture_external_record(source_inputs["app_candidate"])
    app_wheel_record = _capture_external_record(source_inputs["app_wheel"])
    source = {"repository/tools/w25_macos_owned_desktop_actor_r9.py": _capture_static(Path(__file__)),
              "repository/tests/test_w25_macos_owned_desktop_actor_r9.py": _capture_static(REPOSITORY / "tests/test_w25_macos_owned_desktop_actor_r9.py"),
              "repository/comsol_mcp/_control_daemon.py": _capture_static(REPOSITORY / "comsol_mcp/_control_daemon.py"),
              "repository/tests/test_control_listener_error_logging.py": _capture_static(REPOSITORY / "tests/test_control_listener_error_logging.py"),
              "repository/_state/t044-thermal-r5/MAIN_READONLY_TRANSPORT_DIAGNOSIS_PLAN_R1.json":
                  _capture_static(REPOSITORY / "_state/t044-thermal-r5/MAIN_READONLY_TRANSPORT_DIAGNOSIS_PLAN_R1.json"),
              str(R9_LOCK): source_inputs["r9_lock"],
              "repository/comsol_mcp/_desktop_macos.py": _capture_static(REPOSITORY / "comsol_mcp/_desktop_macos.py"),
              "repository/comsol_mcp/_desktop_platforms.py": _capture_static(REPOSITORY / "comsol_mcp/_desktop_platforms.py"),
              "repository/comsol_mcp/_desktop_service.py": _capture_static(REPOSITORY / "comsol_mcp/_desktop_service.py"),
              "repository/comsol_mcp/_desktop_selector_profile.py": _capture_static(REPOSITORY / "comsol_mcp/_desktop_selector_profile.py"),
              str(RESUME): resume_record, str(APP_REPORT): source_inputs["app_reader_report"], str(app_review_path): app_source_review_record,
              str(APP_ACTUAL_REVIEW): source_inputs["app_actual_review"],
              str(Path(source_inputs["app_result"]["path"])): app_result_record,
              str(Path(source_inputs["app_candidate"]["path"])): app_candidate_record,
              str(Path(source_inputs["app_wheel"]["path"])): app_wheel_record,
              str(R9_LOCK): source_inputs["r9_lock"],
              str(BUILDER_PYTHON): source_inputs["builder_python"],
              str(LAUNCHER): source_inputs["launcher"], str(BUNDLE_INFO): source_inputs["bundle_info"]}
    for record in r2_failure_evidence.values():
        key = record["path"]
        prior = source.get(key)
        if prior is not None and prior != record:
            raise RuntimeError("preserved R7 GUI R2 evidence conflicts with another exact source record")
        source[key] = record
    for record in current_install["install_source_records"].values():
        prior = source.get(record["path"])
        if prior is not None and prior != record:
            raise RuntimeError("retained install input conflicts with another exact W25 source record")
        source[record["path"]] = record
    source_record = _write_new(root / "CURRENT_ACTOR_SOURCE_macos1.json", source)
    candidate = {"schema": "w25-macos-owned-desktop-actor-candidate/1",
                 "status": "READY_MAIN_W25_GUI_ACTOR_SOURCE_REVIEW_NOT_EXECUTION_APPROVED",
                 "activity_kind": "W25_MACOS_BLANK_DESKTOP_METADATA_OBSERVATION", "root": str(root),
                 "root_identity": root_identity, "candidate_filename": "ACTOR_CANDIDATE_macos1.json",
                 "plan": plan_record, "source": source_record, "preparation_evidence": evidence_record,
                 "first_record": first_record, "resume_sha256": resume_record["sha256"],
                 "installed_root": current_install["root"], "installed_root_identity": current_install["root_identity"],
                 "installed_result_sha256": current_install["result"]["sha256"],
                 "r9_app_wheel_sha256": source_inputs["app_wheel"]["sha256"],
                 "r9_profile": CURRENT_R9, "installed_caller_at_prepare": "PASS_RETAINED_CURRENT175_R9_OVERLAY_SOURCE_EXTERNAL_CALLER",
                 "actor_revision": "R9", "prior_r7_gui_attempt": {
                     "status": "FAIL_PRESERVED_NATIVE_ATTEMPT_GUI_NOT_ACCEPTED",
                     "candidate_sha256": r2_failure_evidence["candidate"]["sha256"],
                     "actual_report_sha256": r2_failure_evidence["actual_report"]["sha256"],
                     "raw_failure_sha256": r2_failure_evidence["first_failure"]["sha256"],
                     "consumed_once_sha256": r2_failure_evidence["consumed_once"]["sha256"],
                     "post_terminal_check_sha256": r2_failure_evidence["post_terminal_check"]["sha256"],
                     "preserved_r7_actor_sha256": r2_failure_evidence["preserved_r7_actor"]["sha256"],
                     "identity_delta": "UNRESOLVED_NOT_CAPTURED; R8 does not retroactively claim the R2 delta"},
                 "native_Popen": 0, "GUI": "NOT_RUN", "COMSOL": "NOT_RUN", "JVM": "NOT_RUN",
                 "T044_lane": "FRESH_CHECK_REQUIRED_IN_W25_LANE_CHECK_CURRENT_JSON",
                 "main_source_review": "NOT_CREATED", "main_once_binding": "NOT_CREATED"}
    candidate_record = _write_new(root / "ACTOR_CANDIDATE_macos1.json", candidate)
    return {"root": str(root), "root_identity": root_identity, "plan": plan_record, "source": source_record,
            "candidate": candidate_record, "preparation_evidence": evidence_record, "native_Popen": 0}


def _assert_same_root(root: Path, candidate: Mapping[str, Any]) -> None:
    if candidate.get("root") != str(root) or candidate.get("schema") != "w25-macos-owned-desktop-actor-candidate/1":
        raise RuntimeError("W25 candidate root/schema mismatch")
    if _secure_root(root) != candidate.get("root_identity"):
        raise RuntimeError("W25 candidate root inode/device changed")


def build_expected_binding(candidate_record: Mapping[str, Any], lane_record: Mapping[str, Any]) -> dict[str, Any]:
    """Return the closed once fields Main must independently review and sign."""
    root = Path(candidate_record["path"]).parent
    candidate = _read_json(root / "ACTOR_CANDIDATE_macos1.json")
    plan = _read_json(Path(candidate["plan"]["path"]))
    return {"status": "MAIN_APPROVED_ONCE_W25_MACOS_OWNED_DESKTOP_METADATA",
            "activity_kind": candidate["activity_kind"], "candidate_sha256": candidate_record["sha256"],
            "root": str(root), "root_identity": candidate["root_identity"], "plan_sha256": candidate["plan"]["sha256"],
            "source_sha256": candidate["source"]["sha256"],
            "review_sha256": file_record(root / "MAIN_SOURCE_REVIEW.json")["sha256"],
            "resume_sha256": candidate["resume_sha256"],
            "lane_check_path": lane_record["path"], "lane_check_sha256": lane_record["sha256"],
            "argv_sha256": plan["command"]["argv_sha256"], "runner_python": plan["runner_interpreter_required"],
            "max_runtime_seconds": BOUNDED_RUNTIME_SECONDS,
            "max_active_runtime_seconds": ACTIVE_RUNTIME_SECONDS,
            "cleanup_reserve_seconds": CLEANUP_SECONDS,
            "max_output_bytes": MAX_OUTPUT_BYTES, "max_ax_depth": MAX_AX_DEPTH, "max_ax_nodes": MAX_AX_NODES,
            "max_owned_processes": MAX_PROCESS_ROWS,
            "max_ax_readiness_seconds_total": AX_READINESS_SECONDS,
            "max_ax_window_reselections": MAX_AX_WINDOW_RESELECTIONS,
            "GUI": True, "COMSOL": True,
            "model_path": None, "endpoint_challenge": False, "CUA": False}


def verify_authority(root: Path, binding_path: Path, binding_sha: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    root = Path(root)
    if binding_path != root / "MAIN_ONCE_BINDING.json":
        raise RuntimeError("fresh exact W25 Main once path is required")
    candidate_path = root / "ACTOR_CANDIDATE_macos1.json"
    candidate_raw = candidate_path.read_bytes()
    candidate_record = file_record(candidate_path)
    candidate = json.loads(candidate_raw)
    _assert_same_root(root, candidate)
    if candidate_record["sha256"] != candidate.get("candidate_sha256", candidate_record["sha256"]):
        raise RuntimeError("candidate digest self-binding differs")
    for row in (candidate["plan"], candidate["source"], candidate["preparation_evidence"], candidate["first_record"]):
        if not _record_matches(row):
            raise RuntimeError("W25 candidate input first-record mismatch")
    source = json.loads(Path(candidate["source"]["path"]).read_text(encoding="utf-8"))
    for row in source.values():
        if not _record_matches(row):
            raise RuntimeError("W25 exact source/evidence closure changed")
    resume = file_record(RESUME)
    if resume["sha256"] != candidate["resume_sha256"]:
        raise RuntimeError("current RESUME changed; prepare a fresh W25 candidate")
    installed_root, installed_identity = _secure_installed_root(Path(candidate["installed_root"]))
    plan = _read_json(Path(candidate["plan"]["path"]))
    if installed_identity != candidate.get("installed_root_identity") or \
            plan.get("runner_interpreter_required") != str(installed_root / "current-venv/bin/python") or \
            plan.get("retained_r9_install", {}).get("root") != str(installed_root) or \
            plan.get("retained_r9_install", {}).get("result", {}).get("sha256") != candidate.get("installed_result_sha256"):
        raise RuntimeError("W25 candidate lost the exact retained r9 install root/result binding")
    verified_install = _verify_current_r9_install(installed_root)
    if verified_install["root_identity"] != installed_identity or \
            verified_install["result"]["sha256"] != candidate.get("installed_result_sha256") or \
            verified_install["venv_identity"] != plan.get("retained_r9_install", {}).get("venv_identity"):
        raise RuntimeError("W25 candidate no longer binds the exact Main-reviewed retained r9 install")
    venv = installed_root / "current-venv"
    venv_stat = venv.lstat()
    plan_venv = plan["retained_r9_install"]["venv_identity"]
    if not stat.S_ISDIR(venv_stat.st_mode) or stat.S_ISLNK(venv_stat.st_mode) or \
            {"device": venv_stat.st_dev, "inode": venv_stat.st_ino, "uid": venv_stat.st_uid,
             "mode": stat.S_IMODE(venv_stat.st_mode)} != plan_venv:
        raise RuntimeError("retained r9 venv directory identity changed")
    python_path = venv / "bin/python"
    python_stat = python_path.lstat()
    interpreter = plan["retained_r9_install"]["interpreter"]
    if python_path.resolve(strict=True) != Path(interpreter["resolved_path"]) or \
            (os.readlink(python_path) if stat.S_ISLNK(python_stat.st_mode) else None) != interpreter["link_target"]:
        raise RuntimeError("retained r9 venv interpreter link changed")
    review_path = root / "MAIN_SOURCE_REVIEW.json"
    review = _read_json(review_path)
    review_record = file_record(review_path)
    if review.get("status") != "PASS_W25_MACOS_OWNED_DESKTOP_SOURCE_FOR_EXACT_CANDIDATE" or \
            review.get("candidate_sha256") != candidate_record["sha256"] or \
            review.get("plan_sha256") != candidate["plan"]["sha256"] or \
            review.get("source_sha256") != candidate["source"]["sha256"]:
        raise RuntimeError("Main exact-candidate W25 source review missing")
    if file_record(binding_path)["sha256"] != binding_sha:
        raise RuntimeError("Main W25 once binding bytes differ")
    binding = _read_json(binding_path)
    lane_path = root / "W25_LANE_CHECK_CURRENT.json"
    lane = _read_json(lane_path)
    lane_record = file_record(lane_path)
    required = {"status": "MAIN_APPROVED_ONCE_W25_MACOS_OWNED_DESKTOP_METADATA",
                "activity_kind": candidate["activity_kind"], "candidate_sha256": candidate_record["sha256"],
                "root": str(root), "root_identity": candidate["root_identity"], "plan_sha256": candidate["plan"]["sha256"],
                "source_sha256": candidate["source"]["sha256"], "resume_sha256": candidate["resume_sha256"],
                "review_sha256": review_record["sha256"],
                "lane_check_path": str(lane_path), "lane_check_sha256": lane_record["sha256"],
                "argv_sha256": _read_json(Path(candidate["plan"]["path"]))["command"]["argv_sha256"],
                "runner_python": plan["runner_interpreter_required"],
                "max_runtime_seconds": BOUNDED_RUNTIME_SECONDS,
                "max_active_runtime_seconds": ACTIVE_RUNTIME_SECONDS,
                "cleanup_reserve_seconds": CLEANUP_SECONDS,
                "max_output_bytes": MAX_OUTPUT_BYTES, "max_ax_depth": MAX_AX_DEPTH, "max_ax_nodes": MAX_AX_NODES,
                "max_owned_processes": MAX_PROCESS_ROWS, "max_ax_readiness_seconds_total": AX_READINESS_SECONDS,
                "max_ax_window_reselections": MAX_AX_WINDOW_RESELECTIONS,
                "GUI": True, "COMSOL": True,
                "model_path": None, "endpoint_challenge": False, "CUA": False}
    if any(type(binding.get(k)) is not type(v) or binding.get(k) != v for k, v in required.items()) or \
            not isinstance(binding.get("nonce"), str) or len(binding["nonce"]) < 16:
        raise RuntimeError("Main W25 once does not bind exact candidate, lane, caller, argv, or budgets")
    if lane.get("schema") != "w25-t044-macos-lane-check/1" or lane.get("status") != "CLEAR_FOR_W25_MACOS_GUI_METADATA" or \
            lane.get("matching_comsol_processes") != [] or lane.get("protected_t044_pids_present") != [] or \
            lane.get("listener_49435_present") is not False:
        raise RuntimeError("fresh W25 native lane check is held, ambiguous, or incomplete")
    checked = datetime.fromisoformat(str(lane.get("checked_utc")))
    if checked.tzinfo is None:
        raise RuntimeError("W25 lane check timestamp must include timezone")
    age = (datetime.now(timezone.utc) - checked.astimezone(timezone.utc)).total_seconds()
    if age < 0 or age > LANE_MAX_AGE_SECONDS:
        raise RuntimeError("W25 lane check is not fresh; no native birth")
    if (root / "UNIT_ONCE_GUARD.json").exists() or (root / "UNIT_ONCE_GUARD.json").is_symlink():
        raise RuntimeError("W25 once already consumed; no replay")
    return candidate, binding, lane


def _activate_current_desktop_overlay(install_root: Path) -> dict[str, dict[str, str]]:
    """Revalidate and import only the four source-bound current Desktop modules."""
    root, _identity = _secure_installed_root(install_root)
    manifest_path = _canonical_nosymlink(root / "CURRENT_DESKTOP_OVERLAY.json")
    manifest_record = file_record(manifest_path)
    install_candidate = _read_json(root / "W25_R9_INSTALL_CANDIDATE.json")
    expected_manifest_record = install_candidate.get("current_desktop_overlay")
    if not isinstance(expected_manifest_record, dict) or _capture_external_record(expected_manifest_record) != manifest_record:
        raise RuntimeError("current R9 Desktop overlay manifest no longer matches the install candidate")
    manifest = _read_json(manifest_path)
    overlay = _canonical_nosymlink(root / "current-desktop-overlay" / "comsol_mcp")
    if manifest.get("schema") != DESKTOP_OVERLAY_SCHEMA or \
            manifest.get("status") != "READY_CURRENT_SOURCE_OVERLAY_NOT_PRODUCT_WHEEL_OR_EXECUTION" or \
            manifest.get("source_profile") != CURRENT_R9 or manifest.get("package_path") != str(overlay) or \
            manifest.get("module_filenames") != DESKTOP_MODULES or \
            set(manifest.get("source_records", {})) != set(DESKTOP_MODULES) or \
            set(manifest.get("overlay_records", {})) != set(DESKTOP_MODULES):
        raise RuntimeError("current R9 Desktop overlay is not the exact four-module source set")
    source_hashes: dict[str, dict[str, str]] = {}
    for module, filename in DESKTOP_MODULES.items():
        source_path = _canonical_nosymlink(REPOSITORY / "comsol_mcp" / filename)
        copy_path = _canonical_nosymlink(overlay / filename)
        source_row = manifest["source_records"][module]
        copy_row = manifest["overlay_records"][module]
        if source_row.get("path") != str(source_path) or copy_row.get("path") != str(copy_path):
            raise RuntimeError("current R9 Desktop overlay path is not exact: " + module)
        source_now = _capture_external_record(source_row)
        copy_now = _capture_external_record(copy_row)
        expected = CURRENT_DESKTOP_SOURCE_HASHES[module]
        if source_now["path"] != str(source_path) or copy_now["path"] != str(copy_path) or \
                source_now["sha256"] != expected or copy_now["sha256"] != expected:
            raise RuntimeError("current R9 Desktop overlay/source changed: " + module)
        source_hashes[module] = {"path": str(copy_path), "sha256": expected}
    if file_record(manifest_path) != manifest_record:
        raise RuntimeError("current R9 Desktop overlay manifest changed while being read")

    import comsol_mcp
    for module in DESKTOP_MODULES:
        loaded = sys.modules.get(module)
        if loaded is not None and Path(str(getattr(loaded, "__file__", ""))) != overlay / DESKTOP_MODULES[module]:
            raise RuntimeError("current R9 Desktop module was preloaded from another source: " + module)
    comsol_mcp.__path__ = [str(overlay)] + [str(path) for path in comsol_mcp.__path__ if str(path) != str(overlay)]
    for module, expected in CURRENT_DESKTOP_SOURCE_HASHES.items():
        imported = importlib.import_module(module)
        module_path = Path(str(getattr(imported, "__file__", "")))
        if module_path != overlay / DESKTOP_MODULES[module] or file_record(module_path)["sha256"] != expected:
            raise RuntimeError("current R9 Desktop import did not resolve to its exact overlay: " + module)
    return source_hashes


def _installed_caller_status(expected_python: Path, install_root: Path) -> dict[str, Any]:
    expected_python = Path(expected_python)
    if Path(sys.executable).resolve() != expected_python.resolve() or not expected_python.is_file():
        raise RuntimeError("W25 actor must execute under the exact source-external r9 product venv")
    from importlib import metadata
    if metadata.version("comsol-mcp") != APP_VERSION:
        raise RuntimeError("installed current product distribution is not 0.1.10")
    import jpype
    before = bool(jpype.isJVMStarted())
    if before:
        raise RuntimeError("JPype JVM already started before the W25 blank Desktop observation")
    hashes = _activate_current_desktop_overlay(install_root)
    from comsol_mcp._desktop_macos import MacOSAccessibilityObserver
    status = MacOSAccessibilityObserver().status()
    after = bool(jpype.isJVMStarted())
    if before or after:
        raise RuntimeError("product permission status unexpectedly started a JVM")
    if status.get("permission_status") != "AVAILABLE" or status.get("accessibility_trusted") is not True:
        raise RuntimeError("product caller lacks nonprompt Accessibility permission")
    if status.get("metadata_status") != "AVAILABLE" or status.get("native_evidence") != "NOT_RUN" or \
            status.get("selector_profile") != "UNOBSERVED":
        raise RuntimeError("permission status did not remain metadata-only")
    return {"caller_pid": os.getpid(), "executable": sys.executable, "version": APP_VERSION,
            "source_hashes": hashes, "status": status, "jpype_started_before_after": [before, after],
            "prompt_requested": False, "window_tree_pixels_enumerated": False, "GUI_certification": "NOT_RUN"}


@dataclass(frozen=True, slots=True)
class ProcessRow:
    process_id: int
    process_birth: str
    real_uid: int
    parent_pid: int
    process_group_id: int
    session_id: int
    executable_path: str
    command_sha256: str
    bundle_identifier: str
    comsol_version: str


@dataclass(frozen=True, slots=True)
class OwnedWindowIdentityTuple:
    """Private, value-minimal identity for an owned Quartz window only."""

    platform: str
    native_window_id: str
    process_id: int
    process_birth: str
    login_id: str
    desktop_session_id: str
    comsol_version: str

    @classmethod
    def from_identity(cls, identity: Any) -> "OwnedWindowIdentityTuple":
        from comsol_mcp._desktop_service import WindowIdentity

        if type(identity) is not WindowIdentity:
            raise TypeError("owned Quartz revalidation requires the exact WindowIdentity type")
        return cls(identity.platform, identity.native_window_id, identity.process_id,
                   identity.process_birth, identity.login_id, identity.desktop_session_id,
                   identity.comsol_version)


@dataclass(frozen=True, slots=True)
class OwnedProcessIdentityProof:
    """The bounded process proof persisted with an owned-window receipt."""

    process_id: int
    process_birth: str
    real_uid: int
    parent_pid: int
    executable_path: str
    command_sha256: str
    process_group_id: int
    session_id: int
    bundle_identifier: str
    comsol_version: str

    @classmethod
    def from_process_row(cls, row: Any) -> "OwnedProcessIdentityProof":
        return cls(row.process_id, row.process_birth, row.real_uid, row.parent_pid,
                   row.executable_path, row.command_sha256, row.process_group_id,
                   row.session_id, row.bundle_identifier, row.comsol_version)


@dataclass(frozen=True, slots=True)
class OwnedWindowRevalidationReceipt:
    """Typed, append-only evidence: owned identity tuples and exact process proof only."""

    sample_index: int
    status: str
    expected_window: OwnedWindowIdentityTuple
    observed_owned_windows: tuple[OwnedWindowIdentityTuple, ...]
    original_popen_proof: OwnedProcessIdentityProof
    window_owner_proof: OwnedProcessIdentityProof
    process_tree_member_count: int
    captured_utc: str


_RETAINED_OWNERS: dict[str, "W25OriginalProcessOwner"] = {}


class W25OriginalProcessOwner:
    """Keep the exact direct Popen and its pipe drainers through UNKNOWN.

    This is the W25 task-specific adaptation of the project's qualified
    original-owner hold: one process birth, no PID adoption, no retries, and
    passive observation whenever the child/group cannot be proven absent.
    """

    def __init__(self, root: Path, stdout_path: Path, stderr_path: Path,
                 *, total_deadline_monotonic: float | None = None):
        self.root = Path(root)
        self.stdout_path, self.stderr_path = Path(stdout_path), Path(stderr_path)
        self.process: subprocess.Popen | None = None
        self._original_popen: subprocess.Popen | None = None
        self.process_pid: int | None = None
        self.spawn_attempted = False
        self.spawned = False
        self.rows: list[Any] = []
        self.drainers: dict[str, threading.Thread] = {}
        self.streams: dict[str, Any] = {}
        self.output_total = [0]
        self.output_lock = threading.Lock()
        self.output_exceeded = threading.Event()
        self.output_errors: list[str] = []
        self.first_failure: dict[str, Any] | None = None
        self.diagnostic_errors: list[dict[str, str]] = []
        self.hold_entered = False
        self.released = False
        self.cleanup: dict[str, Any] | None = None
        self._hold_receipt_written = False
        self.total_deadline_monotonic = total_deadline_monotonic

    def _budget_exceeded(self) -> bool:
        return (self.total_deadline_monotonic is not None
                and time.monotonic() >= self.total_deadline_monotonic)

    @staticmethod
    def _safe_text(exc: BaseException) -> str:
        try:
            return str(exc)[:512]
        except BaseException:
            return "diagnostic text unavailable"

    def note(self, phase: str, exc: BaseException) -> None:
        row = {"phase": phase[:96], "type": type(exc).__name__, "error": self._safe_text(exc)}
        self.diagnostic_errors[:] = (self.diagnostic_errors + [row])[-24:]
        try:
            os.write(2, ("W25 original-owner UNKNOWN: " + json.dumps(row, sort_keys=True) + "\n").encode())
        except BaseException:
            pass

    def fail(self, exc: BaseException, phase: str) -> None:
        diagnostics = getattr(exc, "diagnostics", None)
        row: dict[str, Any] = {"phase": phase, "type": type(exc).__name__, "error": self._safe_text(exc)}
        if isinstance(diagnostics, Mapping) and diagnostics:
            # Adapter diagnostics contain bounded scalar error data only; no
            # target tree values or foreign-window contents are copied here.
            row["diagnostics"] = dict(diagnostics)
        if self.first_failure is None:
            self.first_failure = row
        else:
            self.note(phase, exc)

    def open_logs(self) -> None:
        self.streams["stdout"] = self.stdout_path.open("xb")
        try:
            self.streams["stderr"] = self.stderr_path.open("xb")
        except BaseException:
            self.streams["stdout"].close()
            raise

    def _ensure_drainers(self) -> None:
        process = self.process
        if process is None or not self.spawned:
            return
        for name, pipe in (("stdout", process.stdout), ("stderr", process.stderr)):
            thread = self.drainers.get(name)
            if thread is not None and thread.is_alive():
                continue
            if thread is not None:
                continue
            stream = self.streams.get(name)
            if pipe is None or stream is None or stream.closed:
                raise RuntimeError("original process pipe/log owner is unavailable")
            thread = _start_bounded_drainer(pipe, stream, self.stdout_path if name == "stdout" else self.stderr_path,
                                            total=self.output_total, lock=self.output_lock,
                                            output_exceeded=self.output_exceeded,
                                            output_errors=self.output_errors)
            self.drainers[name] = thread

    def popen(self, argv: Sequence[str], **kwargs: Any) -> subprocess.Popen:
        """Create exactly one normal Popen while retaining its object pre-birth."""
        if self.process is not None or self.spawn_attempted:
            raise RuntimeError("W25 original owner refuses a second Popen")
        if kwargs.get("start_new_session") is not True or kwargs.get("stdout") is not subprocess.PIPE or \
                kwargs.get("stderr") is not subprocess.PIPE:
            raise RuntimeError("W25 original Popen requires exact isolated session and owned pipes")
        process = subprocess.Popen.__new__(subprocess.Popen)
        self.process = process
        self._original_popen = process
        self.spawn_attempted = True
        _RETAINED_OWNERS[str(self.root)] = self
        interrupted = None
        old_mask = None
        try:
            # Main-thread Ctrl-C must not land in the bytecode gap after the OS
            # creates the child but before this exact Popen is retained.
            if hasattr(signal, "pthread_sigmask") and hasattr(signal, "SIGINT"):
                old_mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT})
            subprocess.Popen.__init__(process, argv, **kwargs)
            self.spawned = bool(getattr(process, "_child_created", False))
            pid = getattr(process, "pid", None)
            if self.spawned and type(pid) is int and pid > 0:
                self.process_pid = pid
            self._ensure_drainers()
            return process
        except BaseException as exc:
            interrupted = exc
            pid = getattr(process, "pid", None)
            self.spawned = bool(getattr(process, "_child_created", False) or (type(pid) is int and pid > 0))
            if type(pid) is int and pid > 0:
                self.process_pid = pid
            elif not self.spawned:
                # Popen failed before a child birth. Retain no false owner.
                self.process = None
                self._original_popen = None
                _RETAINED_OWNERS.pop(str(self.root), None)
            self.fail(exc, "original_popen_or_drainer_setup")
            raise
        finally:
            if old_mask is not None:
                try:
                    signal.pthread_sigmask(signal.SIG_SETMASK, old_mask)
                except BaseException as exc:
                    self.fail(exc, "restore_interrupt_mask_after_popen")
                    # Propagate only after the exact owner has captured any
                    # created child; the outer boundary will enter UNKNOWN hold.
                    if interrupted is None:
                        raise

    def _group_state(self) -> str:
        if self.process_pid is None:
            return "UNKNOWN"
        try:
            os.killpg(self.process_pid, 0)
            return "PRESENT"
        except ProcessLookupError:
            return "ABSENT"
        except BaseException as exc:
            self.note("passive_process_group_poll", exc)
            return "UNKNOWN"

    def _direct_state(self) -> tuple[str, int | None]:
        process = self.process
        if process is None:
            return ("NOT_CREATED", None)
        if process is not self._original_popen or type(process) is not subprocess.Popen:
            self.note("original_popen_identity", RuntimeError("exact original Popen handle was replaced"))
            return ("OBSERVATION_UNKNOWN", None)
        try:
            code = process.poll()
            if code is None:
                return ("ALIVE", None)
            if type(code) is not int:
                raise RuntimeError("original Popen poll returned an untyped terminal code")
            return ("EXITED", code)
        except BaseException as exc:
            self.note("original_popen_passive_poll", exc)
            return ("OBSERVATION_UNKNOWN", None)

    def _drainers_done(self) -> bool:
        try:
            self._ensure_drainers()
            return len(self.drainers) == 2 and all(not thread.is_alive() for thread in self.drainers.values())
        except BaseException as exc:
            self.note("drainer_lifetime_poll", exc)
            return False

    def _rows_terminal(self) -> bool:
        import psutil
        for row in self.rows:
            if row.process_id == self.process_pid:
                continue
            try:
                process = psutil.Process(row.process_id)
                if f"{process.create_time():.6f}" != row.process_birth:
                    continue
                if process.status() == psutil.STATUS_ZOMBIE:
                    continue
                # A same-birth process that cannot be positively shown terminal
                # keeps the owner fenced, even when its current command changed.
                return False
            except psutil.NoSuchProcess:
                continue
            except BaseException as exc:
                self.note("passive_owned_child_poll", exc)
                return False
        return True

    def _passive_lifetime_clear(self) -> bool:
        if not self.spawned or self.process is None or self.process_pid != getattr(self.process, "pid", None):
            return False
        state, _ = self._direct_state()
        if state != "EXITED" or self._group_state() != "ABSENT":
            return False
        return self._rows_terminal() and self._drainers_done()

    def _write_hold_receipt(self) -> None:
        if self._hold_receipt_written:
            return
        self._hold_receipt_written = True
        try:
            state, returncode = self._direct_state()
            _write_new(self.root / "evidence/W25_ORIGINAL_OWNER_HOLD.json", {
                "schema": "w25-original-process-owner-hold/1",
                "status": "UNKNOWN_ORIGINAL_POPEN_RETAINED_PASSIVE_POLL",
                "popen_pid": self.process_pid, "popen_state": state, "returncode": returncode,
                "process_group_state": self._group_state(),
                "owned_process_row_count": len(self.rows),
                "drainers": {name: {"alive": thread.is_alive()} for name, thread in self.drainers.items()},
                "total_deadline_monotonic": self.total_deadline_monotonic,
                "budget_exceeded_at_hold_start": self._budget_exceeded(),
                "budget_may_be_exceeded_before_hold_release": True,
                "signals_during_unknown_hold": 0, "first_failure": self.first_failure,
                "diagnostic_errors": list(self.diagnostic_errors),
                "stage_acceptance": "NOT_RUN", "COMSOL_GUI": "NOT_ACCEPTED"})
        except BaseException as exc:
            self.note("write_original_owner_hold_receipt", exc)

    def passive_hold(self, reason: BaseException | str) -> dict[str, Any]:
        """Do not signal/adopt an UNKNOWN child; retain and poll until lifetime clears."""
        if isinstance(reason, BaseException):
            self.fail(reason, "enter_passive_unknown_hold")
        elif self.first_failure is None:
            self.first_failure = {"phase": "enter_passive_unknown_hold", "type": "RuntimeError", "error": str(reason)[:512]}
        self.hold_entered = True
        _RETAINED_OWNERS[str(self.root)] = self
        self._write_hold_receipt()
        while True:
            try:
                self._ensure_drainers()
                if self._passive_lifetime_clear():
                    _state, returncode = self._direct_state()
                    observation = {"status": "UNKNOWN_TERMINAL_GROUP_ABSENT_PASSIVE_HOLD_COMPLETE_NOT_ACCEPTANCE",
                                   "popen_pid": self.process_pid, "returncode": returncode,
                                   "process_group_state": "ABSENT", "drainers_complete": True,
                                   "first_failure": self.first_failure}
                    self.cleanup = {"status": "UNKNOWN_PASSIVE_HOLD_COMPLETED", "owned_group_absent": True,
                                    "original_popen_pid": self.process_pid, "original_popen_returncode": returncode,
                                    "signals_during_unknown_hold": 0, "errors": list(self.diagnostic_errors),
                                    "total_deadline_monotonic": self.total_deadline_monotonic,
                                    "budget_exceeded": self._budget_exceeded(),
                                    "budget_overrun_seconds": (None if self.total_deadline_monotonic is None else
                                                                max(0.0, time.monotonic() - self.total_deadline_monotonic))}
                    self.released = True
                    _RETAINED_OWNERS.pop(str(self.root), None)
                    return observation
                # wait() stays bound to the same Popen and never sends a signal.
                if self.process is not None and self.spawned:
                    self.process.wait(timeout=1)
                else:
                    threading.Event().wait(1)
            except subprocess.TimeoutExpired:
                pass
            except BaseException as exc:
                self.note("passive_original_popen_or_group_wait", exc)
                try:
                    threading.Event().wait(1)
                except BaseException as nested:
                    self.note("passive_fallback_wait", nested)

    def cleanup_or_hold(self, rows: Sequence[Any], *, total_deadline_monotonic: float) -> dict[str, Any] | None:
        process = self.process
        if process is None or not self.spawned:
            return None
        self.rows = list(rows)
        if self.first_failure is not None and self.first_failure["phase"] in {
                "original_popen_or_drainer_setup", "restore_interrupt_mask_after_popen"}:
            self.passive_hold("spawn-time owner setup failed; preserve exact original owner")
            return self.cleanup
        cleanup = None
        if not self.rows:
            try:
                self.rows = _snapshot_owned_processes(process, bundle_identifier="com.comsol.comsol", comsol_version="6.4.0")
            except BaseException as exc:
                self.fail(exc, "cleanup_identity_refresh")
                self.passive_hold(exc)
                return self.cleanup
        try:
            cleanup = _terminate_owned(
                process, self.rows, total_deadline_monotonic=total_deadline_monotonic,
            )
            self.cleanup = cleanup
        except BaseException as exc:
            self.fail(exc, "owned_cleanup_exception")
            self.passive_hold(exc)
            return self.cleanup
        if cleanup.get("owned_group_absent") is not True:
            self.fail(RuntimeError("owned cleanup did not prove the original process group absent"), "cleanup_unknown")
            self.passive_hold("owned cleanup UNKNOWN; original owner retained")
            return self.cleanup
        try:
            self._ensure_drainers()
            for thread in self.drainers.values():
                thread.join(timeout=5)
        except BaseException as exc:
            self.fail(exc, "cleanup_drainer_join")
        if not self._drainers_done():
            self.fail(RuntimeError("owned process exited but original bounded pipe drainer remains live"), "cleanup_drainer_unknown")
            self.passive_hold("pipe drainer remains live; original owner retained")
            return self.cleanup
        self.released = True
        _RETAINED_OWNERS.pop(str(self.root), None)
        return cleanup


def _snapshot_owned_processes(root_process: subprocess.Popen, *, bundle_identifier: str, comsol_version: str) -> list[Any]:
    import psutil
    from comsol_mcp._desktop_macos import TaskOwnedMacOSProcessIdentity, macos_process_command_sha256
    if type(root_process) is not subprocess.Popen or root_process.poll() is not None:
        raise RuntimeError("original COMSOL Popen root is not live")
    supervisor = os.getpid()
    root = psutil.Process(root_process.pid)
    children = root.children(recursive=True)
    processes = [root, *children]
    if len(processes) > MAX_PROCESS_ROWS:
        raise RuntimeError("COMSOL owned process tree exceeds the fixed row cap")
    captured = []
    for process in processes:
        with process.oneshot():
            pid = process.pid
            executable = process.exe()
            argv = process.cmdline()
            uid = process.uids().real
            ppid = process.ppid()
            birth = f"{process.create_time():.6f}"
            pgid = os.getpgid(pid)
            sid = os.getsid(pid)
        captured.append(TaskOwnedMacOSProcessIdentity(pid, birth, uid, ppid, pgid, sid, executable,
                                                       macos_process_command_sha256(executable, argv),
                                                       bundle_identifier, comsol_version))
    root_row = next((row for row in captured if row.process_id == root_process.pid), None)
    if root_row is None or root_row.parent_pid != supervisor or root_row.process_group_id != root_row.process_id or \
            root_row.session_id != root_row.process_id:
        raise RuntimeError("COMSOL direct Popen root identity is inconsistent")
    if any(row.real_uid != os.getuid() or row.process_group_id != root_row.process_group_id or row.session_id != root_row.session_id
           for row in captured):
        raise RuntimeError("COMSOL child escaped the exact task-owned UID/process group/session")
    return captured


def _raw_exception_record(exc: BaseException) -> dict[str, Any]:
    """Persist bounded raw exception identity needed to audit a narrow recovery."""
    row: dict[str, Any] = {
        "type": f"{type(exc).__module__}.{type(exc).__qualname__}",
        "text": str(exc),
        "args_repr": repr(getattr(exc, "args", ())),
    }
    for name in ("pid", "name", "msg"):
        if hasattr(exc, name):
            value = getattr(exc, name)
            row[name] = value if value is None or type(value) in (str, int, float, bool) else repr(value)
    return row


def _owned_transition_trigger_record(exc: BaseException) -> dict[str, Any]:
    row = _raw_exception_record(exc)
    cause = getattr(exc, "__cause__", None)
    if isinstance(cause, BaseException):
        row["direct_cause"] = _raw_exception_record(cause)
    return row


def _startup_owned_child_exit_cause(exc: BaseException, frozen_rows: Sequence[Any], root_pid: int) -> tuple[Any, dict[str, Any]] | None:
    """Accept only the two production identity errors directly caused by one captured child NoSuchProcess."""
    from comsol_mcp._desktop_service import DesktopOperationError
    import psutil

    if type(exc) is not DesktopOperationError or exc.code != "WINDOW_IDENTITY_UNKNOWN" or \
            str(exc) not in STARTUP_CHILD_EXIT_ERROR_MESSAGES:
        return None
    cause = exc.__cause__
    if type(cause) is not psutil.NoSuchProcess or cause.__cause__ is not None:
        return None
    pid = getattr(cause, "pid", None)
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return None
    matches = [row for row in frozen_rows if getattr(row, "process_id", None) == pid]
    if len(matches) != 1 or pid == root_pid:
        return None
    row = matches[0]
    if not isinstance(getattr(row, "parent_pid", None), int) or row.parent_pid <= 0:
        return None
    if not isinstance(getattr(row, "process_group_id", None), int) or \
            not isinstance(getattr(row, "session_id", None), int):
        return None
    proof = {
        "wrapper": _raw_exception_record(exc),
        "cause": _raw_exception_record(cause),
        "cause_chain_length": 2,
        "cause_is_direct": True,
        "cause_pid_matches_unique_frozen_nonroot_row": True,
        "frozen_child_row": asdict(row),
    }
    return row, proof


def _prove_owned_child_pid_absent(pid: int) -> dict[str, Any] | None:
    """Require two independent read-only absence checks; permission and reuse are terminal."""
    import psutil

    try:
        first_exists = psutil.pid_exists(pid)
    except Exception:
        return None
    if first_exists is not False:
        return None
    try:
        process = psutil.Process(pid)
        process.create_time()
        return None
    except psutil.NoSuchProcess as exc:
        if type(exc) is not psutil.NoSuchProcess or getattr(exc, "pid", None) != pid:
            return None
        lookup = _raw_exception_record(exc)
    except Exception:
        return None
    try:
        second_exists = psutil.pid_exists(pid)
    except Exception:
        return None
    if second_exists is not False:
        return None
    return {
        "status": "ABSENT_CONFIRMED",
        "pid": pid,
        "first_pid_exists_probe": False,
        "process_create_time_lookup": lookup,
        "second_pid_exists_probe": False,
    }


def _startup_child_exit_rows_prove_single_subset(frozen_rows: Sequence[Any], fresh_rows: Sequence[Any],
                                                  *, root_pid: int, missing_child_pid: int) -> dict[str, Any] | None:
    """Require an exact root-preserving subset with exactly the causative child absent."""
    before = {getattr(row, "process_id", None): row for row in frozen_rows}
    after = {getattr(row, "process_id", None): row for row in fresh_rows}
    if len(before) != len(frozen_rows) or len(after) != len(fresh_rows):
        return None
    if root_pid not in before or root_pid not in after or before[root_pid] != after[root_pid]:
        return None
    added = sorted(set(after) - set(before))
    missing = sorted(set(before) - set(after))
    if added or missing != [missing_child_pid]:
        return None
    changed_survivors = sorted(pid for pid in set(before) & set(after) if before[pid] != after[pid])
    if changed_survivors:
        return None
    return {
        "root_row_equal": True,
        "survivor_rows_equal": True,
        "added_process_ids": added,
        "missing_process_ids": missing,
        "only_missing_row_is_direct_cause_pid": missing == [missing_child_pid],
        "frozen_rows": [asdict(row) for row in frozen_rows],
        "fresh_rows": [asdict(row) for row in fresh_rows],
    }


def _recover_startup_owned_child_exit(root: Path, root_process: subprocess.Popen, frozen_rows: Sequence[Any],
                                      trigger_error: BaseException, *, ordinal: int, deadline: float,
                                      candidate_sha256: str, binding_sha256: str,
                                      before_first_window: bool) -> dict[str, Any] | None:
    """Persist a proof and refresh the snapshot only for one exact startup child exit."""
    root = Path(root)
    if before_first_window is not True or (root / "OWNED_WINDOW_FIRST.json").exists():
        return None
    if isinstance(ordinal, bool) or not isinstance(ordinal, int) or not 1 <= ordinal <= MAX_STARTUP_CHILD_EXIT_RECOVERIES:
        return None
    if type(root_process) is not subprocess.Popen or root_process.poll() is not None:
        return None
    child = _startup_owned_child_exit_cause(trigger_error, frozen_rows, root_process.pid)
    if child is None:
        return None
    child_row, cause_proof = child
    absence = _prove_owned_child_pid_absent(child_row.process_id)
    if absence is None:
        return None
    try:
        fresh_rows = _snapshot_owned_processes(
            root_process, bundle_identifier="com.comsol.comsol", comsol_version="6.4.0",
        )
    except Exception:
        return None
    subset = _startup_child_exit_rows_prove_single_subset(
        frozen_rows, fresh_rows, root_pid=root_process.pid, missing_child_pid=child_row.process_id,
    )
    if subset is None or root_process.poll() is not None:
        return None
    proof_name = f"STARTUP_CHILD_EXIT_RECOVERY_{ordinal:02d}.json"
    proof_path = root / proof_name
    if proof_path.exists() or proof_path.is_symlink():
        return None
    proof = {
        "schema": "w25-startup-owned-child-exit-recovery/1",
        "status": "PASS_SINGLE_CAPTURED_NONROOT_CHILD_EXIT_BEFORE_FIRST_WINDOW",
        "candidate_sha256": candidate_sha256,
        "binding_sha256": binding_sha256,
        "ordinal": ordinal,
        "observed_utc": datetime.now(timezone.utc).isoformat(),
        "original_popen": {
            "same_object_retained": True,
            "pid": root_process.pid,
            "still_live_before_and_after_fresh_snapshot": True,
            "root_row": asdict(next(row for row in fresh_rows if row.process_id == root_process.pid)),
        },
        "first_window_boundary": {
            "before_first_owned_window": True,
            "owned_window_first_record_absent": True,
            "ax_called": False,
        },
        "failure_cause_proof": cause_proof,
        "independent_absence_proof": absence,
        "snapshot_subset_proof": subset,
        "budget": {
            "fixed_total_launch_deadline_monotonic": deadline,
            "remaining_seconds_after_proof": max(0.0, deadline - time.monotonic()),
            "recovery_sleep_cap_seconds": 1.0,
            "maximum_startup_child_exit_recoveries": MAX_STARTUP_CHILD_EXIT_RECOVERIES,
        },
        "recovery_action": "discard_failed_inventory_rebuild_observer_from_fresh_rows_same_original_popen_and_resample_without_extending_deadline",
    }
    receipt = _write_new(proof_path, proof)
    return {"rows": fresh_rows, "proof": proof, "receipt": receipt}


def _enumerate_owned_windows_with_startup_recovery(root: Path, root_process: subprocess.Popen,
                                                    frozen_rows: Sequence[Any], *, ordinal: int,
                                                    deadline: float, candidate_sha256: str,
                                                    binding_sha256: str,
                                                    before_first_window: bool) -> dict[str, Any]:
    """Source-pipeline entry point: create one observer, or return a proven fresh snapshot for retry."""
    from comsol_mcp._desktop_macos import TaskOwnedMacOSWindowObserver

    observer = TaskOwnedMacOSWindowObserver(frozen_rows, original_root=root_process)
    try:
        # The internal collector is used only to retain ambiguity metadata.
        # The actor below must wait without AX or selection while count is 0
        # or greater than one; the public enumerate_windows API stays strict.
        windows = list(observer._collect_owned_window_observations())
    except BaseException as exc:
        if before_first_window is not True:
            raise
        recovery = _recover_startup_owned_child_exit(
            root, root_process, frozen_rows, exc, ordinal=ordinal, deadline=deadline,
            candidate_sha256=candidate_sha256, binding_sha256=binding_sha256,
            before_first_window=True,
        )
        if recovery is None:
            raise
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("startup child-exit recovery exhausted the original fixed launch deadline") from exc
        # The failed observer is discarded; caller receives no window list and
        # will build a new observer from the persisted fresh rows.
        return {
            "observer": None,
            "windows": None,
            "rows": recovery["rows"],
            "recovery_receipt": recovery["receipt"],
            "retry_after_seconds": min(1.0, remaining),
        }
    return {
        "observer": observer,
        "windows": windows,
        "rows": list(frozen_rows),
        "recovery_receipt": None,
        "retry_after_seconds": 0.0,
    }


def _ax_error_record(exc: BaseException) -> dict[str, Any]:
    diagnostics = getattr(exc, "diagnostics", None)
    return dict(diagnostics) if isinstance(diagnostics, Mapping) else {}


def _is_transient_owned_window_change(exc: BaseException) -> bool:
    """Permit reselection only after a transient AXWindows-to-Quartz mismatch."""
    from comsol_mcp._desktop_service import DesktopOperationError

    if not isinstance(exc, DesktopOperationError):
        return False
    diagnostics = _ax_error_record(exc)
    last = diagnostics.get("last_ax_error")
    readiness_error = (
        diagnostics.get("scope") == "readiness_revalidation" and isinstance(last, Mapping)
        and last.get("kind") == "AX_ERROR" and last.get("attribute") == "AXWindows"
        and last.get("scope") == "application_window_map" and last.get("readiness_transient") is True
        and type(last.get("ax_error_code")) is int and last.get("ax_error_code") in {-25204, -25212}
    )
    if not readiness_error:
        return False
    if exc.code == "WINDOW_IDENTITY_CHANGED":
        return True
    if exc.code != "WINDOW_IDENTITY_REVALIDATION_FAILED":
        return False
    # The public API refuses all ambiguity. Permit the bounded internal path
    # only when that exact multiple-window refusal directly followed an
    # approved transient AXWindows readiness error.
    cause = exc.__cause__
    return (type(cause) is DesktopOperationError and cause.code == "WINDOW_IDENTITY_UNKNOWN"
            and str(cause) == "task-owned COMSOL process tree has multiple visible windows"
            and cause.__cause__ is None
            and diagnostics.get("revalidation_error_type") == "DesktopOperationError")


def _window_identity_value(identity: Any) -> dict[str, Any]:
    return {"platform": identity.platform, "native_window_id": identity.native_window_id,
            "process_id": identity.process_id, "process_birth": identity.process_birth,
            "login_id": identity.login_id, "desktop_session_id": identity.desktop_session_id,
            "comsol_version": identity.comsol_version}


def _is_exact_pre_ax_identity_loss_after_last_windows_retry(exc: BaseException,
                                                             attempts: Sequence[Mapping[str, Any]],
                                                             attempt_start_index: int) -> bool:
    """Recognize only R7's literal pre-AX unknown after the last real AXWindows retry."""
    from comsol_mcp._desktop_service import DesktopOperationError

    if type(exc) is not DesktopOperationError or exc.code != "WINDOW_IDENTITY_UNKNOWN" or \
            str(exc) != PREAX_IDENTITY_LOSS_LITERAL or exc.diagnostics:
        return False
    recent = list(attempts[attempt_start_index:])
    if len(recent) < 2 or recent[-1].get("status") != "FAIL_TERMINAL" or \
            recent[-1].get("diagnostics") != {"error_code": "WINDOW_IDENTITY_UNKNOWN"}:
        return False
    last = recent[-2]
    diagnostics = last.get("diagnostics")
    return (
        last.get("status") == "RETRY_AX_WINDOWS_READINESS"
        and isinstance(diagnostics, Mapping)
        and diagnostics.get("kind") == "AX_ERROR"
        and diagnostics.get("attribute") == "AXWindows"
        and diagnostics.get("scope") == "application_window_map"
        and diagnostics.get("readiness_transient") is True
        and type(diagnostics.get("ax_error_code")) is int
        and diagnostics.get("ax_error_code") in {-25204, -25212}
    )


def _is_initial_window_association_invalid_axref(exc: BaseException,
                                                 attempts: Sequence[Mapping[str, Any]],
                                                 attempt_start_index: int) -> bool:
    """Recognize only AXPosition/AXSize invalid references from initial association."""
    from comsol_mcp._desktop_service import DesktopOperationError

    if type(exc) is not DesktopOperationError or exc.code != "AX_ATTRIBUTE_UNAVAILABLE":
        return False
    diagnostics = _ax_error_record(exc)
    if (diagnostics.get("kind") != "AX_ERROR"
            or diagnostics.get("ax_error_code") != -25202
            or diagnostics.get("ax_error_name") != "invalid_ui_element"
            or diagnostics.get("attribute") not in {"AXPosition", "AXSize"}
            or diagnostics.get("scope") != "window_association"
            or diagnostics.get("readiness_transient") is not False):
        return False
    recent = list(attempts[attempt_start_index:])
    return bool(recent and recent[-1].get("status") == "FAIL_TERMINAL"
                and recent[-1].get("diagnostics") == diagnostics)


def _preax_accessibility_status(permission_reader=None) -> Mapping[str, Any]:
    """Read nonprompt product status; a missing/revoked AX grant is terminal."""
    if permission_reader is None:
        from comsol_mcp._desktop_macos import MacOSAccessibilityObserver

        status = MacOSAccessibilityObserver().status()
    else:
        status = permission_reader()
    if not isinstance(status, Mapping) or status.get("permission_status") != "AVAILABLE" or \
            status.get("accessibility_trusted") is not True or status.get("metadata_status") != "AVAILABLE":
        safe_status = ({key: status.get(key) for key in ("permission_status", "accessibility_trusted", "metadata_status")}
                       if isinstance(status, Mapping) else {"status_type": type(status).__name__})
        from comsol_mcp._desktop_service import DesktopOperationError

        raise DesktopOperationError(
            "PREAX_ACCESSIBILITY_PERMISSION_UNAVAILABLE",
            "nonprompt product Accessibility status is not AVAILABLE",
            diagnostics={"permission_status": safe_status},
        )
    return {key: status.get(key) for key in ("permission_status", "accessibility_trusted", "metadata_status")}


def _owned_process_rows_for_revalidation(observer: Any, root_process: subprocess.Popen,
                                         previous_identity: Any) -> list[Any]:
    """Re-snapshot the same Popen tree and require exact frozen identities."""
    from comsol_mcp._desktop_macos import TaskOwnedMacOSWindowObserver
    from comsol_mcp._desktop_service import DesktopOperationError

    if type(observer) is not TaskOwnedMacOSWindowObserver or observer._original_root is not root_process:
        raise DesktopOperationError(
            "PREAX_OWNED_PROCESS_PROOF_REJECTED",
            "pre-AX recovery requires the exact production observer and original Popen object",
        )
    observer._assert_original_root_live()
    frozen = observer._owned_processes
    if not isinstance(frozen, dict) or not frozen:
        raise DesktopOperationError("PREAX_OWNED_PROCESS_PROOF_REJECTED", "original owned process snapshot is unavailable")
    root_expected = frozen.get(root_process.pid)
    owner_expected = frozen.get(previous_identity.process_id)
    if root_expected is None or owner_expected is None:
        raise DesktopOperationError("PREAX_OWNED_PROCESS_PROOF_REJECTED", "original Popen or target owner row is absent")
    if previous_identity.process_birth != owner_expected.process_birth or \
            previous_identity.login_id != f"uid:{owner_expected.real_uid}" or \
            previous_identity.desktop_session_id != f"process-session:{owner_expected.session_id}" or \
            previous_identity.comsol_version != owner_expected.comsol_version or \
            previous_identity.platform != "macos" or \
            not re.fullmatch(r"CGWindow:[1-9][0-9]*", previous_identity.native_window_id):
        raise DesktopOperationError(
            "PREAX_OWNED_PROCESS_PROOF_REJECTED",
            "prior WindowIdentity no longer matches its exact frozen owned process row",
        )
    current_rows = _snapshot_owned_processes(
        root_process, bundle_identifier=root_expected.bundle_identifier,
        comsol_version=root_expected.comsol_version,
    )
    current = {row.process_id: row for row in current_rows}
    if set(current) != set(frozen) or any(current.get(pid) != row for pid, row in frozen.items()):
        raise DesktopOperationError(
            "PREAX_OWNED_PROCESS_TREE_CHANGED",
            "fresh original Popen descendant identities differ from the frozen task-owned tree",
            diagnostics={"frozen_member_count": len(frozen), "fresh_member_count": len(current)},
        )
    observer._assert_original_root_live()
    return sorted(current_rows, key=lambda row: row.process_id)


def _classify_preax_owned_windows(previous_identity: Any,
                                  observed_identities: Sequence[OwnedWindowIdentityTuple]) -> str:
    if not observed_identities:
        return "ZERO_OWNED_WINDOWS"
    previous = OwnedWindowIdentityTuple.from_identity(previous_identity)
    if any(type(row) is not OwnedWindowIdentityTuple for row in observed_identities):
        return "OWNED_WINDOW_TUPLE_MALFORMED_TERMINAL"
    if len(observed_identities) > 16:
        return "OWNED_WINDOW_INVENTORY_CAP_EXCEEDED_TERMINAL"
    if len(observed_identities) > 1:
        native_ids = [row.native_window_id for row in observed_identities]
        if len(set(native_ids)) != len(native_ids) or any(
                not re.fullmatch(r"CGWindow:[1-9][0-9]*", native_id) for native_id in native_ids):
            return "MULTIPLE_OWNED_WINDOWS_MALFORMED_OR_DUPLICATE_TERMINAL"
        if any((row.platform, row.process_id, row.process_birth, row.login_id,
                row.desktop_session_id, row.comsol_version) != (
                previous.platform, previous.process_id, previous.process_birth, previous.login_id,
                previous.desktop_session_id, previous.comsol_version) for row in observed_identities):
            return "MULTIPLE_OWNED_WINDOWS_OWNER_CONTEXT_CHANGED_TERMINAL"
        return "MULTIPLE_OWNED_WINDOWS_WAITING"
    current = observed_identities[0]
    if current.process_id != previous.process_id or current.process_birth != previous.process_birth:
        return "WINDOW_OWNER_PID_OR_BIRTH_CHANGED_TERMINAL"
    if current.native_window_id == previous.native_window_id:
        if current == previous:
            return "SAME_NATIVE_ID_SAME_OWNER"
        return "OLD_NATIVE_IDENTITY_TUPLE_CHANGED_TERMINAL"
    if not re.fullmatch(r"CGWindow:[1-9][0-9]*", current.native_window_id):
        return "NEW_NATIVE_WINDOW_ID_MALFORMED_TERMINAL"
    if (current.platform, current.process_id, current.process_birth, current.login_id,
            current.desktop_session_id, current.comsol_version) != (
            previous.platform, previous.process_id, previous.process_birth, previous.login_id,
            previous.desktop_session_id, previous.comsol_version):
        return "NEW_NATIVE_WINDOW_OWNER_CONTEXT_CHANGED_TERMINAL"
    return "ONE_NEW_NATIVE_ID_SAME_OWNER"


def _capture_owned_window_revalidation_receipt(observer: Any, root_process: subprocess.Popen,
                                                previous_identity: Any, *, sample_index: int,
                                                permission_reader=None) -> tuple[OwnedWindowRevalidationReceipt, list[Any], Any | None]:
    """Take one fresh production Quartz inventory with no foreign row details."""
    from comsol_mcp._desktop_macos import TaskOwnedMacOSWindowObserver
    from comsol_mcp._desktop_service import DesktopOperationError

    if type(sample_index) is not int or not 1 <= sample_index <= PREAX_REVALIDATION_MAX_SAMPLES:
        raise ValueError("pre-AX revalidation sample index is outside its fixed cap")
    _preax_accessibility_status(permission_reader)
    before = _owned_process_rows_for_revalidation(observer, root_process, previous_identity)
    frozen = observer._owned_processes
    collector = getattr(observer, "_collect_owned_window_observations", None)
    if type(observer) is not TaskOwnedMacOSWindowObserver or not callable(collector):
        raise DesktopOperationError("PREAX_OWNED_WINDOW_COLLECTOR_REJECTED",
                                    "bounded transition requires the exact production owned-Quartz collector")
    observations = list(collector())
    after = _owned_process_rows_for_revalidation(observer, root_process, previous_identity)
    if before != after:
        raise DesktopOperationError(
            "PREAX_OWNED_PROCESS_TREE_CHANGED",
            "owned process tree changed during the Quartz window revalidation",
            diagnostics={"member_count": len(before)},
        )
    current_rows = {row.process_id: row for row in after}
    owner_expected = current_rows.get(previous_identity.process_id)
    if owner_expected is None:
        raise DesktopOperationError("PREAX_OWNED_PROCESS_PROOF_REJECTED", "prior window owner is absent from the fresh Popen tree")
    observed_identities: list[OwnedWindowIdentityTuple] = []
    observed_identity_object = None
    for observation in observations:
        identity = getattr(observation, "identity", None)
        from comsol_mcp._desktop_service import WindowIdentity

        if type(identity) is not WindowIdentity:
            raise DesktopOperationError("PREAX_OWNED_WINDOW_TUPLE_REJECTED", "Quartz row identity is not the exact WindowIdentity type")
        if identity.process_id not in frozen:
            raise DesktopOperationError("PREAX_FOREIGN_WINDOW_IDENTITY", "fresh Quartz identity is outside the exact Popen-owned process tree")
        if identity.process_id != previous_identity.process_id:
            raise DesktopOperationError("PREAX_OWNED_WINDOW_OWNER_CHANGED",
                                        "owned Quartz window owner PID differs from the prior exact window owner")
        expected_process = frozen[identity.process_id]
        if identity.process_birth != expected_process.process_birth or \
                identity.login_id != f"uid:{expected_process.real_uid}" or \
                identity.desktop_session_id != f"process-session:{expected_process.session_id}" or \
                identity.comsol_version != expected_process.comsol_version:
            raise DesktopOperationError("PREAX_FOREIGN_OR_REUSED_WINDOW_IDENTITY", "fresh Quartz identity does not match its exact process proof")
        observed_identities.append(OwnedWindowIdentityTuple.from_identity(identity))
        if len(observations) == 1:
            observed_identity_object = identity
    root_proof = OwnedProcessIdentityProof.from_process_row(current_rows[root_process.pid])
    owner_proof = OwnedProcessIdentityProof.from_process_row(owner_expected)
    status = _classify_preax_owned_windows(previous_identity, observed_identities)
    receipt = OwnedWindowRevalidationReceipt(
        sample_index, status, OwnedWindowIdentityTuple.from_identity(previous_identity),
        tuple(observed_identities), root_proof, owner_proof, len(after),
        datetime.now(timezone.utc).isoformat(),
    )
    return receipt, after, observed_identity_object


def _persist_preax_revalidation_receipt(root: Path, *, candidate_sha256: str, binding_sha256: str,
                                        receipt: OwnedWindowRevalidationReceipt,
                                        receipt_ordinal: int, trigger_kind: str,
                                        trigger_error: BaseException) -> dict[str, Any]:
    payload = {"schema": "w25-owned-window-preax-revalidation/1",
               "candidate_sha256": candidate_sha256,"binding_sha256": binding_sha256,
               "receipt": asdict(receipt),
               "trigger_kind": trigger_kind,
               "trigger_error": _owned_transition_trigger_record(trigger_error),
               "privacy_scope": "owned WindowIdentity tuples and original Popen/owner process proofs only; no foreign identity, title, bounds, AX tree, or pixels"}
    return _write_new(Path(root) / f"OWNED_WINDOW_PREAX_REVALIDATION_{receipt_ordinal:03d}.json", payload)


def _bounded_owned_window_transition(root: Path, observer: Any, root_process: subprocess.Popen,
                                     previous_identity: Any, *, candidate_sha256: str,
                                     binding_sha256: str, action_ordinal: int,
                                     readiness_deadline: float, deadline: float,
                                     trigger_kind: str, trigger_error: BaseException,
                                     attempts: list[dict[str, Any]], attempt_start_index: int,
                                     receipt_ordinal_start: int, permission_reader=None,
                                     clock=None, sleeper=None) -> dict[str, Any]:
    """Wait for one stable owned Quartz row after an exact, already-permitted AX trigger.

    Zero or multiple owned rows are receipts only: they are never selected and
    never reach Accessibility. The same original Popen root, full process tree,
    nonprompt permission state, fixed readiness deadline, 60-sample cap, and
    global two-action cap apply to every sample.
    """
    from comsol_mcp._desktop_macos import TaskOwnedMacOSWindowObserver
    from comsol_mcp._desktop_service import DesktopOperationError

    if trigger_kind == "AXREF":
        allowed = _is_initial_window_association_invalid_axref(trigger_error, attempts, attempt_start_index)
        cap_code = "AXREF_REFRESH_CAP_EXCEEDED"
        timeout_code = "AXREF_REFRESH_READINESS_TIMEOUT"
    elif trigger_kind == "PREAX_AXWINDOWS":
        allowed = _is_exact_pre_ax_identity_loss_after_last_windows_retry(
            trigger_error, attempts, attempt_start_index)
        cap_code = "PREAX_RESELECTION_CAP_EXCEEDED"
        timeout_code = "PREAX_RESELECTION_READINESS_TIMEOUT"
    elif trigger_kind == "AXWINDOWS_TRANSIENT":
        allowed = _is_transient_owned_window_change(trigger_error)
        cap_code = "AX_WINDOW_TRANSITION_CAP_EXCEEDED"
        timeout_code = "AX_WINDOW_TRANSITION_READINESS_TIMEOUT"
    else:
        raise ValueError("unknown owned-window transition trigger kind")
    if not allowed:
        raise RuntimeError("bounded owned-window transition lacks its exact approved AX error gate")
    if type(action_ordinal) is not int or isinstance(action_ordinal, bool) or \
            not 1 <= action_ordinal <= MAX_AX_WINDOW_RESELECTIONS:
        raise DesktopOperationError(cap_code, "shared owned-window reselection/refresh action cap exceeded")
    if type(observer) is not TaskOwnedMacOSWindowObserver or observer._original_root is not root_process:
        raise DesktopOperationError("OWNED_WINDOW_TRANSITION_OWNER_REJECTED",
                                    "transition requires the exact production observer and original Popen handle")
    now = time.monotonic if clock is None else clock
    sleep = time.sleep if sleeper is None else sleeper
    cutoff = min(float(deadline), float(readiness_deadline))
    wait_started = now()
    records: list[dict[str, Any]] = []
    for sample_index in range(1, PREAX_REVALIDATION_MAX_SAMPLES + 1):
        if now() >= cutoff:
            raise DesktopOperationError(timeout_code, "shared AX readiness deadline expired before owned-window sampling",
                                        diagnostics={"sample_count": len(records), "readiness_deadline_monotonic": cutoff,
                                                     "trigger_kind": trigger_kind})
        receipt, rows, observed_identity = _capture_owned_window_revalidation_receipt(
            observer, root_process, previous_identity, sample_index=sample_index,
            permission_reader=permission_reader,
        )
        receipt_record = _persist_preax_revalidation_receipt(
            root, candidate_sha256=candidate_sha256, binding_sha256=binding_sha256,
            receipt=receipt, receipt_ordinal=receipt_ordinal_start + len(records) + 1,
            trigger_kind=trigger_kind, trigger_error=trigger_error,
        )
        record = {"status": receipt.status, "sample_index": sample_index,
                  "trigger_kind": trigger_kind,
                  "previous_identity_fingerprint": previous_identity.fingerprint(),
                  "receipt": receipt_record,
                  "elapsed_seconds": round(now() - wait_started, 3)}
        records.append(record)
        attempts.append({"attempt": len(attempts) + 1,
                         "status": "BOUNDED_OWNED_QUARTZ_TRANSITION_SAMPLE",
                         "revalidation_status": receipt.status,
                         "trigger_kind": trigger_kind,
                         "previous_identity_fingerprint": previous_identity.fingerprint(),
                         "receipt": receipt_record})
        if now() >= cutoff:
            raise DesktopOperationError(
                timeout_code, "shared AX readiness deadline expired during owned-window sampling",
                diagnostics={"sample_count": len(records), "revalidation_status": receipt.status,
                             "last_receipt_sha256": receipt_record["sha256"],
                             "readiness_deadline_monotonic": cutoff, "trigger_kind": trigger_kind},
            )
        if receipt.status in {"SAME_NATIVE_ID_SAME_OWNER", "ONE_NEW_NATIVE_ID_SAME_OWNER"}:
            if observed_identity is None:
                raise DesktopOperationError("OWNED_WINDOW_TRANSITION_IDENTITY_MISSING",
                                            "unique Quartz transition receipt has no typed target identity")
            if receipt.status == "SAME_NATIVE_ID_SAME_OWNER":
                proof = _same_original_root_window_proof(observed_identity, rows, root_process)
                refresh_record = _write_new(Path(root) / f"OWNED_WINDOW_AXREF_REFRESH_{action_ordinal:02d}.json", {
                    "schema": "w25-owned-window-axref-refresh/1",
                    "candidate_sha256": candidate_sha256,
                    "binding_sha256": binding_sha256,
                    "ordinal": action_ordinal,
                    "trigger_kind": trigger_kind,
                    "reason": "initial AXWindows readiness or AXPosition/AXSize invalid reference; bounded owned-Quartz refresh",
                    "trigger_ax_error": _ax_error_record(trigger_error),
                    "trigger_error": _owned_transition_trigger_record(trigger_error),
                    "previous_identity": _window_identity_value(previous_identity),
                    "fresh_identity": _window_identity_value(observed_identity),
                    "same_quartz_identity": True,
                    "owned_window_count": 1,
                    "transition_sample_receipts": [item["receipt"] for item in records],
                    "full_process_tree_equal_before_and_after_each_inventory": True,
                    "same_original_root_proof": proof,
                    "snapshot_owned_processes": [asdict(row) for row in rows],
                    "old_adapter_and_ax_enumeration_discarded": True,
                    "next_observation_constructs_fresh_adapter_then_axapplication_and_axwindows": True,
                    "readiness_deadline_monotonic": cutoff,
                    "observed_utc": datetime.now(timezone.utc).isoformat(),
                })
                transition = {"kind": "AXREF_REFRESH", "refresh_record": refresh_record}
            else:
                reselection_record = _persist_owned_window_reselection(
                    root, candidate_sha256=candidate_sha256, binding_sha256=binding_sha256,
                    ordinal=action_ordinal, previous_identity=previous_identity,
                    identity=observed_identity, rows=rows, root_process=root_process,
                    trigger_kind=trigger_kind, trigger_error=trigger_error,
                    sample_receipts=[item["receipt"] for item in records],
                )
                transition = {"kind": "WINDOW_RESELECTION", "reselection_record": reselection_record}
            if now() >= cutoff:
                raise DesktopOperationError(
                    timeout_code, "shared AX readiness deadline expired before the unique owned window could be used",
                    diagnostics={"sample_count": len(records), "revalidation_status": receipt.status,
                                 "readiness_deadline_monotonic": cutoff, "transition_record": transition},
                )
            return {"identity": observed_identity, "rows": rows, "receipts": records,
                    "sample_count": len(records), **transition}
        if receipt.status not in {"ZERO_OWNED_WINDOWS", "MULTIPLE_OWNED_WINDOWS_WAITING"}:
            raise DesktopOperationError(
                "OWNED_WINDOW_TRANSITION_REFUSED",
                "fresh owned Quartz inventory changed owner identity or failed its unique-window proof",
                diagnostics={"revalidation_status": receipt.status, "receipt_sha256": receipt_record["sha256"],
                             "sample_index": sample_index, "trigger_kind": trigger_kind},
            )
        remaining = cutoff - now()
        if remaining < PREAX_REVALIDATION_SAMPLE_SECONDS or sample_index >= PREAX_REVALIDATION_MAX_SAMPLES:
            raise DesktopOperationError(
                timeout_code, "no unique task-owned Quartz window appeared within the shared AX readiness deadline",
                diagnostics={"sample_count": len(records), "revalidation_status": receipt.status,
                             "last_receipt_sha256": receipt_record["sha256"],
                             "readiness_deadline_monotonic": cutoff, "trigger_kind": trigger_kind},
            )
        sleep(PREAX_REVALIDATION_SAMPLE_SECONDS)
    raise DesktopOperationError(timeout_code, "owned-window inventory sample cap exhausted",
                                diagnostics={"sample_count": len(records), "readiness_deadline_monotonic": cutoff,
                                             "trigger_kind": trigger_kind})


def _reselect_after_exact_preax_identity_loss(root: Path, observer: Any, root_process: subprocess.Popen,
                                              previous_identity: Any, *, candidate_sha256: str,
                                              binding_sha256: str, reselection_ordinal: int,
                                              readiness_deadline: float, deadline: float,
                                              trigger_error: BaseException,
                                              attempts: list[dict[str, Any]], attempt_start_index: int,
                                              receipt_ordinal_start: int, permission_reader=None,
                                              clock=None, sleeper=None) -> dict[str, Any]:
    """Bounded recovery after the exact initial AXWindows identity-loss gate."""
    return _bounded_owned_window_transition(
        root, observer, root_process, previous_identity, candidate_sha256=candidate_sha256,
        binding_sha256=binding_sha256, action_ordinal=reselection_ordinal,
        readiness_deadline=readiness_deadline, deadline=deadline, trigger_kind="PREAX_AXWINDOWS",
        trigger_error=trigger_error, attempts=attempts, attempt_start_index=attempt_start_index,
        receipt_ordinal_start=receipt_ordinal_start, permission_reader=permission_reader,
        clock=clock, sleeper=sleeper,
    )


def _same_original_root_window_proof(identity: Any, rows: Sequence[Any], root_process: subprocess.Popen) -> dict[str, Any]:
    """Bind a newly selected Quartz ID to a fresh snapshot under the exact live Popen root."""
    if type(root_process) is not subprocess.Popen or root_process.poll() is not None:
        raise RuntimeError("owned-window reselection requires the same live original Popen object")
    root_row = next((row for row in rows if row.process_id == root_process.pid), None)
    owned_row = next((row for row in rows if row.process_id == identity.process_id), None)
    if root_row is None or owned_row is None or root_row.parent_pid != os.getpid() or \
            root_row.process_group_id != root_process.pid or root_row.session_id != root_process.pid or \
            root_row.real_uid != os.getuid() or owned_row.process_birth != identity.process_birth or \
            owned_row.real_uid != os.getuid() or owned_row.process_group_id != root_process.pid or \
            owned_row.session_id != root_process.pid or identity.platform != "macos" or \
            identity.login_id != f"uid:{os.getuid()}" or identity.desktop_session_id != f"process-session:{root_process.pid}" or \
            not re.fullmatch(r"CGWindow:[1-9][0-9]*", identity.native_window_id):
        raise RuntimeError("fresh Quartz window is not rooted in the exact original Popen process tree")
    if root_process.poll() is not None:
        raise RuntimeError("original Popen root became terminal during owned-window reselection proof")
    return {"original_popen_pid": root_process.pid, "original_popen_birth": root_row.process_birth,
            "supervisor_pid": root_row.parent_pid, "uid": root_row.real_uid,
            "process_group_id": root_row.process_group_id, "session_id": root_row.session_id,
            "window_owner_pid": owned_row.process_id, "window_owner_birth": owned_row.process_birth,
            "window_owner_parent_pid": owned_row.parent_pid, "window_owner_command_sha256": owned_row.command_sha256,
            "same_original_popen_object": True, "root_live_before_after": True}


def _refresh_same_owned_window_after_stale_axref(root: Path, observer: Any,
                                                  root_process: subprocess.Popen, identity: Any, *,
                                                  candidate_sha256: str, binding_sha256: str,
                                                  refresh_ordinal: int, readiness_deadline: float,
                                                  deadline: float, trigger_error: BaseException,
                                                  attempts: list[dict[str, Any]],
                                                  attempt_start_index: int,
                                                  permission_reader=None, clock=None,
                                                  sleeper=None, receipt_ordinal_start: int = 0) -> dict[str, Any]:
    """Recover after the exact initial AXPosition/AXSize -25202 gate."""
    return _bounded_owned_window_transition(
        root, observer, root_process, identity, candidate_sha256=candidate_sha256,
        binding_sha256=binding_sha256, action_ordinal=refresh_ordinal,
        readiness_deadline=readiness_deadline, deadline=deadline, trigger_kind="AXREF",
        trigger_error=trigger_error, attempts=attempts, attempt_start_index=attempt_start_index,
        receipt_ordinal_start=receipt_ordinal_start, permission_reader=permission_reader,
        clock=clock, sleeper=sleeper,
    )


def _persist_owned_window_reselection(root: Path, *, candidate_sha256: str, binding_sha256: str,
                                     ordinal: int, previous_identity: Any, identity: Any,
                                     rows: Sequence[Any], root_process: subprocess.Popen,
                                     trigger_kind: str = "AXWINDOWS_TRANSIENT",
                                     trigger_error: BaseException | None = None,
                                     sample_receipts: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
    """Durably record a new owned native ID before any AX retry uses it."""
    if isinstance(ordinal, bool) or not isinstance(ordinal, int) or not 1 <= ordinal <= MAX_AX_WINDOW_RESELECTIONS:
        raise RuntimeError("owned-window reselection ordinal is outside its fixed cap")
    proof = _same_original_root_window_proof(identity, rows, root_process)
    identity_value = _window_identity_value(identity)
    return _write_new(Path(root) / f"OWNED_WINDOW_RESELECTION_{ordinal:02d}.json", {
        "schema": "w25-owned-window-reselection/1",
        "candidate_sha256": candidate_sha256,
        "binding_sha256": binding_sha256,
        "reason": "fresh unique Quartz window ID after an approved AX readiness transition",
        "trigger_kind": trigger_kind,
        "trigger_ax_error": _ax_error_record(trigger_error) if trigger_error is not None else {},
        "trigger_error": _owned_transition_trigger_record(trigger_error) if trigger_error is not None else {},
        "previous_identity_fingerprint": previous_identity.fingerprint(),
        "previous_identity": _window_identity_value(previous_identity),
        "identity": identity_value,
        "identity_fingerprint": identity.fingerprint(),
        "old_native_id_absent_in_unique_inventory": identity.native_window_id != previous_identity.native_window_id,
        "transition_sample_receipts": [dict(item) for item in sample_receipts],
        "same_original_root_proof": proof,
        "snapshot_owned_processes": [asdict(row) for row in rows],
        "observed_utc": datetime.now(timezone.utc).isoformat(),
    })


def _owned_ax_metadata_gaps(tree: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return bounded, value-free reasons an owned AX tree is partial.

    This metadata observer may preserve a partial tree for review, but a
    truncated or attribute-incomplete tree is never a selector/profile proof.
    """
    if (not isinstance(tree, Mapping) or type(tree.get("truncated")) is not bool
            or not isinstance(tree.get("nodes"), list)
            or tree.get("scope") != "TARGET_WINDOW_AX_METADATA_ONLY"
            or tree.get("model_binding") != "UNVERIFIED"):
        raise RuntimeError("owned AX metadata provider returned a malformed or broadened tree")
    gaps: list[dict[str, Any]] = []
    if tree["truncated"]:
        gaps.append({"kind": "TREE_TRUNCATED", "scope": "TARGET_WINDOW_AX_METADATA_ONLY"})
    for node in tree["nodes"]:
        if not isinstance(node, Mapping) or not isinstance(node.get("path"), list):
            raise RuntimeError("owned AX metadata node is malformed")
        path = list(node["path"])
        attributes = node.get("attribute_observations", {})
        if not isinstance(attributes, Mapping):
            raise RuntimeError("owned AX attribute diagnostics are malformed")
        for attribute, evidence in attributes.items():
            if not isinstance(attribute, str) or not isinstance(evidence, Mapping):
                raise RuntimeError("owned AX attribute diagnostics are malformed")
            gaps.append({"kind": "ATTRIBUTE_GAP", "path": path, "attribute": attribute,
                         "status": evidence.get("status"), "ax_error_code": evidence.get("ax_error_code")})
        children = node.get("children_observation", {"status": "OBSERVED"})
        if not isinstance(children, Mapping):
            raise RuntimeError("owned AX child diagnostics are malformed")
        if children.get("status") != "OBSERVED":
            gaps.append({"kind": "CHILDREN_GAP", "path": path,
                         "status": children.get("status"), "ax_error_code": children.get("ax_error_code")})
    return gaps


def _owned_observation_status(*, failure: str | None, tree: Mapping[str, Any] | None,
                              metadata_gaps: Sequence[Mapping[str, Any]],
                              cleanup: Mapping[str, Any] | None, owner_hold_entered: bool,
                              budget_exceeded: bool) -> str:
    # Terminal evidence recorded at or after the absolute total deadline is
    # never an accepted observation, even if its resource facts are clean.
    if budget_exceeded:
        return "UNKNOWN_BUDGET_EXCEEDED"
    if (failure is not None or tree is None or not cleanup
            or cleanup.get("owned_group_absent") is not True or owner_hold_entered):
        return "FAIL_PRESERVED_NATIVE_ATTEMPT"
    if metadata_gaps:
        return "PARTIAL_SCOPED_TASK_OWNED_AX_METADATA_WITH_GAPS_NOT_PROFILE_CERTIFIED"
    return "PASS_SCOPED_TASK_OWNED_AX_METADATA_ONLY"


def _fresh_owned_accessibility_observer(observer: Any) -> Any:
    """Construct a new metadata adapter; its gateway creates a fresh AXApplication on inspection."""
    from comsol_mcp._desktop_macos import MacOSAccessibilityObserver

    return MacOSAccessibilityObserver(identity_observer=observer)


def _inspect_owned_window_with_readiness(observer: Any, adapter: Any, identity: Any, *,
                                         deadline: float, attempts: list[dict[str, Any]] | None = None,
                                         readiness_deadline: float | None = None,
                                         clock=None, sleeper=None) -> dict[str, Any]:
    """Retry only the approved AX readiness reads for the same owned window.

    AXWindows may retry only in application_window_map; AXPosition and AXSize
    may retry only in window_association. Both require numeric -25204/-25212,
    and every retry re-enumerates the single task-owned Quartz row through the
    same observer retaining the original Popen process tree. Unsupported,
    permission, stale-identity, malformed-value, and all other errors remain
    terminal. A passed readiness_deadline is shared across reselections and is
    never extended by this helper.
    """
    from comsol_mcp._desktop_service import DesktopOperationError

    now = time.monotonic if clock is None else clock
    sleep = time.sleep if sleeper is None else sleeper
    rows = attempts if attempts is not None else []
    started = now()
    readiness_deadline = min(float(deadline), started + AX_READINESS_SECONDS) if readiness_deadline is None else min(
        float(deadline), float(readiness_deadline))

    def timeout(last_error: Mapping[str, Any] | None = None) -> DesktopOperationError:
        now_value = now()
        diagnostics = {
            "scope": "owned_window_readiness", "attempt_count": len(rows),
            "waited_seconds": round(now_value - started, 3),
            "readiness_deadline_monotonic": readiness_deadline,
            "window_identity_fingerprint": identity.fingerprint(),
        }
        if last_error is not None:
            diagnostics.update({"attribute": last_error.get("attribute"),
                                "attribute_scope": last_error.get("scope"),
                                "last_ax_error": dict(last_error)})
        return DesktopOperationError(
            "AX_READINESS_TIMEOUT", "owned AX readiness exceeded its single bounded wait",
            diagnostics=diagnostics,
        )

    last_error: Mapping[str, Any] | None = None
    while True:
        if now() > readiness_deadline or now() > float(deadline):
            rows.append({"attempt": len(rows) + 1, "status": "FAIL_READINESS_DEADLINE",
                         "elapsed_seconds": round(now() - started, 3),
                         "last_ax_error": None if last_error is None else dict(last_error)})
            raise timeout(last_error)
        try:
            result = adapter.inspect_accessibility(identity, max_depth=MAX_AX_DEPTH, max_nodes=MAX_AX_NODES)
            finished = now()
            if finished > readiness_deadline or finished > float(deadline):
                rows.append({"attempt": len(rows) + 1, "status": "FAIL_READINESS_DEADLINE",
                             "elapsed_seconds": round(finished - started, 3),
                             "last_ax_error": None if last_error is None else dict(last_error)})
                raise timeout(last_error)
            rows.append({"attempt": len(rows) + 1, "status": "PASS",
                         "elapsed_seconds": round(finished - started, 3)})
            return result
        except DesktopOperationError as exc:
            if exc.code == "AX_READINESS_TIMEOUT":
                raise
            diagnostics = _ax_error_record(exc)
            code = diagnostics.get("ax_error_code")
            attribute = diagnostics.get("attribute")
            scope = diagnostics.get("scope")
            exact_readiness_scope = (
                (attribute == "AXWindows" and scope == "application_window_map")
                or (attribute in {"AXPosition", "AXSize"} and scope == "window_association")
            )
            transient = (
                exc.code == "AX_ATTRIBUTE_TRANSIENT"
                and diagnostics.get("kind") == "AX_ERROR"
                and exact_readiness_scope
                and diagnostics.get("readiness_transient") is True
                and type(code) is int and code in {-25204, -25212}
            )
            if not transient:
                rows.append({"attempt": len(rows) + 1, "status": "FAIL_TERMINAL",
                             "elapsed_seconds": round(now() - started, 3),
                             "diagnostics": diagnostics or {"error_code": exc.code}})
                raise
            last_error = diagnostics
            retry_status = ("RETRY_AX_WINDOWS_READINESS" if attribute == "AXWindows"
                            else "RETRY_AX_WINDOW_GEOMETRY_ASSOCIATION")
            rows.append({"attempt": len(rows) + 1, "status": retry_status,
                         "elapsed_seconds": round(now() - started, 3), "diagnostics": diagnostics})
            current_time = now()
            if current_time >= readiness_deadline:
                raise timeout(last_error) from exc
            try:
                observed = list(observer.enumerate_windows())
            except BaseException as revalidation_error:
                raise DesktopOperationError(
                    "WINDOW_IDENTITY_REVALIDATION_FAILED",
                    "owned Quartz process/window identity could not be revalidated after transient AX error",
                    diagnostics={"scope": "readiness_revalidation", "last_ax_error": diagnostics,
                                 "revalidation_error_type": type(revalidation_error).__name__},
                ) from revalidation_error
            if len(observed) != 1 or observed[0].identity != identity:
                raise DesktopOperationError(
                    "WINDOW_IDENTITY_CHANGED",
                    "owned Quartz window identity changed during transient AX readiness wait",
                    diagnostics={"scope": "readiness_revalidation", "last_ax_error": diagnostics,
                                 "window_identity_fingerprint": identity.fingerprint(),
                                 "observed_owned_window_count": len(observed)},
                ) from exc
            remaining = min(readiness_deadline, float(deadline)) - now()
            if remaining <= 0:
                raise timeout(last_error) from exc
            sleep(min(1.0, remaining))


def _terminate_owned(root_process: subprocess.Popen, known_rows: Sequence[Any], *,
                     total_deadline_monotonic: float) -> dict[str, Any]:
    """Gracefully stop only identities captured under the original isolated Popen group."""
    import psutil
    from comsol_mcp._desktop_macos import macos_process_command_sha256

    root_pid = root_process.pid
    errors: list[str] = []
    all_rows = {row.process_id: row for row in known_rows}
    now = time.monotonic()
    if (isinstance(total_deadline_monotonic, bool)
            or not isinstance(total_deadline_monotonic, (int, float))
            or not math.isfinite(total_deadline_monotonic)):
        raise ValueError("cleanup requires the original finite total deadline")
    total_deadline_monotonic = float(total_deadline_monotonic)
    grace_deadline = min(now + 30.0, total_deadline_monotonic)

    def matches(row: Any) -> bool:
        try:
            process = psutil.Process(row.process_id)
            with process.oneshot():
                exe = process.exe(); argv = process.cmdline()
                return (f"{process.create_time():.6f}" == row.process_birth and process.uids().real == row.real_uid and
                        (process.ppid() == row.parent_pid or (root_process.poll() is not None and
                         os.getpgid(row.process_id) == root_pid and os.getsid(row.process_id) == root_pid)) and
                        os.getpgid(row.process_id) == row.process_group_id and os.getsid(row.process_id) == row.session_id and
                        exe == row.executable_path and macos_process_command_sha256(exe, argv) == row.command_sha256)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError, ValueError):
            return False

    # Re-snapshot descendants before signalling so newly observed, rooted members
    # are included. Any unrooted, mismatched, or over-cap tree prevents cleanup.
    try:
        current = _snapshot_owned_processes(root_process,
                                            bundle_identifier=known_rows[0].bundle_identifier,
                                            comsol_version=known_rows[0].comsol_version)
        all_rows.update({row.process_id: row for row in current})
    except BaseException as exc:
        if root_process.poll() is None:
            errors.append(f"owned identity refresh failed before graceful stop: {type(exc).__name__}")
            return {"original_popen_pid": root_pid, "original_popen_returncode": None,
                    "known_owned_pid_count": len(all_rows), "force_stopped_known_owned_pids": [],
                    "remaining_owned_group_or_session_pids": None, "unresolved_known_owned_pids": [],
                    "owned_group_absence_inventory_complete": False, "owned_group_absent": False,
                    "identity_refresh_complete": False, "total_deadline_monotonic": total_deadline_monotonic,
                    "cleanup_deadline_monotonic": total_deadline_monotonic,
                    "cleanup_reserve_seconds": CLEANUP_SECONDS,
                    "budget_exceeded": time.monotonic() >= total_deadline_monotonic,
                    "errors": errors}

    def depth(row: Any) -> int:
        d, pid, seen = 0, row.process_id, set()
        while pid != root_pid and pid in all_rows and pid not in seen:
            seen.add(pid); d += 1; pid = all_rows[pid].parent_pid
        return d

    # Descendants first; use psutil's birth-pinned process handle. The root is
    # signalled only through the original Popen object.
    for row in sorted(all_rows.values(), key=depth, reverse=True):
        if time.monotonic() >= total_deadline_monotonic:
            errors.append("cleanup reserve expired before all owned TERM attempts; exact owner retained")
            break
        if row.process_id == root_pid:
            try:
                if (time.monotonic() < total_deadline_monotonic and root_process.poll() is None
                        and matches(row) and time.monotonic() < total_deadline_monotonic):
                    root_process.terminate()
            except Exception as exc:
                errors.append(f"original Popen graceful stop refused: {type(exc).__name__}")
            continue
        if time.monotonic() < total_deadline_monotonic and matches(row) and time.monotonic() < total_deadline_monotonic:
            try:
                psutil.Process(row.process_id).terminate()
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError) as exc:
                errors.append(f"owned child graceful stop failed: {type(exc).__name__}")

    while time.monotonic() < grace_deadline:
        alive = []
        if root_process.poll() is None:
            alive.append(root_pid)
        for row in all_rows.values():
            if row.process_id != root_pid and matches(row):
                alive.append(row.process_id)
        if not alive:
            break
        time.sleep(0.25)

    killed: list[int] = []
    if (time.monotonic() < total_deadline_monotonic and root_process.poll() is None
            and root_pid in all_rows and matches(all_rows[root_pid])
            and time.monotonic() < total_deadline_monotonic):
        try:
            root_process.kill(); killed.append(root_pid)
        except Exception as exc:
            errors.append(f"original Popen force-stop failed: {type(exc).__name__}")
    for row in all_rows.values():
        if time.monotonic() >= total_deadline_monotonic:
            break
        if row.process_id == root_pid or not matches(row):
            continue
        if time.monotonic() >= total_deadline_monotonic:
            break
        try:
            psutil.Process(row.process_id).kill(); killed.append(row.process_id)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError) as exc:
            errors.append(f"owned child force-stop failed: {type(exc).__name__}")
    wait_remaining = max(0.0, total_deadline_monotonic - time.monotonic())
    if wait_remaining > 0.0:
        try:
            root_process.wait(timeout=min(5.0, wait_remaining))
        except subprocess.TimeoutExpired:
            errors.append("original Popen root did not become terminal before the existing cleanup deadline")
    elif root_process.poll() is None:
        errors.append("cleanup reserve expired before original Popen wait; exact owner retained")

    remaining: list[int] = []
    absence_inventory_ok = True
    try:
        for proc in psutil.process_iter(["pid"]):
            pid = proc.info["pid"]
            try:
                if os.getpgid(pid) == root_pid or os.getsid(pid) == root_pid:
                    remaining.append(pid)
            except psutil.NoSuchProcess:
                continue
            except (psutil.AccessDenied, psutil.ZombieProcess, OSError):
                absence_inventory_ok = False
    except Exception as exc:
        absence_inventory_ok = False
        errors.append(f"owned group absence inventory failed: {type(exc).__name__}")
    unresolved: list[int] = []
    for row in all_rows.values():
        try:
            current = psutil.Process(row.process_id)
            if f"{current.create_time():.6f}" == row.process_birth and current.status() != psutil.STATUS_ZOMBIE and not matches(row):
                unresolved.append(row.process_id)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
            continue
    return {"original_popen_pid": root_pid, "original_popen_returncode": root_process.poll(),
            "known_owned_pid_count": len(all_rows), "force_stopped_known_owned_pids": sorted(set(killed)),
            "remaining_owned_group_or_session_pids": sorted(set(remaining)),
            "unresolved_known_owned_pids": sorted(set(unresolved)),
            "owned_group_absence_inventory_complete": absence_inventory_ok,
            "owned_group_absent": absence_inventory_ok and not remaining and not unresolved and root_process.poll() is not None,
            "total_deadline_monotonic": total_deadline_monotonic,
            "cleanup_deadline_monotonic": total_deadline_monotonic,
            "cleanup_reserve_seconds": CLEANUP_SECONDS,
            "cleanup_finished_monotonic": time.monotonic(),
            "budget_exceeded": time.monotonic() >= total_deadline_monotonic,
            "budget_overrun_seconds": max(0.0, time.monotonic() - total_deadline_monotonic),
            "errors": errors}


def _start_bounded_drainer(pipe: Any, stream: Any, log_path: Path, *, total: list[int], lock: threading.Lock,
                           output_exceeded: threading.Event, output_errors: list[str]) -> threading.Thread:
    def drain() -> None:
        try:
            while True:
                chunk = pipe.read(65536)
                if not chunk:
                    break
                with lock:
                    remaining = max(0, MAX_OUTPUT_BYTES - total[0])
                    bounded = chunk[:remaining]
                    if bounded:
                        stream.write(bounded)
                        total[0] += len(bounded)
                    if len(chunk) > remaining:
                        output_exceeded.set()
        except Exception as exc:
            output_errors.append(f"{type(exc).__name__}: {exc}")
            output_exceeded.set()
        finally:
            try:
                stream.flush()
                os.fsync(stream.fileno())
            except Exception as exc:
                output_errors.append(f"log flush {type(exc).__name__}: {exc}")
                output_exceeded.set()
            stream.close()
            try:
                pipe.close()
            except Exception:
                pass

    thread = threading.Thread(target=drain, name=f"w25-log-{log_path.name}", daemon=False)
    thread.start()
    return thread


def execute(root: Path, binding_path: Path, binding_sha: str) -> dict[str, Any]:
    """Run only after Main signs this exact candidate and a fresh lane record."""
    root = Path(root)
    candidate, binding, lane = verify_authority(root, Path(binding_path), binding_sha)
    plan = _read_json(Path(candidate["plan"]["path"]))
    if Path(sys.executable).resolve() != Path(plan["runner_interpreter_required"]).resolve():
        raise RuntimeError("W25 runner interpreter is not the exact fresh product venv")
    if candidate.get("native_Popen") != 0 or candidate.get("r9_profile") != CURRENT_R9:
        raise RuntimeError("W25 candidate is stale or carries prior native execution")
    if ((root / "APPLICATION_DESKTOP_OBSERVATION_RESULT_R1.json").exists()
            or (root / "APPLICATION_DESKTOP_OBSERVATION_PARTIAL_RESULT_R1.json").exists()
            or (root / "FIRST_FAILURE.json").exists()
            or (root / "OWNED_WINDOW_FIRST.json").exists()
            or any((root / f"STARTUP_CHILD_EXIT_RECOVERY_{ordinal:02d}.json").exists()
                   for ordinal in range(1, MAX_STARTUP_CHILD_EXIT_RECOVERIES + 1))):
        raise RuntimeError("W25 launch attempt already has terminal/failure evidence; no replay")
    expected_launcher = file_record(LAUNCHER)
    source = json.loads(Path(candidate["source"]["path"]).read_text(encoding="utf-8"))
    if expected_launcher["sha256"] != source[str(LAUNCHER)]["sha256"]:
        raise RuntimeError("COMSOL launcher changed since candidate preparation")
    installed_root = Path(candidate["installed_root"])
    _activate_current_desktop_overlay(installed_root)
    current_os_build = None
    from comsol_mcp._desktop_macos import MacOSDesktopAdapter
    current_os_build = MacOSDesktopAdapter._os_build()
    caller = _installed_caller_status(Path(plan["runner_interpreter_required"]), installed_root)
    # A second timestamp/lane check is required immediately before consuming once.
    candidate, binding, lane = verify_authority(root, Path(binding_path), binding_sha)
    for name in ("prefs", "configuration", "recovery", "tmp", "home", "logs", "evidence"):
        path = root / name
        if path.is_symlink() or not path.is_dir() or path.stat().st_uid != os.getuid() or stat.S_IMODE(path.stat().st_mode) != 0o700:
            raise RuntimeError("W25 exact private execution directory changed")
    stdout_path, stderr_path = root / "logs/comsol.stdout", root / "logs/comsol.stderr"
    if stdout_path.exists() or stderr_path.exists() or stdout_path.is_symlink() or stderr_path.is_symlink():
        raise RuntimeError("W25 fresh command logs are occupied")
    argv = _exact_argv(root)
    if _sha(_json_bytes(argv)) != binding["argv_sha256"]:
        raise RuntimeError("W25 direct COMSOL argv differs from exact Main authority")
    _write_new(root / "UNIT_ONCE_GUARD.json", {"schema": "w25-macos-owned-desktop-once/1",
                                               "candidate_sha256": file_record(root / "ACTOR_CANDIDATE_macos1.json")["sha256"],
                                               "review_sha256": file_record(root / "MAIN_SOURCE_REVIEW.json")["sha256"],
                                               "plan_sha256": candidate["plan"]["sha256"],
                                               "source_sha256": candidate["source"]["sha256"],
                                               "lane_check_sha256": file_record(root / "W25_LANE_CHECK_CURRENT.json")["sha256"],
                                               "binding_sha256": binding_sha, "nonce_sha256": _sha(binding["nonce"].encode()),
                                               "pid": os.getpid(), "consumed_utc": datetime.now(timezone.utc).isoformat()})
    env = {"HOME": str(root / "home"), "TMPDIR": str(root / "tmp"),
           "PATH": "/Applications/COMSOL64/Multiphysics/bin:/usr/bin:/bin:/usr/sbin:/sbin",
           "LANG": "C", "LC_ALL": "C"}
    for name in ("LMCOMSOL_LICENSE_FILE", "LM_LICENSE_FILE"):
        if name in os.environ:
            env[name] = os.environ[name]
    observed: dict[str, Any] | None = None
    window_identity: dict[str, Any] | None = None
    active_identity: Any | None = None
    owned_window_first_record: dict[str, Any] | None = None
    owned_window_reselection_records: list[dict[str, Any]] = []
    owned_window_preax_revalidation_records: list[dict[str, Any]] = []
    owned_window_axref_refresh_records: list[dict[str, Any]] = []
    owned_window_initial_ambiguity_records: list[dict[str, Any]] = []
    owned_startup_child_exit_recovery_records: list[dict[str, Any]] = []
    reselection_count = 0
    readiness_deadline: float | None = None
    tree: dict[str, Any] | None = None
    metadata_gaps: list[dict[str, Any]] = []
    ax_readiness_attempts: list[dict[str, Any]] = []
    start = time.monotonic()
    total_deadline = start + BOUNDED_RUNTIME_SECONDS
    active_deadline = total_deadline - CLEANUP_SECONDS
    deadline = active_deadline
    owner = W25OriginalProcessOwner(
        root, stdout_path, stderr_path, total_deadline_monotonic=total_deadline,
    )
    output_records: dict[str, Any] = {}
    failure: str | None = None
    try:
        owner.open_logs()
        process = owner.popen(argv, shell=False, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              cwd=str(root), env=env, start_new_session=True, close_fds=True, bufsize=0)
        while time.monotonic() < deadline:
            if owner.output_exceeded.is_set():
                raise RuntimeError("COMSOL output exceeded the fixed byte cap or a bounded log write failed")
            if process.poll() is not None:
                raise RuntimeError("COMSOL exited before one owned visible window was observed")
            owner.rows = _snapshot_owned_processes(process, bundle_identifier="com.comsol.comsol",
                                                  comsol_version="6.4.0")
            inventory = _enumerate_owned_windows_with_startup_recovery(
                root, process, owner.rows,
                ordinal=len(owned_startup_child_exit_recovery_records) + 1,
                deadline=deadline,
                candidate_sha256=file_record(root / "ACTOR_CANDIDATE_macos1.json")["sha256"],
                binding_sha256=binding_sha,
                before_first_window=active_identity is None and owned_window_first_record is None,
            )
            if inventory["recovery_receipt"] is not None:
                owned_startup_child_exit_recovery_records.append(inventory["recovery_receipt"])
                owner.rows = inventory["rows"]
                delay = inventory["retry_after_seconds"]
                del inventory
                time.sleep(delay)
                continue
            observer = inventory["observer"]
            windows = inventory["windows"]
            if len(windows) > 1:
                owned_tuples = []
                for observation in windows:
                    identity = getattr(observation, "identity", None)
                    from comsol_mcp._desktop_service import WindowIdentity
                    if type(identity) is not WindowIdentity:
                        raise RuntimeError("ambiguous owned Quartz inventory contains a malformed identity tuple")
                    owned_tuples.append(_window_identity_value(identity))
                ambiguity_record = _write_new(root / "OWNED_WINDOW_INITIAL_AMBIGUITY.json", {
                    "schema": "w25-owned-window-initial-ambiguity/1",
                    "candidate_sha256": file_record(root / "ACTOR_CANDIDATE_macos1.json")["sha256"],
                    "binding_sha256": binding_sha,
                    "status": "MULTIPLE_OWNED_WINDOWS_NO_SELECTION",
                    "owned_window_count": len(owned_tuples),
                    "owned_identity_tuples": owned_tuples,
                    "snapshot_owned_processes": [asdict(row) for row in owner.rows],
                    "ax_called": False,
                    "selection_attempted": False,
                    "privacy_scope": "owned identity tuples and process rows only; no foreign identity, titles, bounds, AX tree, or pixels",
                    "observed_utc": datetime.now(timezone.utc).isoformat(),
                })
                owned_window_initial_ambiguity_records.append(ambiguity_record)
                raise RuntimeError("multiple task-owned visible windows appeared before an approved AX transition; no selection")
            if not windows:
                if active_identity is not None:
                    raise RuntimeError("task-owned visible window disappeared without an explicit transient AXWindows mismatch")
                time.sleep(1)
                continue

            identity = windows[0].identity
            if active_identity is None:
                active_identity = identity
                readiness_deadline = min(deadline, time.monotonic() + AX_READINESS_SECONDS)
                window_identity = _window_identity_value(identity)
                # Preserve the initial native handle before the first AX request.
                owned_window_first_record = _write_new(root / "OWNED_WINDOW_FIRST.json", {
                    "schema": "w25-owned-window-first-observation/1",
                    "candidate_sha256": file_record(root / "ACTOR_CANDIDATE_macos1.json")["sha256"],
                    "binding_sha256": binding_sha,
                    "identity": window_identity,
                    "identity_fingerprint": identity.fingerprint(),
                    "original_popen_pid": process.pid,
                    "observed_utc": datetime.now(timezone.utc).isoformat(),
                })
            elif identity != active_identity:
                raise RuntimeError("task-owned Quartz window changed without an explicit transient AXWindows mismatch")

            adapter = _fresh_owned_accessibility_observer(observer)
            attempt_start_index = len(ax_readiness_attempts)
            while True:
                try:
                    observed = _inspect_owned_window_with_readiness(
                        observer, adapter, identity, deadline=deadline, attempts=ax_readiness_attempts,
                        readiness_deadline=readiness_deadline,
                    )
                except BaseException as exc:
                    if _is_transient_owned_window_change(exc):
                        if reselection_count >= MAX_AX_WINDOW_RESELECTIONS:
                            raise RuntimeError("transient AXWindows replacement exceeded the fixed Quartz reselection cap") from exc
                        if readiness_deadline is None:
                            raise RuntimeError("AXWindows transition has no original shared readiness deadline") from exc
                        recovery = _bounded_owned_window_transition(
                            root, observer, process, identity,
                            candidate_sha256=file_record(root / "ACTOR_CANDIDATE_macos1.json")["sha256"],
                            binding_sha256=binding_sha, action_ordinal=reselection_count + 1,
                            readiness_deadline=readiness_deadline, deadline=deadline,
                            trigger_kind="AXWINDOWS_TRANSIENT", trigger_error=exc,
                            attempts=ax_readiness_attempts, attempt_start_index=attempt_start_index,
                            receipt_ordinal_start=len(owned_window_preax_revalidation_records),
                        )
                        owned_window_preax_revalidation_records.extend(recovery["receipts"])
                        if recovery["kind"] == "AXREF_REFRESH":
                            transition_record = recovery["refresh_record"]
                            owned_window_axref_refresh_records.append(transition_record)
                        else:
                            transition_record = recovery["reselection_record"]
                            owned_window_reselection_records.append(transition_record)
                        reselection_count += 1
                        owner.rows = recovery["rows"]
                        active_identity = recovery["identity"]
                        identity = active_identity
                        window_identity = _window_identity_value(identity)
                        adapter = None
                        del exc
                        adapter = _fresh_owned_accessibility_observer(observer)
                        attempt_start_index = len(ax_readiness_attempts)
                        continue
                    if _is_initial_window_association_invalid_axref(
                            exc, ax_readiness_attempts, attempt_start_index):
                        if readiness_deadline is None:
                            raise RuntimeError("initial AXREF loss has no original shared AX readiness deadline") from exc
                        if reselection_count >= MAX_AX_WINDOW_RESELECTIONS:
                            raise RuntimeError("initial AXREF loss exceeded the shared total Quartz reselection/refresh cap") from exc
                        recovery = _refresh_same_owned_window_after_stale_axref(
                            root, observer, process, identity,
                            candidate_sha256=file_record(root / "ACTOR_CANDIDATE_macos1.json")["sha256"],
                            binding_sha256=binding_sha, refresh_ordinal=reselection_count + 1,
                            readiness_deadline=readiness_deadline, deadline=deadline,
                            trigger_error=exc, attempts=ax_readiness_attempts,
                            attempt_start_index=attempt_start_index,
                            receipt_ordinal_start=len(owned_window_preax_revalidation_records),
                        )
                        owned_window_preax_revalidation_records.extend(recovery["receipts"])
                        if recovery["kind"] == "AXREF_REFRESH":
                            transition_record = recovery["refresh_record"]
                            owned_window_axref_refresh_records.append(transition_record)
                        else:
                            transition_record = recovery["reselection_record"]
                            owned_window_reselection_records.append(transition_record)
                        reselection_count += 1
                        owner.rows = recovery["rows"]
                        ax_readiness_attempts.append({
                            "attempt": len(ax_readiness_attempts) + 1,
                            "status": ("REFRESH_AX_APPLICATION_WINDOWS_AFTER_INITIAL_INVALID_REFERENCE"
                                       if recovery["kind"] == "AXREF_REFRESH" else "RESELECT_UNIQUE_NEW_OWNED_QUARTZ_ID"),
                            "transition_record": transition_record,
                            "same_quartz_identity": recovery["kind"] == "AXREF_REFRESH",
                            "shared_readiness_deadline_monotonic": readiness_deadline,
                            "global_reselection_refresh_count": reselection_count,
                        })
                        active_identity = recovery["identity"]
                        identity = recovery["identity"]
                        window_identity = _window_identity_value(identity)
                        # Clear the failing call's traceback before creating a new
                        # adapter so no stale AXUIElement from that enumeration survives.
                        adapter = None
                        del exc
                        adapter = _fresh_owned_accessibility_observer(observer)
                        attempt_start_index = len(ax_readiness_attempts)
                        continue
                    if not _is_exact_pre_ax_identity_loss_after_last_windows_retry(
                            exc, ax_readiness_attempts, attempt_start_index):
                        raise
                    if readiness_deadline is None:
                        raise RuntimeError("pre-AX identity loss has no original shared AX readiness deadline") from exc
                    if reselection_count >= MAX_AX_WINDOW_RESELECTIONS:
                        raise RuntimeError("pre-AX identity loss exceeded the fixed Quartz reselection cap") from exc
                    recovery = _reselect_after_exact_preax_identity_loss(
                        root, observer, process, identity,
                        candidate_sha256=file_record(root / "ACTOR_CANDIDATE_macos1.json")["sha256"],
                        binding_sha256=binding_sha, reselection_ordinal=reselection_count + 1,
                        readiness_deadline=readiness_deadline, deadline=deadline,
                        trigger_error=exc, attempts=ax_readiness_attempts,
                        attempt_start_index=attempt_start_index,
                        receipt_ordinal_start=len(owned_window_preax_revalidation_records),
                    )
                    owned_window_preax_revalidation_records.extend(recovery["receipts"])
                    if recovery["kind"] == "AXREF_REFRESH":
                        transition_record = recovery["refresh_record"]
                        owned_window_axref_refresh_records.append(transition_record)
                    else:
                        transition_record = recovery["reselection_record"]
                        owned_window_reselection_records.append(transition_record)
                    reselection_count += 1
                    active_identity = recovery["identity"]
                    identity = active_identity
                    owner.rows = recovery["rows"]
                    window_identity = _window_identity_value(identity)
                    attempt_start_index = len(ax_readiness_attempts)
                    adapter = _fresh_owned_accessibility_observer(observer)
                    continue
                else:
                    break
            window_identity = _window_identity_value(identity)
            metadata_gaps = _owned_ax_metadata_gaps(observed)
            tree = observed
            break
        else:
            raise TimeoutError("no owned visible Desktop window appeared before the active cutoff; cleanup reserve remains")
    except BaseException as exc:
        owner.fail(exc, "task_owned_observation")
        failure = f"{type(exc).__name__}: {owner._safe_text(exc)}"

    cleanup: dict[str, Any] | None = None
    if owner.process is not None and owner.spawned and not owner.released:
        try:
            cleanup = owner.cleanup_or_hold(
                owner.rows, total_deadline_monotonic=total_deadline,
            )
        except BaseException as exc:
            owner.fail(exc, "outer_cleanup_boundary")
            # This is deliberately passive. If cleanup itself became uncertain,
            # do not replace it with a PID/group SIGKILL or lose the Popen.
            owner.passive_hold(exc)
            cleanup = owner.cleanup
    elif owner.process is not None and not owner.spawned:
        # A pre-birth Popen initialization failure has no child to retain.
        owner.process = None
        _RETAINED_OWNERS.pop(str(root), None)

    process = owner.process
    if process is not None and owner.spawned and not owner.released:
        # No exception, including KeyboardInterrupt/SystemExit during result
        # construction or evidence writes, may leave a potentially live owner.
        try:
            owner.passive_hold("post-cleanup owner still unresolved")
            cleanup = owner.cleanup
        except BaseException as exc:
            owner.fail(exc, "post_cleanup_owner_guard")
            while not owner.released:
                owner.passive_hold(exc)
            cleanup = owner.cleanup

    for stream in owner.streams.values():
        try:
            if not stream.closed:
                stream.close()
        except BaseException as exc:
            owner.note("close_terminal_log_stream", exc)
    try:
        output_records = {"stdout": file_record(stdout_path), "stderr": file_record(stderr_path)}
    except BaseException as exc:
        owner.note("record_terminal_log_evidence", exc)
        owner.output_errors.append(f"log evidence record failed: {type(exc).__name__}")
    if owner.output_errors and failure is None:
        failure = "; ".join(owner.output_errors)
    if owner.first_failure is not None and failure is None:
        first = owner.first_failure
        failure = f"{first['phase']}: {first['type']}: {first['error']}"
    # Capture the terminal clock exactly once, after cleanup/evidence work and
    # before status classification. The boundary is inclusive: elapsed time
    # reaching the absolute deadline is over budget.
    terminal_snapshot_monotonic = time.monotonic()
    terminal_elapsed_seconds = max(0.0, terminal_snapshot_monotonic - start)
    terminal_budget_exceeded = terminal_snapshot_monotonic >= total_deadline
    terminal_budget_overrun_seconds = max(0.0, terminal_snapshot_monotonic - total_deadline)
    if terminal_budget_exceeded and failure is None:
        failure = "terminal observation evidence reached or exceeded the absolute total runtime deadline"
    result_status = _owned_observation_status(failure=failure, tree=tree, metadata_gaps=metadata_gaps,
                                              cleanup=cleanup, owner_hold_entered=owner.hold_entered,
                                              budget_exceeded=terminal_budget_exceeded)
    partial_observation = result_status == "PARTIAL_SCOPED_TASK_OWNED_AX_METADATA_WITH_GAPS_NOT_PROFILE_CERTIFIED"
    terminal_resources = {
        "schema": "w25-owned-desktop-terminal-resources/1",
        "cleanup_status": None if not isinstance(cleanup, Mapping) else cleanup.get("status"),
        "owned_group_absent": None if not isinstance(cleanup, Mapping) else cleanup.get("owned_group_absent"),
        "popen_pid": None if process is None else process.pid,
        "popen_returncode": None if process is None else process.poll(),
        "owner_released": owner.released,
        "owner_hold_entered": owner.hold_entered,
        "recorded_separately_from_acceptance": True,
    }
    result = {"schema": "w25-macos-owned-desktop-observation-result/1",
              "budget": {"total_budget_seconds": BOUNDED_RUNTIME_SECONDS,
                         "active_observation_budget_seconds": ACTIVE_RUNTIME_SECONDS,
                         "cleanup_reserve_seconds": CLEANUP_SECONDS,
                         "active_deadline_monotonic": active_deadline,
                         "total_deadline_monotonic": total_deadline,
                         "terminal_snapshot_monotonic": terminal_snapshot_monotonic,
                         "terminal_elapsed_seconds": round(terminal_elapsed_seconds, 3),
                         "deadline_policy": "one total deadline; observation stops at total minus cleanup reserve; cleanup receives only that same total deadline",
                         "terminal_boundary_policy": "budget_exceeded when terminal_snapshot_monotonic >= total_deadline_monotonic",
                         "budget_exceeded": terminal_budget_exceeded,
                         "budget_overrun_seconds": round(terminal_budget_overrun_seconds, 3)},
              "status": result_status,
              "candidate_sha256": file_record(root / "ACTOR_CANDIDATE_macos1.json")["sha256"],
              "binding_sha256": binding_sha, "lane_check": lane, "installed_product_caller": caller,
              "host_os_build": current_os_build, "argv": argv, "argv_sha256": _sha(_json_bytes(argv)),
              "popen_pid": None if process is None else process.pid,
              "popen_returncode": None if process is None else process.poll(),
              "owned_window": window_identity, "owned_window_first_record": owned_window_first_record,
              "owned_window_reselection_records": owned_window_reselection_records,
              "owned_window_preax_revalidation_records": owned_window_preax_revalidation_records,
              "owned_window_axref_refresh_records": owned_window_axref_refresh_records,
              "owned_window_initial_ambiguity_records": owned_window_initial_ambiguity_records,
              "owned_startup_child_exit_recovery_records": owned_startup_child_exit_recovery_records,
              "owned_ax_tree": tree, "ax_readiness_attempts": ax_readiness_attempts,
              "metadata_gaps": metadata_gaps,
              "ownership_rows": [asdict(row) for row in owner.rows], "cleanup": cleanup,
              "terminal_resources": terminal_resources,
              "original_owner": {"held_unknown": owner.hold_entered, "released_after_process_and_group_terminal": owner.released,
                                 "signals_during_unknown_hold": 0, "first_failure": owner.first_failure,
                                 "total_deadline_monotonic": total_deadline,
                                 "budget_exceeded": terminal_budget_exceeded,
                                 "budget_overrun_seconds": round(terminal_budget_overrun_seconds, 3),
                                 "diagnostic_errors": owner.diagnostic_errors},
              "output_bytes": owner.output_total[0], "output_records": output_records,
              "output_errors": owner.output_errors, "owner_diagnostic_errors": owner.diagnostic_errors,
              "elapsed_seconds": round(terminal_elapsed_seconds, 3), "failure": failure,
              "model_binding": "UNVERIFIED", "endpoint": "NOT_OBSERVED", "endpoint_challenge": "NOT_RUN",
              "selector_profile": "UNAVAILABLE", "selector_profile_certification": "NOT_CERTIFIED" if partial_observation else "NOT_RUN",
              "control_actions": "NOT_RUN", "pixels": "NOT_RUN",
              "native_engine": "NOT_PROBED", "solver_science": "NOT_RUN", "release_acceptance": "NOT_RUN"}
    try:
        if result_status == "PARTIAL_SCOPED_TASK_OWNED_AX_METADATA_WITH_GAPS_NOT_PROFILE_CERTIFIED":
            _write_new(root / "APPLICATION_DESKTOP_OBSERVATION_PARTIAL_RESULT_R1.json", result)
        elif result["status"] != "PASS_SCOPED_TASK_OWNED_AX_METADATA_ONLY":
            _write_new(root / "FIRST_FAILURE.json", result)
        else:
            _write_new(root / "APPLICATION_DESKTOP_OBSERVATION_RESULT_R1.json", result)
    except BaseException as exc:
        owner.note("write_terminal_result_evidence", exc)
        if owner.process is not None and owner.spawned and not owner.released:
            owner.passive_hold(exc)
        raise
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    prepare_parser = commands.add_parser("prepare")
    prepare_parser.add_argument("--root", type=Path, required=True)
    prepare_parser.add_argument("--installed-root", type=Path, required=True)
    execute_parser = commands.add_parser("execute")
    execute_parser.add_argument("--root", type=Path, required=True)
    execute_parser.add_argument("--binding", type=Path, required=True)
    execute_parser.add_argument("--binding-sha", required=True)
    args = parser.parse_args(argv)
    if args.action == "prepare":
        print(json.dumps(prepare(args.root, args.installed_root), sort_keys=True))
        return 0
    print(json.dumps(execute(args.root, args.binding, args.binding_sha), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
