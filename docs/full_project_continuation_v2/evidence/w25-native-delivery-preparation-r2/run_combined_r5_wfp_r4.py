#!/usr/bin/env python3
"""Run exact private R5 + WFP R4 regressions with durable logs and hashes."""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

REPO = pathlib.Path("/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2/repository")
EVIDENCE = REPO / "docs/full_project_continuation_v2/evidence/w25-native-delivery-preparation-r2"
CANDIDATE = pathlib.Path("/private/tmp/w25-r5-r3-wfp-v2-r4-candidate-20261012")
CANDIDATE_RECEIPT = CANDIDATE / "evidence/COMBINED_CANDIDATE_R2.json"
INTERPRETER = pathlib.Path("/private/tmp/comsol-v2-runtime-20261011-new/comsol-mcp-recovery-runtime-20261004-001/python312/bin/python")
TESTS = [
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
]


def sha(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_once(path: pathlib.Path, value: dict) -> None:
    raw = (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        view = memoryview(raw)
        while view:
            count = os.write(fd, view)
            if count <= 0:
                raise OSError("short receipt write")
            view = view[count:]
        os.fsync(fd)
    finally:
        os.close(fd)


def main() -> int:
    candidate = json.loads(CANDIDATE_RECEIPT.read_text())
    pre = {row["path"]: sha(CANDIDATE / row["path"]) for row in candidate["source_files"]}
    for row in candidate["source_files"]:
        path = CANDIDATE / row["path"]
        if pre[row["path"]] != row["sha256"] or path.stat().st_size != row["size_bytes"]:
            raise SystemExit(f"candidate input differs from freeze: {row['path']}")
    for name in TESTS:
        if not (CANDIDATE / name).is_file():
            raise SystemExit(f"test input missing from private candidate: {name}")

    xml_path = CANDIDATE / "evidence/COMBINED_R5_WFP_R4.junit.xml"
    out_path = CANDIDATE / "evidence/COMBINED_R5_WFP_R4.stdout.log"
    err_path = CANDIDATE / "evidence/COMBINED_R5_WFP_R4.stderr.log"
    guard_path = CANDIDATE / "evidence/COMBINED_R5_WFP_R4_NETWORK_GUARD.json"
    result_path = CANDIDATE / "evidence/COMBINED_R5_WFP_R4_RESULT.json"
    argv_path = CANDIDATE / "evidence/COMBINED_R5_WFP_R4_ARGV.json"
    for path in (xml_path, out_path, err_path, guard_path, result_path, argv_path):
        if path.exists() or path.is_symlink():
            raise SystemExit(f"no-clobber evidence destination exists: {path}")
    argv = [str(INTERPRETER), "-B", "evidence/guarded_pytest.py", *TESTS,
            "-q", "--disable-warnings", "--maxfail=1", "-p", "no:cacheprovider",
            f"--junitxml={xml_path}"]
    write_once(argv_path, {"schema": "comsol-mcp.w25-combined-test-argv/1",
                           "created_utc": datetime.now(timezone.utc).isoformat(),
                           "argv": argv, "cwd": str(CANDIDATE),
                           "environment": {"PYTHONDONTWRITEBYTECODE": "1",
                                           "PYTHONPATH": str(CANDIDATE),
                                           "W25_NETWORK_GUARD_RECEIPT": str(guard_path)}})
    env = dict(os.environ)
    env.update({"PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONPATH": str(CANDIDATE),
                "W25_NETWORK_GUARD_RECEIPT": str(guard_path)})
    with out_path.open("xb") as stdout, err_path.open("xb") as stderr:
        proc = subprocess.run(argv, cwd=CANDIDATE, env=env,
                              stdout=stdout, stderr=stderr, check=False)
        stdout.flush()
        stderr.flush()
        os.fsync(stdout.fileno())
        os.fsync(stderr.fileno())

    post = {row["path"]: sha(CANDIDATE / row["path"]) for row in candidate["source_files"]}
    stable = pre == post
    suite_counts = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    if xml_path.is_file():
        root = ET.parse(xml_path).getroot()
        suite_counts["tests"] = int(root.attrib.get("tests", 0))
        suite_counts["failures"] = int(root.attrib.get("failures", 0))
        suite_counts["errors"] = int(root.attrib.get("errors", 0))
        suite_counts["skipped"] = int(root.attrib.get("skipped", 0))
    guard = json.loads(guard_path.read_text()) if guard_path.is_file() else None
    status = "PASS_SCOPED_SOFTWARE" if proc.returncode == 0 and stable and guard and guard.get("status") == "PASS" else "FAIL_OR_UNBOUND"
    result = {
        "schema": "comsol-mcp.w25-r5-wfp-r4-combined-test-result/1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "candidate_receipt": {"path": str(CANDIDATE_RECEIPT), "sha256": sha(CANDIDATE_RECEIPT)},
        "candidate_tree_sha256": candidate["candidate_tree_sha256"],
        "r5_freeze": candidate["r5_freeze"],
        "wfp_r4_freeze": candidate["wfp_r4_freeze"],
        "argv_file": {"path": str(argv_path), "sha256": sha(argv_path), "argv": argv},
        "exit_code": proc.returncode,
        "junit": {"path": str(xml_path), "sha256": sha(xml_path) if xml_path.is_file() else None,
                  **suite_counts},
        "stdout": {"path": str(out_path), "sha256": sha(out_path), "bytes": out_path.stat().st_size},
        "stderr": {"path": str(err_path), "sha256": sha(err_path), "bytes": err_path.stat().st_size},
        "network_guard": {"path": str(guard_path), "sha256": sha(guard_path) if guard_path.is_file() else None,
                          "status": guard.get("status") if guard else "MISSING",
                          "attempts": guard.get("attempts") if guard else None},
        "source_input_hashes_before": pre,
        "source_input_hashes_after": post,
        "source_inputs_identical": stable,
        "limits": ["software-only fixtures and guarded routes",
                   "no COMSOL launch, GUI call, ScreenCaptureKit call, permission check, or solver call",
                   "does not resolve real Windows kernel/Intel build/native acceptance"],
    }
    write_once(result_path, result)
    print(json.dumps({"status": status, "exit_code": proc.returncode,
                      "junit": suite_counts, "source_inputs_identical": stable,
                      "guard": result["network_guard"]["status"],
                      "result": str(result_path), "result_sha256": sha(result_path)},
                     sort_keys=True))
    return 0 if status == "PASS_SCOPED_SOFTWARE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
