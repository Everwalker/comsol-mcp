#!/usr/bin/env python3
"""Preserve exact combined-candidate JUnit/logs and report corrected counts."""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import shutil
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

REPO = pathlib.Path("/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2/repository")
EVIDENCE = pathlib.Path(__file__).resolve().parent
CANDIDATE = pathlib.Path("/private/tmp/w25-r5-r3-wfp-v2-r4-candidate-20261012")
SOURCE_EVIDENCE = CANDIDATE / "evidence"
DEST = EVIDENCE / "combined-r5-wfp-r4-r1"


def sha(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def copy_no_clobber(source: pathlib.Path, target: pathlib.Path) -> dict:
    source_before = source.stat(follow_symlinks=False)
    if source.is_symlink() or not source.is_file():
        raise SystemExit(f"not a regular evidence source: {source}")
    source_hash = sha(source)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(target, flags, 0o600)
    try:
        with source.open("rb") as inp:
            while True:
                data = inp.read(1024 * 1024)
                if not data:
                    break
                view = memoryview(data)
                while view:
                    count = os.write(fd, view)
                    if count <= 0:
                        raise OSError("short evidence copy")
                    view = view[count:]
        os.fsync(fd)
    finally:
        os.close(fd)
    source_after = source.stat(follow_symlinks=False)
    if ((source_before.st_dev, source_before.st_ino, source_before.st_size,
         source_before.st_mtime_ns, source_before.st_ctime_ns) !=
        (source_after.st_dev, source_after.st_ino, source_after.st_size,
         source_after.st_mtime_ns, source_after.st_ctime_ns)):
        raise SystemExit(f"source changed during preservation: {source}")
    target_hash = sha(target)
    if target_hash != source_hash or target.stat().st_size != source_before.st_size:
        raise SystemExit(f"preserved bytes differ: {target}")
    return {"source": str(source), "target": str(target),
            "sha256": target_hash, "size_bytes": target.stat().st_size}


def write_once(path: pathlib.Path, value: dict) -> str:
    raw = (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        view = memoryview(raw)
        while view:
            count = os.write(fd, view)
            if count <= 0:
                raise OSError("short metadata write")
            view = view[count:]
        os.fsync(fd)
    finally:
        os.close(fd)
    return hashlib.sha256(raw).hexdigest()


def main() -> int:
    if DEST.exists() or DEST.is_symlink():
        raise SystemExit(f"no-clobber destination exists: {DEST}")
    DEST.mkdir(mode=0o700)
    names = [
        "COMBINED_CANDIDATE_R2.json",
        "COMBINED_R5_WFP_R4_ARGV.json",
        "COMBINED_R5_WFP_R4.stdout.log",
        "COMBINED_R5_WFP_R4.stderr.log",
        "COMBINED_R5_WFP_R4.junit.xml",
        "COMBINED_R5_WFP_R4_NETWORK_GUARD.json",
        "COMBINED_R5_WFP_R4_RESULT.json",
        "COMBINED_R5_WFP_R4_ATTEMPT2_ARGV.json",
        "COMBINED_R5_WFP_R4_ATTEMPT2.stdout.log",
        "COMBINED_R5_WFP_R4_ATTEMPT2.stderr.log",
        "COMBINED_R5_WFP_R4_ATTEMPT2.junit.xml",
        "COMBINED_R5_WFP_R4_ATTEMPT2.NETWORK_GUARD.json",
        "COMBINED_R5_WFP_R4_ATTEMPT2_RESULT.json",
        "COMBINED_R5_WFP_R4_ATTEMPT3_ARGV.json",
        "COMBINED_R5_WFP_R4_ATTEMPT3.stdout.log",
        "COMBINED_R5_WFP_R4_ATTEMPT3.stderr.log",
        "COMBINED_R5_WFP_R4_ATTEMPT3.junit.xml",
        "COMBINED_R5_WFP_R4_ATTEMPT3.NETWORK_GUARD.json",
        "COMBINED_R5_WFP_R4_ATTEMPT3_RESULT.json",
    ]
    files = [copy_no_clobber(SOURCE_EVIDENCE / name, DEST / name) for name in names]
    final_xml = DEST / "COMBINED_R5_WFP_R4_ATTEMPT3.junit.xml"
    root = ET.parse(final_xml).getroot()
    suite = next((node for node in root.iter("testsuite")), None)
    if suite is None:
        raise SystemExit("JUnit has no testsuite")
    regular_nodes = list(root.iter("testcase"))
    xml_counts = {
        "junit_root": root.tag,
        "testsuite_tests_attribute": int(suite.attrib.get("tests", 0)),
        "regular_testcase_nodes": len(regular_nodes),
        "skipped_testcase_nodes": sum(len(node.findall("skipped")) for node in regular_nodes),
        "failure_nodes": sum(len(node.findall("failure")) for node in regular_nodes),
        "error_nodes": sum(len(node.findall("error")) for node in regular_nodes),
    }
    candidate = json.loads((DEST / "COMBINED_CANDIDATE_R2.json").read_text())
    result3 = json.loads((DEST / "COMBINED_R5_WFP_R4_ATTEMPT3_RESULT.json").read_text())
    corrected = {
        "schema": "comsol-mcp.w25-r5-wfp-r4-combined-closure/1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "status": "PASS_SCOPED_SOFTWARE",
        "candidate": {
            "path": str(CANDIDATE),
            "receipt_path": str(DEST / "COMBINED_CANDIDATE_R2.json"),
            "receipt_sha256": sha(DEST / "COMBINED_CANDIDATE_R2.json"),
            "source_count": candidate["source_file_count"],
            "source_tree_sha256": candidate["candidate_tree_sha256"],
        },
        "frozen_inputs": {
            "r5": candidate["r5_freeze"],
            "wfp_v2_r4": candidate["wfp_r4_freeze"],
            "wfp_r4_preservation": candidate["wfp_r4_preservation"],
        },
        "test_scope": {
            "modules": [
                "tests/test_desktop_control_wiring.py",
                "tests/test_desktop_native_adapters_w25.py",
                "tests/test_desktop_service.py",
                "tests/test_desktop_sticky_effects_phase2c.py",
                "tests/test_project_file_stream_publish.py",
                "tests/test_windows_parameter_publish_native.py",
                "tests/test_windows_host_change.py",
                "tests/test_windows_t037_native.py",
                "tests/test_operation_store.py",
                "tests/test_operation_store_migrations.py",
            ],
            "command_argv": result3["argv"]["value"],
            "environment": {"PYTHONDONTWRITEBYTECODE": "1",
                            "TMPDIR": "/private/var/folders/4d/6pphmzkx5szf8dyqx7zhqh1r0000gn/T",
                            "TMPDIR_identity": {"device": 16777231, "inode": 297256},
                            "network_guard_attempts": 0},
            "pytest_stdout_summary": "193 passed, 9 skipped, 1 deselected, 20 subtests passed",
            "junit_raw": {"path": str(DEST / "COMBINED_R5_WFP_R4_ATTEMPT3.junit.xml"),
                          "sha256": sha(final_xml), **xml_counts},
            "interpretation": "JUnit suite tests=222 includes subtest accounting; 202 explicit testcase nodes comprise 193 pass and 9 skipped. Pytest's 20 subtests are separately reported; the historical Git-object case is one deselected test.",
            "source_inputs_identical": result3["source_inputs_identical"],
            "source_hash_map_bound": {
                "before_sha256": hashlib.sha256(json.dumps(result3["source_input_hashes_before"], sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                "after_sha256": hashlib.sha256(json.dumps(result3["source_input_hashes_after"], sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                "file_count": len(result3["source_input_hashes_before"]),
            },
        },
        "preserved_prior_attempts": [
            {
                "attempt": 1,
                "status": "FAILED_ENVIRONMENT_ALIAS",
                "reason": "Default /var/folders temporary path contains a symlink; the production path verifier correctly refused it.",
                "pytest_output": "103 passed before the first failure.",
                "network_attempts": 0,
                "raw_files_prefix": "COMBINED_R5_WFP_R4",
            },
            {
                "attempt": 2,
                "status": "FAILED_HISTORICAL_GIT_FIXTURE",
                "reason": "The isolated candidate does not contain historical Git commit a3418bd4546ecc33e26448e7f2a1982727a37153; the exact WFP R4 test invocation excludes this inherited Git-object case.",
                "pytest_output": "191 passed, 9 skipped, 20 subtests passed before the historical Git-object case failed.",
                "network_attempts": 0,
                "raw_files_prefix": "COMBINED_R5_WFP_R4_ATTEMPT2",
            },
        ],
        "third_attempt_receipt": {
            "path": str(DEST / "COMBINED_R5_WFP_R4_ATTEMPT3_RESULT.json"),
            "sha256": sha(DEST / "COMBINED_R5_WFP_R4_ATTEMPT3_RESULT.json"),
            "internal_junit_count_bug": "The attempt result reader used root attributes on a testsuites document and recorded zero counters. This appended closure parses the child testsuite and preserves the raw JUnit unchanged.",
        },
        "raw_preserved_files": files,
        "limits": ["software-only; no COMSOL launch, GUI call, ScreenCaptureKit, permission query, or solver call",
                   "Windows native kernel and Intel build remain NOT_RUN",
                   "does not claim physical/numerical acceptance"],
    }
    correction_path = EVIDENCE / "COMBINED_R5_WFP_R4_CLOSURE_R1.json"
    correction_sha = write_once(correction_path, corrected)
    print(json.dumps({"closure": str(correction_path), "closure_sha256": correction_sha,
                      "preserved_files": len(files), "junit": xml_counts,
                      "status": corrected["status"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
