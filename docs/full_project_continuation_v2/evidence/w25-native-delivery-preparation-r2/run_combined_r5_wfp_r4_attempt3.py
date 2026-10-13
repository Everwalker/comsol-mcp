#!/usr/bin/env python3
"""Retry the frozen combined suite with a verified canonical private TMPDIR."""
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
sys.path.insert(0, str(REPO))
from comsol_mcp._host_scoped_provider import verify_private_path_security

CANDIDATE = pathlib.Path("/private/tmp/w25-r5-r3-wfp-v2-r4-candidate-20261012")
EVIDENCE = CANDIDATE / "evidence"
CANDIDATE_RECEIPT = EVIDENCE / "COMBINED_CANDIDATE_R2.json"
INTERPRETER = pathlib.Path("/private/tmp/comsol-v2-runtime-20261011-new/comsol-mcp-recovery-runtime-20261004-001/python312/bin/python")
TMPDIR = pathlib.Path("/private/var/folders/4d/6pphmzkx5szf8dyqx7zhqh1r0000gn/T")
TMPDIR_ID = {"device": 16777231, "inode": 297256}
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
SUFFIX = "COMBINED_R5_WFP_R4_ATTEMPT3"


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
    verify_private_path_security(TMPDIR, directory=True, expected_identity=TMPDIR_ID)
    candidate = json.loads(CANDIDATE_RECEIPT.read_text())
    pre = {row["path"]: sha(CANDIDATE / row["path"]) for row in candidate["source_files"]}
    for row in candidate["source_files"]:
        path = CANDIDATE / row["path"]
        if pre[row["path"]] != row["sha256"] or path.stat().st_size != row["size_bytes"]:
            raise SystemExit(f"private candidate input mismatch: {row['path']}")
    paths = {
        "xml": EVIDENCE / f"{SUFFIX}.junit.xml",
        "stdout": EVIDENCE / f"{SUFFIX}.stdout.log",
        "stderr": EVIDENCE / f"{SUFFIX}.stderr.log",
        "guard": EVIDENCE / f"{SUFFIX}.NETWORK_GUARD.json",
        "result": EVIDENCE / f"{SUFFIX}_RESULT.json",
        "argv": EVIDENCE / f"{SUFFIX}_ARGV.json",
    }
    for path in paths.values():
        if path.exists() or path.is_symlink():
            raise SystemExit(f"no-clobber evidence refusal: {path}")
    argv = [str(INTERPRETER), "-B", "evidence/guarded_pytest.py", *TESTS,
            "-k", "not historical_fixture_commit_and_blob_identity_resolve",
            "-q", "--disable-warnings", "--maxfail=1", "-p", "no:cacheprovider",
            f"--junitxml={paths['xml']}"]
    env = dict(os.environ)
    env.update({"PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(CANDIDATE),
                "TMPDIR": str(TMPDIR), "W25_NETWORK_GUARD_RECEIPT": str(paths["guard"])})
    write_once(paths["argv"], {
        "schema": "comsol-mcp.w25-combined-test-argv/3",
        "runner_sha256": sha(pathlib.Path(__file__)),
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "argv": argv, "cwd": str(CANDIDATE),
        "environment": {"PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(CANDIDATE),
                        "TMPDIR": str(TMPDIR), "TMPDIR_identity": TMPDIR_ID,
                        "W25_NETWORK_GUARD_RECEIPT": str(paths["guard"])},
        "reason_for_environment_correction": "Attempt 1 correctly refused /var/folders alias; use verified /private/var/folders canonical path.",
        "single_historical_git_exclusion": "Windows R4 exact invocation excludes this inherited commit/blob case because the private candidate has no historical Git object database."
    })
    with paths["stdout"].open("xb") as stdout, paths["stderr"].open("xb") as stderr:
        proc = subprocess.run(argv, cwd=CANDIDATE, env=env, stdout=stdout, stderr=stderr, check=False)
        stdout.flush(); stderr.flush()
        os.fsync(stdout.fileno()); os.fsync(stderr.fileno())
    post = {row["path"]: sha(CANDIDATE / row["path"]) for row in candidate["source_files"]}
    identical = pre == post
    counts = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    if paths["xml"].is_file():
        root = ET.parse(paths["xml"]).getroot()
        for key in counts:
            counts[key] = int(root.attrib.get(key, 0))
    guard = json.loads(paths["guard"].read_text()) if paths["guard"].is_file() else None
    status = "PASS_SCOPED_SOFTWARE" if proc.returncode == 0 and identical and guard and guard.get("status") == "PASS" else "FAIL_OR_UNBOUND"
    result = {
        "schema": "comsol-mcp.w25-r5-wfp-r4-combined-test-result/3",
        "runner": {"path": str(pathlib.Path(__file__)), "sha256": sha(pathlib.Path(__file__))},
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "candidate_receipt": {"path": str(CANDIDATE_RECEIPT), "sha256": sha(CANDIDATE_RECEIPT)},
        "candidate_tree_sha256": candidate["candidate_tree_sha256"],
        "r5_freeze": candidate["r5_freeze"],
        "wfp_r4_freeze": candidate["wfp_r4_freeze"],
        "argv": {"path": str(paths["argv"]), "sha256": sha(paths["argv"]), "value": argv},
        "exit_code": proc.returncode,
        "junit": {"path": str(paths["xml"]), "sha256": sha(paths["xml"]) if paths["xml"].is_file() else None, **counts},
        "stdout": {"path": str(paths["stdout"]), "sha256": sha(paths["stdout"]), "bytes": paths["stdout"].stat().st_size},
        "stderr": {"path": str(paths["stderr"]), "sha256": sha(paths["stderr"]), "bytes": paths["stderr"].stat().st_size},
        "network_guard": {"path": str(paths["guard"]), "sha256": sha(paths["guard"]) if paths["guard"].is_file() else None,
                          "status": guard.get("status") if guard else "MISSING",
                          "attempts": guard.get("attempts") if guard else None},
        "source_input_hashes_before": pre,
        "source_input_hashes_after": post,
        "source_inputs_identical": identical,
        "limits": ["software-only private fixtures", "no COMSOL or GUI calls",
                   "no ScreenCaptureKit, permission queries, or solver calls",
                   "does not establish Windows kernel/native acceptance"],
    }
    write_once(paths["result"], result)
    print(json.dumps({"status": status, "exit_code": proc.returncode, "junit": counts,
                      "source_inputs_identical": identical,
                      "network_guard": result["network_guard"]["status"],
                      "result_path": str(paths["result"]), "result_sha256": sha(paths["result"])},
                     sort_keys=True))
    return 0 if status == "PASS_SCOPED_SOFTWARE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
